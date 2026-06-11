"""Completeness inspection for durable session evidence cards."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.generate.evidence_extraction.mcp import validate_evidence_chain_against_turn
from prompt_diary.generate.evidence_extraction.model import (
    InvalidEvidenceChain,
    parse_evidence_chain,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import IndexedSession, IndexedTurn, PreparedProject


@dataclass(frozen=True)
class EvidenceCardInspection:
    """Result of inspecting one session evidence-card artifact."""

    complete: bool
    errors: tuple[str, ...] = ()


def inspect_evidence_card(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
) -> EvidenceCardInspection:
    """Check whether the session card is complete for the current prepared workspace."""
    workspace = load_prepared_workspace(workspace_path)
    project = _find_project(tuple(workspace.projects), project_key)
    if project is None:
        return _incomplete(_unknown_project_message(project_key))
    session = _find_session(project, session_ref)
    if session is None:
        return _incomplete(_unknown_session_message(project_key, session_ref))
    return inspect_evidence_card_for_session(
        workspace_path=workspace_path,
        project_key=project_key,
        session=session,
    )


def inspect_evidence_card_for_session(
    *,
    workspace_path: Path,
    project_key: str,
    session: IndexedSession,
) -> EvidenceCardInspection:
    """Check whether one prepared session's card exactly covers its indexed turns."""
    card_path = (
        workspace_path / "projects" / project_key / "evidence" / f"{session.session_ref}.json"
    )
    card = _read_card(card_path)
    if card is None:
        return _incomplete(_missing_card_message(card_path))

    errors = _envelope_errors(card, project_key=project_key, session_ref=session.session_ref)
    chains = _as_list(card.get("evidence_chains"))
    turn_by_ref = {turn.turn_ref: turn for turn in session.turns}
    seen: set[str] = set()
    for index, raw_chain in enumerate(chains):
        if not isinstance(raw_chain, dict):
            errors.append(_chain_object_message(index))
            continue
        chain_errors, turn_ref = _chain_errors(cast("dict[str, Any]", raw_chain), turn_by_ref)
        errors.extend(chain_errors)
        if turn_ref is None:
            continue
        if turn_ref in seen:
            errors.append(_duplicate_turn_message(turn_ref))
        seen.add(turn_ref)

    missing = tuple(turn.turn_ref for turn in session.turns if turn.turn_ref not in seen)
    if missing:
        errors.append(_missing_turns_message(missing))

    return EvidenceCardInspection(complete=not errors, errors=tuple(errors))


def _read_card(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _envelope_errors(card: dict[str, Any], *, project_key: str, session_ref: str) -> list[str]:
    errors: list[str] = []
    if card.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if card.get("project_key") != project_key:
        errors.append(_project_mismatch_message(project_key))
    if card.get("session_ref") != session_ref:
        errors.append(_session_mismatch_message(session_ref))
    if not isinstance(card.get("evidence_chains"), list):
        errors.append("evidence_chains must be a list")
    return errors


def _chain_errors(
    raw_chain: dict[str, Any],
    turn_by_ref: dict[str, IndexedTurn],
) -> tuple[list[str], str | None]:
    parsed = parse_evidence_chain(raw_chain)
    raw_turn_ref = raw_chain.get("turn_ref")
    turn_ref = raw_turn_ref if isinstance(raw_turn_ref, str) else None
    if isinstance(parsed, InvalidEvidenceChain):
        return [error.message for error in parsed.errors], turn_ref

    chain = parsed.chain
    turn = turn_by_ref.get(chain.turn_ref)
    if turn is None:
        return [_unknown_turn_message(chain.turn_ref)], chain.turn_ref
    return (
        [error.message for error in validate_evidence_chain_against_turn(chain, turn.span)],
        chain.turn_ref,
    )


def _find_project(
    projects: tuple[PreparedProject, ...], project_key: str
) -> PreparedProject | None:
    return next((item for item in projects if item.project_key == project_key), None)


def _find_session(project: PreparedProject, session_ref: str) -> IndexedSession | None:
    return next((item for item in project.sessions if item.session_ref == session_ref), None)


def _incomplete(error: str) -> EvidenceCardInspection:
    return EvidenceCardInspection(complete=False, errors=(error,))


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"


def _unknown_session_message(project_key: str, session_ref: str) -> str:
    return f"unknown session_ref {session_ref!r} for project {project_key!r}"


def _missing_card_message(path: Path) -> str:
    return f"missing evidence card: {path}"


def _project_mismatch_message(project_key: str) -> str:
    return f"project_key must be {project_key!r}"


def _session_mismatch_message(session_ref: str) -> str:
    return f"session_ref must be {session_ref!r}"


def _chain_object_message(index: int) -> str:
    return f"evidence_chains[{index}] must be a JSON object"


def _unknown_turn_message(turn_ref: str) -> str:
    return f"unknown turn_ref {turn_ref!r} in the current session index"


def _duplicate_turn_message(turn_ref: str) -> str:
    return f"duplicate turn_ref {turn_ref!r}"


def _missing_turns_message(turn_refs: tuple[str, ...]) -> str:
    return "missing turn_ref(s): " + ", ".join(turn_refs)
