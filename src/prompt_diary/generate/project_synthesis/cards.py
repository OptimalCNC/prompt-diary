"""Read one project's committed evidence cards into typed chains.

Project synthesis consumes the evidence cards produced by extraction. This module reads every
``projects/<project_key>/evidence/<session_ref>.json`` card for the project and returns its
committed chains as typed ``CommittedChain`` values, in session-index order then card order. Both
the prompt paste builder and the ``write_work_item`` API read cards through here, so the
committed-turn universe and the pasted summaries always agree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.source_records import is_claude_tool_result_content, parse_codex_message

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import IndexedSession, PreparedProject, PreparedWorkspace


@dataclass(frozen=True)
class CommittedOutcome:
    """One card outcome reduced to the fields project synthesis pastes."""

    category: str
    summary: str


@dataclass(frozen=True)
class CommittedChain:
    """One committed evidence chain reduced to what project synthesis needs."""

    session_ref: str
    turn_ref: str
    materiality: str
    trigger_summary: str
    reaction_summaries: tuple[str, ...]
    outcomes: tuple[CommittedOutcome, ...]
    observed_check_summaries: tuple[str, ...]
    terminal_type: str
    terminal_summary: str
    messages: tuple[str, ...]
    trigger_at: datetime | None = None
    evidence_end_at: datetime | None = None


@dataclass(frozen=True)
class _TurnTimes:
    """Authoritative timestamps at the indexed boundaries, or unavailable source evidence."""

    trigger_at: datetime | None = None
    evidence_end_at: datetime | None = None


def load_committed_chains(
    workspace_path: Path, project_key: str, *, include_source_times: bool = False
) -> tuple[CommittedChain, ...]:
    """Return committed chains, optionally adding source times for synthesis judgments."""
    workspace = load_prepared_workspace(workspace_path)
    project = _find_project(workspace, project_key)
    if project is None:
        return ()
    chains: list[CommittedChain] = []
    for session in project.sessions:
        card_path = (
            workspace_path / "projects" / project_key / "evidence" / f"{session.session_ref}.json"
        )
        turn_times = (
            _source_turn_times(workspace_path / "projects" / project_key, session)
            if include_source_times
            else {}
        )
        for raw in _card_chains(card_path):
            times = turn_times.get(_as_str(raw.get("turn_ref")), _TurnTimes())
            chains.append(_committed_chain(session.session_ref, raw, times))
    return tuple(chains)


def committed_turn_keys(chains: tuple[CommittedChain, ...]) -> frozenset[tuple[str, str]]:
    """Return the ``(session_ref, turn_ref)`` keys that have a committed chain."""
    return frozenset((chain.session_ref, chain.turn_ref) for chain in chains)


def _find_project(workspace: PreparedWorkspace, project_key: str) -> PreparedProject | None:
    return next((item for item in workspace.projects if item.project_key == project_key), None)


def _card_chains(card_path: Path) -> list[dict[str, Any]]:
    if not card_path.exists():
        return []
    raw: object = json.loads(card_path.read_text(encoding="utf-8"))
    card = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    chains = card.get("evidence_chains")
    rows = cast("list[Any]", chains) if isinstance(chains, list) else []
    return [cast("dict[str, Any]", row) for row in rows if isinstance(row, dict)]


def _committed_chain(session_ref: str, raw: dict[str, Any], times: _TurnTimes) -> CommittedChain:
    trigger = _as_mapping(raw.get("trigger"))
    terminal = _as_mapping(raw.get("terminal_state"))
    return CommittedChain(
        session_ref=session_ref,
        turn_ref=_as_str(raw.get("turn_ref")),
        materiality=_as_str(raw.get("materiality")),
        trigger_summary=_as_str(trigger.get("summary")),
        reaction_summaries=tuple(
            _as_str(_as_mapping(item).get("summary"))
            for item in _as_list(raw.get("agent_reactions"))
        ),
        outcomes=tuple(
            CommittedOutcome(
                category=_as_str(_as_mapping(item).get("category")),
                summary=_as_str(_as_mapping(item).get("summary")),
            )
            for item in _as_list(raw.get("outcomes"))
        ),
        observed_check_summaries=tuple(
            _as_str(_as_mapping(item).get("summary"))
            for item in _as_list(raw.get("observed_checks"))
        ),
        terminal_type=_as_str(terminal.get("type")),
        terminal_summary=_as_str(terminal.get("summary")),
        messages=tuple(
            text
            for item in _as_list(trigger.get("quoted_messages"))
            if (text := _as_str(_as_mapping(item).get("text")))
        ),
        trigger_at=times.trigger_at,
        evidence_end_at=times.evidence_end_at,
    )


def _source_turn_times(project_dir: Path, session: IndexedSession) -> dict[str, _TurnTimes]:
    source_path = project_dir / session.session_path
    if not source_path.is_file() or not session.turns:
        return {}
    boundaries = {line for turn in session.turns for line in (turn.span.start, turn.span.end)}
    last_boundary = max(boundaries)
    records: dict[int, dict[str, Any]] = {}
    with source_path.open(encoding="utf-8", errors="replace") as handle:
        for line, text in enumerate(handle, start=1):
            if line in boundaries:
                records[line] = _source_record(text)
            if line >= last_boundary:
                break
    return {
        turn.turn_ref: _TurnTimes(
            trigger_at=_trigger_timestamp(records.get(turn.span.start, {}), session.source),
            evidence_end_at=_timestamp(records.get(turn.span.end, {}).get("timestamp")),
        )
        for turn in session.turns
    }


def _source_record(text: str) -> dict[str, Any]:
    try:
        return _as_mapping(json.loads(text))
    except json.JSONDecodeError:
        return {}


def _trigger_timestamp(record: dict[str, Any], source: str) -> datetime | None:
    if source == "codex":
        message = parse_codex_message(record)
        return message.timestamp if message is not None and message.role == "user" else None
    if source != "claude-code":
        return None
    message = _as_mapping(record.get("message"))
    if (
        record.get("type") != "user"
        or message.get("role") != "user"
        or record.get("sourceToolAssistantUUID") is not None
        or any(record.get(field) is True for field in ("isSidechain", "isMeta", "isCompactSummary"))
        or is_claude_tool_result_content(message.get("content"))
    ):
        return None
    return _timestamp(record.get("timestamp"))


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp.astimezone(timezone.utc) if timestamp.tzinfo is not None else None


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""
