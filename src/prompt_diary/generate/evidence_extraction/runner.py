"""Evidence extraction phase runner."""

from __future__ import annotations

import json
import time
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
    EvidenceCardCheckpoint,
    inspect_evidence_card,
)
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from prompt_diary.generate.evidence_extraction.model import new_session_card
from prompt_diary.generate.pipeline import TaskResult, evidence_card_artifact
from prompt_diary.generate.prompts import evidence_extractor_prompt
from prompt_diary.progress.events import TurnAdvanced
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskSpec
    from prompt_diary.progress.reporter import ProgressReporter


@dataclass(frozen=True)
class EvidenceExtractionRunner:
    """Drive an agent to extract one evidence chain per indexed turn of a session."""

    agent_factory: AgentSessionFactory
    settings: AgentSettings = field(
        default_factory=lambda: load_agent_settings().evidence_extraction
    )
    retry_policy: AgentRetryPolicy = field(default_factory=AgentRetryPolicy)

    async def run(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> TaskResult:
        """Run one session evidence extraction task."""
        project_key, session_ref = _require_scope(task)
        inputs = build_session_extraction_inputs(
            workspace_path=workspace_path,
            project_key=project_key,
            session_ref=session_ref,
        )
        card_path = workspace_path / evidence_card_artifact(project_key, session_ref).path
        committed: frozenset[str] = frozenset()
        if card_path.exists():
            inspection = inspect_evidence_card(
                workspace_path=workspace_path,
                project_key=project_key,
                session_ref=session_ref,
            )
            if inspection.complete:
                return TaskResult(task_id=task.task_id, status="success")
            if isinstance(inspection, EvidenceCardCheckpoint):
                committed = inspection.committed_turn_refs
            else:
                card_path.unlink()

        if not inputs.turns:
            _write_empty_card(card_path, project_key, session_ref)
            return TaskResult(task_id=task.task_id, status="success")

        total_turns = len(inputs.turns)
        for index, turn in enumerate(inputs.turns):
            if turn.turn_ref in committed:
                continue
            runner = await self.agent_factory.runner(
                AgentConfig(
                    working_directory=workspace_path,
                    model=self.settings.model,
                    approval_mode="auto_review",
                    sandbox="workspace-write",
                    reasoning_effort=self.settings.reasoning_effort,
                )
            )
            prompt = evidence_extractor_prompt(
                project_key=inputs.project_key,
                project_json=inputs.project_json,
                session_ref=inputs.session_ref,
                session_index_record=inputs.session_index_record,
                target_turn=turn.target_turn_json,
            )
            retry = await run_agent_turn_with_resume(
                runner=runner,
                initial_prompt=prompt,
                resume_prompt=lambda turn=turn: _resume_prompt_for_turn(
                    session_ref=session_ref,
                    turn_ref=turn.turn_ref,
                ),
                inspect_artifacts=lambda turn=turn: _turn_artifact_status(
                    workspace_path, project_key, session_ref, turn.turn_ref
                ),
                progress_made=lambda before, after: after and not before,
                action=f"while extracting session {session_ref} turn {turn.turn_ref}",
                retry_policy=self.retry_policy,
            )
            if not retry.ok:
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    errors=retry.errors,
                )
            reporter.emit(
                TurnAdvanced(
                    at=time.monotonic(),
                    task_id=task.task_id,
                    turn_index=index + 1,
                    total_turns=total_turns,
                    turn_ref=turn.turn_ref,
                )
            )
        completed = inspect_evidence_card(
            workspace_path=workspace_path, project_key=project_key, session_ref=session_ref
        )
        return TaskResult(
            task_id=task.task_id,
            status="success" if completed.complete else "failed",
            errors=completed.errors,
        )


def _require_scope(task: TaskSpec) -> tuple[str, str]:
    if task.project_key is None or task.session_ref is None:
        raise PromptDiaryError(_missing_scope_message(task.task_id))
    return task.project_key, task.session_ref


def _resume_prompt_for_turn(*, session_ref: str, turn_ref: str) -> str:
    return (
        "Continue this assigned evidence extraction turn. "
        f"The evidence card still does not show a committed chain for session {session_ref} "
        f"turn {turn_ref}. Reuse the assignment and source context already in this conversation; "
        "extract only this assigned turn and make one successful `write_evidence` commit for it. "
        "Correct any returned validation errors. Work silently, then stop."
    )


def _turn_artifact_status(
    workspace_path: Path, project_key: str, session_ref: str, turn_ref: str
) -> AgentArtifactStatus[bool]:
    inspection = inspect_evidence_card(
        workspace_path=workspace_path, project_key=project_key, session_ref=session_ref
    )
    committed = (
        isinstance(inspection, EvidenceCardCheckpoint)
        and turn_ref in inspection.committed_turn_refs
    )
    return AgentArtifactStatus(complete=committed, progress_marker=committed)


def _write_empty_card(card_path: Path, project_key: str, session_ref: str) -> None:
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        json.dumps(new_session_card(project_key, session_ref), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _missing_scope_message(task_id: str) -> str:
    return f"evidence extraction task {task_id} requires project_key and session_ref"
