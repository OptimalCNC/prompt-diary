"""Evidence extraction phase runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentConfig
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from prompt_diary.generate.evidence_extraction.model import new_session_card
from prompt_diary.generate.pipeline import TaskResult, evidence_card_artifact
from prompt_diary.generate.prompts import (
    evidence_extractor_next_turn_prompt,
    evidence_extractor_prompt,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.evidence_extraction.inputs import (
        ExtractionTurn,
        SessionExtractionInputs,
    )
    from prompt_diary.generate.pipeline import TaskSpec


@dataclass(frozen=True)
class EvidenceExtractionRunner:
    """Drive an agent to extract one evidence chain per indexed turn of a session."""

    agent_factory: AgentSessionFactory

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
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

        runner = await self.agent_factory.runner(AgentConfig(working_directory=workspace_path))
        previous_result_json: str | None = None
        for index, turn in enumerate(inputs.turns):
            await runner.turn(_prompt_for_turn(inputs, turn, index, previous_result_json))
            if turn.turn_ref not in _committed_turn_refs(card_path):
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    errors=(_uncommitted_turn_message(session_ref, turn.turn_ref),),
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
            session_path=inputs.session_path,
            session_index_record=inputs.session_index_record,
            target_turn=turn.target_turn_json,
        )
    return evidence_extractor_next_turn_prompt(
        write_evidence_result=previous_result_json,
        target_turn=turn.target_turn_json,
    )


def _committed_turn_refs(card_path: Path) -> frozenset[str]:
    if not card_path.exists():
        return frozenset()
    card = cast("dict[str, Any]", json.loads(card_path.read_text(encoding="utf-8")))
    chains = cast("list[dict[str, Any]]", card["evidence_chains"])
    return frozenset(cast("str", chain["turn_ref"]) for chain in chains)


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


def _uncommitted_turn_message(session_ref: str, turn_ref: str) -> str:
    return (
        f"no evidence chain was committed for session {session_ref} turn {turn_ref}; "
        "the agent did not write a valid chain for the assigned turn"
    )
