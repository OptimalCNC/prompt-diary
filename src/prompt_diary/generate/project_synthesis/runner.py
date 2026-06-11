"""Project synthesis phase runner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentConfig
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.agent_retry import (
    AgentArtifactStatus,
    AgentRetryPolicy,
    run_agent_turn_with_resume,
)
from prompt_diary.generate.pipeline import TaskResult, project_synthesis_artifact
from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from prompt_diary.generate.project_synthesis.inputs import build_project_synthesis_inputs
from prompt_diary.generate.project_synthesis.model import (
    TurnReference,
    new_project_synthesis_envelope,
)
from prompt_diary.generate.prompts import (
    project_synthesizer_next_prompt,
    project_synthesizer_prompt,
)
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskSpec
    from prompt_diary.generate.workspace import PreparedProject
    from prompt_diary.progress.reporter import ProgressReporter


DEFAULT_PROJECT_SYNTHESIS_REASONING_EFFORT = "medium"
"""Per-thread Codex reasoning effort for project synthesis.

Grouping evidence chains into work items needs more judgment than evidence extraction but is not
deep problem solving, so the synthesis thread pins a mid-level effort instead of inheriting the
user's global Codex setting. It is a per-thread (``AgentConfig``) value; override it by
constructing the runner with ``reasoning_effort``.
"""


@dataclass(frozen=True)
class ProjectSynthesisRunner:
    """Drive an agent to group one project's evidence chains into work items."""

    agent_factory: AgentSessionFactory
    reasoning_effort: str | None = DEFAULT_PROJECT_SYNTHESIS_REASONING_EFFORT
    retry_policy: AgentRetryPolicy = field(default_factory=AgentRetryPolicy)

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

        committed = committed_turn_keys(load_committed_chains(workspace_path, project_key))
        runner = await self.agent_factory.runner(
            AgentConfig(
                working_directory=workspace_path,
                approval_mode="auto_review",
                sandbox="workspace-write",
                reasoning_effort=self.reasoning_effort,
            )
        )
        initial_prompt = project_synthesizer_prompt(
            project_key=inputs.project_key,
            project_json=inputs.project_json,
            evidence_chains=inputs.evidence_chains,
        )
        retry = await run_agent_turn_with_resume(
            runner=runner,
            initial_prompt=initial_prompt,
            resume_prompt=lambda: project_synthesizer_next_prompt(
                project_key=project_key,
                uncovered_turns=_render_uncovered(
                    _uncovered_turns(output_path, universe), committed
                ),
            ),
            inspect_artifacts=lambda: _project_artifact_status(output_path, universe),
            progress_made=lambda before, after: after < before,
            action=f"while synthesizing project {project_key}",
            retry_policy=self.retry_policy,
        )
        if not retry.ok:
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=retry.errors,
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


def _project_artifact_status(
    output_path: Path, universe: tuple[TurnReference, ...]
) -> AgentArtifactStatus[int]:
    uncovered_count = len(_uncovered_turns(output_path, universe))
    return AgentArtifactStatus(complete=uncovered_count == 0, progress_marker=uncovered_count)


def _render_uncovered(
    uncovered: tuple[TurnReference, ...], committed: frozenset[tuple[str, str]]
) -> str:
    lines: list[str] = []
    for ref in uncovered:
        has_chain = (ref.session_ref, ref.turn_ref) in committed
        note = "has an evidence chain" if has_chain else "no evidence chain"
        lines.append(f"- `{ref.session_ref}/{ref.turn_ref}` — {note}")
    return "\n".join(lines)


def _covered_keys(output_path: Path) -> frozenset[tuple[str, str]]:
    if not output_path.exists():
        return frozenset()
    raw: object = json.loads(output_path.read_text(encoding="utf-8"))
    envelope = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    items = envelope.get("work_items")
    rows = cast("list[Any]", items) if isinstance(items, list) else []
    dict_rows = [cast("dict[str, Any]", row) for row in rows if isinstance(row, dict)]
    keys: set[tuple[str, str]] = set()
    for row in dict_rows:
        covered = row.get("covered_turns")
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
