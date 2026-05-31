"""Project synthesis phase runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentConfig
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.pipeline import TaskResult, project_synthesis_artifact
from prompt_diary.generate.project_synthesis.inputs import build_project_synthesis_inputs
from prompt_diary.generate.project_synthesis.model import (
    TurnReference,
    new_project_synthesis_envelope,
)
from prompt_diary.generate.prompts import project_synthesizer_prompt
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskSpec
    from prompt_diary.generate.workspace import PreparedProject
    from prompt_diary.progress.reporter import ProgressReporter


@dataclass(frozen=True)
class ProjectSynthesisRunner:
    """Drive an agent to group one project's evidence chains into work items."""

    agent_factory: AgentSessionFactory

    async def run(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> TaskResult:
        """Run one project synthesis task."""
        del reporter
        project_key = _require_scope(task)
        project = _require_project(workspace_path, project_key)
        inputs = build_project_synthesis_inputs(
            workspace_path=workspace_path, project_key=project_key
        )
        output_path = workspace_path / project_synthesis_artifact(project_key).path
        if output_path.exists():
            output_path.unlink()

        universe = _indexed_turn_universe(project)
        if not universe:
            _write_empty_envelope(output_path, project_key, project.project_label)
            return TaskResult(task_id=task.task_id, status="success")

        # The synthesizer self-loops on write_work_item's uncovered_turns within one turn. An
        # all-gap project (zero committed chains) cannot be bootstrapped this way and fails the
        # coverage check below; that degenerate case is out of MVP scope.
        runner = await self.agent_factory.runner(
            AgentConfig(
                working_directory=workspace_path,
                approval_mode="auto_review",
                sandbox="workspace-write",
            )
        )
        await runner.turn(
            project_synthesizer_prompt(
                project_key=inputs.project_key,
                project_json=inputs.project_json,
                evidence_chains=inputs.evidence_chains,
            )
        )
        uncovered = _uncovered_turns(output_path, universe)
        if uncovered:
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=(_uncovered_message(project_key, uncovered),),
            )
        return TaskResult(task_id=task.task_id, status="success")


def _require_scope(task: TaskSpec) -> str:
    if task.project_key is None:
        raise PromptDiaryError(_missing_scope_message(task.task_id))
    return task.project_key


def _require_project(workspace_path: Path, project_key: str) -> PreparedProject:
    workspace = load_prepared_workspace(workspace_path)
    project = next((item for item in workspace.projects if item.project_key == project_key), None)
    if project is None:
        raise PromptDiaryError(_unknown_project_message(project_key))
    return project


def _indexed_turn_universe(project: PreparedProject) -> tuple[TurnReference, ...]:
    return tuple(
        TurnReference(session.session_ref, turn.turn_ref)
        for session in project.sessions
        for turn in session.turns
    )


def _uncovered_turns(
    output_path: Path, universe: tuple[TurnReference, ...]
) -> tuple[TurnReference, ...]:
    covered = _covered_keys(output_path)
    return tuple(ref for ref in universe if (ref.session_ref, ref.turn_ref) not in covered)


def _covered_keys(output_path: Path) -> frozenset[tuple[str, str]]:
    if not output_path.exists():
        return frozenset()
    raw: object = json.loads(output_path.read_text(encoding="utf-8"))
    envelope = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    items = envelope.get("work_items")
    rows = cast("list[Any]", items) if isinstance(items, list) else []
    keys: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        covered = cast("dict[str, Any]", row).get("covered_turns")
        for ref in cast("list[Any]", covered) if isinstance(covered, list) else []:
            if isinstance(ref, dict):
                mapping = cast("dict[str, Any]", ref)
                keys.add((_as_str(mapping.get("session_ref")), _as_str(mapping.get("turn_ref"))))
    return frozenset(keys)


def _write_empty_envelope(output_path: Path, project_key: str, project_label: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            new_project_synthesis_envelope(project_key, project_label),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _missing_scope_message(task_id: str) -> str:
    return f"project synthesis task {task_id} requires project_key"


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"


def _uncovered_message(project_key: str, uncovered: tuple[TurnReference, ...]) -> str:
    listed = ", ".join(f"{ref.session_ref}/{ref.turn_ref}" for ref in uncovered)
    return (
        f"project synthesis for {project_key} left {len(uncovered)} "
        f"indexed turn(s) uncovered: {listed}"
    )
