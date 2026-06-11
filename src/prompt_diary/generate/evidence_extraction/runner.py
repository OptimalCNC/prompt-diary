"""Evidence extraction phase runner."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentConfig
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.agent_retry import (
    AgentArtifactStatus,
    AgentRetryPolicy,
    run_agent_turn_with_resume,
)
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from prompt_diary.generate.evidence_extraction.model import new_session_card
from prompt_diary.generate.pipeline import TaskResult, evidence_card_artifact
from prompt_diary.generate.prompts import (
    evidence_extractor_next_turn_prompt,
    evidence_extractor_prompt,
)
from prompt_diary.progress.events import TurnAdvanced
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.evidence_extraction.inputs import (
        ExtractionTurn,
        SessionExtractionInputs,
    )
    from prompt_diary.generate.pipeline import TaskSpec
    from prompt_diary.progress.reporter import ProgressReporter


DEFAULT_EVIDENCE_REASONING_EFFORT = "low"
"""Per-thread Codex reasoning effort for evidence extraction.

Extraction is reconstruct-the-turn-and-cite-exact-lines work, not deep problem solving, so the
extraction thread pins a low effort instead of inheriting the user's global Codex setting
(which is often much higher). It is a per-thread (``AgentConfig``) value, so other generation
phases keep their own effort; override it by constructing the runner with ``reasoning_effort``.
"""


@dataclass(frozen=True)
class EvidenceExtractionRunner:
    """Drive an agent to extract one evidence chain per indexed turn of a session."""

    agent_factory: AgentSessionFactory
    reasoning_effort: str | None = DEFAULT_EVIDENCE_REASONING_EFFORT
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
        if card_path.exists():
            card_path.unlink()

        if not inputs.turns:
            _write_empty_card(card_path, project_key, session_ref)
            return TaskResult(task_id=task.task_id, status="success")

        runner = await self.agent_factory.runner(
            AgentConfig(
                working_directory=workspace_path,
                approval_mode="auto_review",
                sandbox="workspace-write",
                reasoning_effort=self.reasoning_effort,
            )
        )
        total_turns = len(inputs.turns)
        previous_result_json: str | None = None
        for index, turn in enumerate(inputs.turns):
            prompt = _prompt_for_turn(inputs, turn, index, previous_result_json)
            retry = await run_agent_turn_with_resume(
                runner=runner,
                initial_prompt=prompt,
                resume_prompt=lambda prompt=prompt, turn=turn: _resume_prompt_for_turn(
                    prompt=prompt,
                    session_ref=session_ref,
                    turn_ref=turn.turn_ref,
                ),
                inspect_artifacts=lambda turn=turn: _turn_artifact_status(card_path, turn.turn_ref),
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
            previous_result_json = _committed_result_json(project_key, session_ref, turn.turn_ref)
        return TaskResult(task_id=task.task_id, status="success")


def _require_scope(task: TaskSpec) -> tuple[str, str]:
    if task.project_key is None or task.session_ref is None:
        raise PromptDiaryError(_missing_scope_message(task.task_id))
    return task.project_key, task.session_ref


def _prompt_for_turn(
    inputs: SessionExtractionInputs,
    turn: ExtractionTurn,
    index: int,
    previous_result_json: str | None,
) -> str:
    if index == 0 or previous_result_json is None:
        return evidence_extractor_prompt(
            project_key=inputs.project_key,
            project_json=inputs.project_json,
            session_ref=inputs.session_ref,
            session_index_record=inputs.session_index_record,
            target_turn=turn.target_turn_json,
        )
    return evidence_extractor_next_turn_prompt(
        write_evidence_result=previous_result_json,
        target_turn=turn.target_turn_json,
    )


def _resume_prompt_for_turn(*, prompt: str, session_ref: str, turn_ref: str) -> str:
    return (
        "Continue this assigned evidence extraction turn. "
        f"The evidence card still does not show a committed chain for session {session_ref} "
        f"turn {turn_ref}. Reuse the same context below, extract only this assigned turn, "
        "and make one successful `write_evidence` commit for it.\n\n"
        f"{prompt}"
    )


def _turn_artifact_status(card_path: Path, turn_ref: str) -> AgentArtifactStatus[bool]:
    committed = turn_ref in _committed_turn_refs(card_path)
    return AgentArtifactStatus(complete=committed, progress_marker=committed)


def _committed_turn_refs(card_path: Path) -> frozenset[str]:
    if not card_path.exists():
        return frozenset()
    card_obj: object = json.loads(card_path.read_text(encoding="utf-8"))
    card = cast("dict[str, Any]", card_obj) if isinstance(card_obj, dict) else {}
    chains_obj = card.get("evidence_chains")
    chains = cast("list[Any]", chains_obj) if isinstance(chains_obj, list) else []
    committed = (
        cast("dict[str, Any]", row).get("turn_ref") for row in chains if isinstance(row, dict)
    )
    return frozenset(ref for ref in committed if isinstance(ref, str))


def _committed_result_json(project_key: str, session_ref: str, turn_ref: str) -> str:
    return json.dumps(
        {
            "status": "appended",
            "project_key": project_key,
            "session_ref": session_ref,
            "turn_ref": turn_ref,
        },
        indent=2,
        ensure_ascii=False,
    )


def _write_empty_card(card_path: Path, project_key: str, session_ref: str) -> None:
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        json.dumps(new_session_card(project_key, session_ref), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _missing_scope_message(task_id: str) -> str:
    return f"evidence extraction task {task_id} requires project_key and session_ref"
