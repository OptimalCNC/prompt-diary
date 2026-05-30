"""Build evidence extractor prompt inputs for one indexed session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import (
        IndexedSession,
        LineSpan,
        PreparedProject,
        PreparedWorkspace,
    )


@dataclass(frozen=True)
class ExtractionTurn:
    """One assigned turn with its verified span and faithful target-turn JSON."""

    turn_ref: str
    span: LineSpan
    target_turn_json: str


@dataclass(frozen=True)
class SessionExtractionInputs:
    """Rendered-ready inputs for extracting one session's evidence chains."""

    project_key: str
    session_ref: str
    project_json: str
    session_path: str
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

    project_dir = workspace_path / "projects" / project_key
    raw_row = _find_index_row(project_dir / "sessions.index.jsonl", session_ref)
    raw_turns = _raw_turns_by_ref(raw_row)
    record_without_turns = {key: value for key, value in raw_row.items() if key != "turns"}

    turns = tuple(
        ExtractionTurn(
            turn_ref=turn.turn_ref,
            span=turn.span,
            target_turn_json=json.dumps(raw_turns[turn.turn_ref], indent=2, ensure_ascii=False),
        )
        for turn in session.turns
    )
    return SessionExtractionInputs(
        project_key=project_key,
        session_ref=session_ref,
        project_json=_normalized_json(project_dir / "project.json"),
        session_path=f"projects/{project_key}/{session.session_path.as_posix()}",
        session_index_record=json.dumps(record_without_turns, indent=2, ensure_ascii=False),
        turns=turns,
    )


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


def _find_index_row(index_path: Path, session_ref: str) -> dict[str, Any]:
    rows_by_ref: dict[str, dict[str, Any]] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = cast("dict[str, Any]", json.loads(line))
            rows_by_ref[cast("str", row["session_ref"])] = row
    # session_ref was already validated to exist by load_prepared_workspace, which parsed this
    # same index; we re-read only to keep raw row fields the typed model drops (e.g. per-turn
    # target_subagents).
    return rows_by_ref[session_ref]


def _raw_turns_by_ref(raw_row: dict[str, Any]) -> dict[str, Any]:
    turns = raw_row.get("turns")
    rows = cast("list[Any]", turns) if isinstance(turns, list) else []
    return {
        cast("dict[str, Any]", turn)["turn_ref"]: turn for turn in rows if isinstance(turn, dict)
    }


def _normalized_json(path: Path) -> str:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(raw, indent=2, ensure_ascii=False)


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"


def _unknown_session_message(session_ref: str, project_key: str) -> str:
    return f"unknown session_ref {session_ref!r} for project {project_key!r}"
