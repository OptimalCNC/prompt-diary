"""Build evidence extractor prompt inputs for one indexed session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import (
        IndexedSession,
        IndexedTurn,
        LineSpan,
        PreparedProject,
        PreparedWorkspace,
    )


@dataclass(frozen=True)
class ExtractionTurn:
    """One assigned turn with its verified span and target-turn JSON."""

    turn_ref: str
    span: LineSpan
    target_turn_json: str


@dataclass(frozen=True)
class SessionExtractionInputs:
    """Rendered-ready inputs for extracting one session's evidence chains."""

    project_key: str
    session_ref: str
    project_json: str
    session_index_record: str
    turns: tuple[ExtractionTurn, ...]


def build_session_extraction_inputs(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
) -> SessionExtractionInputs:
    """Build prompt inputs for one indexed session from the prepared workspace."""
    workspace = load_prepared_workspace(workspace_path)
    project = _find_project(workspace, project_key)
    session = _find_session(project, session_ref, project_key)

    record_without_turns = {
        "session_ref": session.session_ref,
        "source": session.source,
        "source_session_id": session.source_session_id,
        "session_path": session.session_path.as_posix(),
        "target_start_line": session.target_span.start,
        "target_end_line": session.target_span.end,
    }

    turns = tuple(
        ExtractionTurn(
            turn_ref=turn.turn_ref,
            span=turn.span,
            target_turn_json=_target_turn_json(
                turn, session.turns[index - 1] if index > 0 else None
            ),
        )
        for index, turn in enumerate(session.turns)
    )
    return SessionExtractionInputs(
        project_key=project_key,
        session_ref=session_ref,
        project_json=json.dumps(
            {"project_key": project.project_key, "project_label": project.project_label},
            indent=2,
            ensure_ascii=False,
        ),
        session_index_record=json.dumps(record_without_turns, indent=2, ensure_ascii=False),
        turns=turns,
    )


def _target_turn_json(turn: IndexedTurn, previous: IndexedTurn | None) -> str:
    assignment = _turn_locator(turn)
    if previous is not None:
        assignment["previous_turn"] = _turn_locator(previous)
    return json.dumps(assignment, indent=2, ensure_ascii=False)


def _turn_locator(turn: IndexedTurn) -> dict[str, object]:
    return {
        "turn_ref": turn.turn_ref,
        "turn_start_line": turn.span.start,
        "turn_end_line": turn.span.end,
    }


def _find_project(workspace: PreparedWorkspace, project_key: str) -> PreparedProject:
    project = next((item for item in workspace.projects if item.project_key == project_key), None)
    if project is None:
        raise PromptDiaryError(_unknown_project_message(project_key))
    return project


def _find_session(
    project: PreparedProject,
    session_ref: str,
    project_key: str,
) -> IndexedSession:
    session = next((item for item in project.sessions if item.session_ref == session_ref), None)
    if session is None:
        raise PromptDiaryError(_unknown_session_message(session_ref, project_key))
    return session


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"


def _unknown_session_message(session_ref: str, project_key: str) -> str:
    return f"unknown session_ref {session_ref!r} for project {project_key!r}"
