"""Project synthesis phase runner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompt_diary.agent import AgentConfig
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.agent_retry import (
    AgentArtifactStatus,
    AgentRetryPolicy,
    run_agent_turn_with_resume,
)
from prompt_diary.generate.agent_settings import AgentSettings, load_agent_settings
from prompt_diary.generate.evidence_extraction.completeness import (
    inspect_evidence_card_for_session,
)
from prompt_diary.generate.pipeline import TaskResult, project_synthesis_artifact
from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from prompt_diary.generate.project_synthesis.completeness import (
    ProjectSynthesisCheckpoint,
    inspect_project_synthesis,
)
from prompt_diary.generate.project_synthesis.inputs import build_project_synthesis_inputs
from prompt_diary.generate.project_synthesis.model import (
    TurnReference,
    new_project_synthesis_envelope,
)
from prompt_diary.generate.prompts import (
    project_synthesizer_instructions,
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


@dataclass(frozen=True)
class ProjectSynthesisRunner:
    """Drive an agent to group one project's evidence chains into work items."""

    agent_factory: AgentSessionFactory
    settings: AgentSettings = field(default_factory=lambda: load_agent_settings().project_synthesis)
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
        output_path = workspace_path / project_synthesis_artifact(project_key).path
        evidence_errors = _incomplete_evidence_errors(workspace_path, project)
        if evidence_errors:
            return TaskResult(task_id=task.task_id, status="failed", errors=evidence_errors)

        checkpoint: ProjectSynthesisCheckpoint | None = None
        if output_path.exists():
            inspection = inspect_project_synthesis(
                workspace_path=workspace_path, project_key=project_key
            )
            if inspection.complete:
                return TaskResult(task_id=task.task_id, status="success")
            if isinstance(inspection, ProjectSynthesisCheckpoint):
                checkpoint = inspection
            else:
                output_path.unlink()

        inputs = build_project_synthesis_inputs(
            workspace_path=workspace_path,
            project_key=project_key,
            covered_turns=checkpoint.covered_keys if checkpoint is not None else frozenset(),
        )

        universe = _indexed_turn_universe(project)
        if not universe:
            _write_empty_envelope(output_path, project_key, project.project_label)
            return TaskResult(task_id=task.task_id, status="success")

        committed = committed_turn_keys(load_committed_chains(workspace_path, project_key))
        runner = await self.agent_factory.runner(
            AgentConfig(
                working_directory=workspace_path,
                model=self.settings.model,
                approval_mode="auto_review",
                sandbox="workspace-write",
                reasoning_effort=self.settings.reasoning_effort,
                base_instructions=project_synthesizer_instructions(),
                mcp_tools=("write_work_item",),
            )
        )
        initial_prompt = project_synthesizer_prompt(
            project_key=inputs.project_key,
            project_json=inputs.project_json,
            evidence_chains=inputs.evidence_chains,
            committed_work_items=_render_checkpoint(checkpoint),
        )
        retry = await run_agent_turn_with_resume(
            runner=runner,
            initial_prompt=initial_prompt,
            resume_prompt=lambda: project_synthesizer_next_prompt(
                project_key=project_key,
                uncovered_turns=_render_uncovered(
                    _uncovered_turns(workspace_path, project_key, universe), committed
                ),
            ),
            inspect_artifacts=lambda: _project_artifact_status(
                workspace_path=workspace_path,
                project_key=project_key,
                universe=universe,
            ),
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


def _incomplete_evidence_errors(workspace_path: Path, project: PreparedProject) -> tuple[str, ...]:
    errors: list[str] = []
    for session in project.sessions:
        inspection = inspect_evidence_card_for_session(
            workspace_path=workspace_path,
            project_key=project.project_key,
            session=session,
        )
        if not inspection.complete:
            details = "; ".join(inspection.errors)
            errors.append(_incomplete_evidence_message(session.session_ref, details))
    return tuple(errors)


def _uncovered_turns(
    workspace_path: Path, project_key: str, universe: tuple[TurnReference, ...]
) -> tuple[TurnReference, ...]:
    inspection = inspect_project_synthesis(workspace_path=workspace_path, project_key=project_key)
    return (
        inspection.uncovered_turns
        if isinstance(inspection, ProjectSynthesisCheckpoint)
        else universe
    )


def _project_artifact_status(
    *,
    workspace_path: Path,
    project_key: str,
    universe: tuple[TurnReference, ...],
) -> AgentArtifactStatus[int]:
    inspection = inspect_project_synthesis(workspace_path=workspace_path, project_key=project_key)
    uncovered_count = (
        len(inspection.uncovered_turns)
        if isinstance(inspection, ProjectSynthesisCheckpoint)
        else len(universe)
    )
    return AgentArtifactStatus(
        complete=inspection.complete,
        progress_marker=uncovered_count,
    )


def _render_uncovered(
    uncovered: tuple[TurnReference, ...], committed: frozenset[tuple[str, str]]
) -> str:
    lines: list[str] = []
    for ref in uncovered:
        has_chain = (ref.session_ref, ref.turn_ref) in committed
        note = "has an evidence chain" if has_chain else "no evidence chain"
        lines.append(f"- `{ref.session_ref}/{ref.turn_ref}` — {note}")
    return "\n".join(lines)


def _render_checkpoint(checkpoint: ProjectSynthesisCheckpoint | None) -> str:
    if checkpoint is None or not checkpoint.work_items:
        return ""
    return "\n".join(
        f"- {item.work_item_ref}: "
        + ", ".join(f"{ref.session_ref}/{ref.turn_ref}" for ref in item.covered_turns)
        for item in checkpoint.work_items
    )


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


def _missing_scope_message(task_id: str) -> str:
    return f"project synthesis task {task_id} requires project_key"


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"


def _incomplete_evidence_message(session_ref: str, details: str) -> str:
    return f"incomplete prerequisite evidence card for {session_ref}: {details}"
