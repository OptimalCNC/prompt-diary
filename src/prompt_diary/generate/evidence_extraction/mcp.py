"""Transport-independent evidence extraction MCP tool APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from prompt_diary.generate.evidence_extraction.model import (
    CitationSpan,
    EvidenceChain,
    EvidenceWriteError,
    InvalidEvidenceChain,
    Outcome,
    evidence_chain_to_json,
    new_session_card,
    parse_evidence_chain,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from prompt_diary.generate.workspace import IndexedTurn, LineSpan, PreparedWorkspace

__all__ = [
    "EvidenceWriteError",
    "WriteEvidenceAppendedResult",
    "WriteEvidenceInvalidResult",
    "WriteEvidenceResult",
    "write_evidence",
]


@dataclass(frozen=True)
class WriteEvidenceAppendedResult:
    """Successful evidence-chain write result."""

    status: Literal["appended"]
    project_key: str
    session_ref: str
    turn_ref: str


@dataclass(frozen=True)
class WriteEvidenceInvalidResult:
    """Rejected evidence-chain write result."""

    status: Literal["invalid"]
    errors: tuple[EvidenceWriteError, ...]


WriteEvidenceResult: TypeAlias = WriteEvidenceAppendedResult | WriteEvidenceInvalidResult


@dataclass(frozen=True)
class _ResolvedTurn:
    """A submitted chain resolved against the prepared workspace and session index."""

    turn_span: LineSpan
    card_path: Path
    existing_card: dict[str, Any] | None


def write_evidence(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
    evidence_chain: dict[str, Any],
) -> WriteEvidenceResult:
    """Validate and append one evidence chain to a session evidence card.

    Chain-only structure is parsed first; workspace-dependent checks (project, session, turn,
    duplicate, and citation containment) run against the prepared workspace. A rejected write
    returns structured errors and never touches the canonical card file.
    """
    parsed = parse_evidence_chain(evidence_chain)
    if isinstance(parsed, InvalidEvidenceChain):
        return WriteEvidenceInvalidResult("invalid", parsed.errors)
    chain = parsed.chain

    resolved = _resolve_turn(
        workspace_path=workspace_path,
        project_key=project_key,
        session_ref=session_ref,
        turn_ref=chain.turn_ref,
    )
    if isinstance(resolved, WriteEvidenceInvalidResult):
        return resolved

    chain_errors = _check_chain_against_turn(chain, resolved.turn_span)
    if chain_errors:
        return WriteEvidenceInvalidResult("invalid", tuple(chain_errors))

    _append_chain_to_card(
        card_path=resolved.card_path,
        project_key=project_key,
        session_ref=session_ref,
        chain=chain,
        existing_card=resolved.existing_card,
    )
    return WriteEvidenceAppendedResult("appended", project_key, session_ref, chain.turn_ref)


def _resolve_turn(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
    turn_ref: str,
) -> _ResolvedTurn | WriteEvidenceInvalidResult:
    workspace = load_prepared_workspace(workspace_path)
    turn = _find_turn(workspace, project_key, session_ref, turn_ref)
    if isinstance(turn, WriteEvidenceInvalidResult):
        return turn

    card_path = workspace_path / "projects" / project_key / "evidence" / f"{session_ref}.json"
    existing_card = _read_card(card_path)
    if _has_committed_turn(existing_card, turn_ref):
        return _invalid(
            "evidence_chain.turn_ref",
            _duplicate_turn_message(turn_ref),
            _DUPLICATE_HINT,
        )
    return _ResolvedTurn(turn_span=turn.span, card_path=card_path, existing_card=existing_card)


def _find_turn(
    workspace: PreparedWorkspace,
    project_key: str,
    session_ref: str,
    turn_ref: str,
) -> IndexedTurn | WriteEvidenceInvalidResult:
    project = next((item for item in workspace.projects if item.project_key == project_key), None)
    if project is None:
        return _invalid("project_key", _unknown_project_message(project_key), _UNKNOWN_PROJECT_HINT)
    session = next((item for item in project.sessions if item.session_ref == session_ref), None)
    if session is None:
        return _invalid(
            "session_ref", _unknown_session_message(session_ref, project_key), _UNKNOWN_SESSION_HINT
        )
    turn = next((item for item in session.turns if item.turn_ref == turn_ref), None)
    if turn is None:
        return _invalid(
            "evidence_chain.turn_ref", _unknown_turn_message(turn_ref), _UNKNOWN_TURN_HINT
        )
    return turn


def _check_chain_against_turn(
    chain: EvidenceChain,
    turn_span: LineSpan,
) -> list[EvidenceWriteError]:
    errors: list[EvidenceWriteError] = []
    _check_citation_containment(chain, turn_span, errors)
    _check_material_outcomes_cite_reaction(chain, errors)
    return errors


def _check_citation_containment(
    chain: EvidenceChain,
    turn_span: LineSpan,
    errors: list[EvidenceWriteError],
) -> None:
    for path, span in _iter_citations(chain):
        if span.start < turn_span.start or span.end > turn_span.end:
            errors.append(
                EvidenceWriteError(
                    path,
                    _outside_turn_message(span, chain.turn_ref, turn_span),
                    _OUTSIDE_TURN_HINT,
                )
            )


def _check_material_outcomes_cite_reaction(
    chain: EvidenceChain,
    errors: list[EvidenceWriteError],
) -> None:
    trigger_lines = _span_lines(chain.trigger.citations)
    reaction_lines = _reaction_lines(chain)
    for index, outcome in enumerate(chain.outcomes):
        if _cites_only_trigger(outcome, trigger_lines, reaction_lines):
            errors.append(
                EvidenceWriteError(
                    f"evidence_chain.outcomes[{index}].citations[0].lines",
                    _ONLY_TRIGGER_MESSAGE,
                    _ONLY_TRIGGER_HINT,
                )
            )


def _cites_only_trigger(
    outcome: Outcome,
    trigger_lines: frozenset[int],
    reaction_lines: frozenset[int],
) -> bool:
    outcome_lines = _span_lines(outcome.citations)
    return (
        bool(outcome_lines)
        and outcome_lines <= trigger_lines
        and not (outcome_lines & reaction_lines)
    )


def _iter_citations(chain: EvidenceChain) -> Iterator[tuple[str, CitationSpan]]:
    prefix = "evidence_chain"
    yield from _enumerate_citations(chain.trigger.citations, f"{prefix}.trigger")
    for message_index, message in enumerate(chain.trigger.quoted_messages):
        yield from _enumerate_citations(
            message.citations, f"{prefix}.trigger.quoted_messages[{message_index}]"
        )
    for reaction_index, reaction in enumerate(chain.agent_reactions):
        yield from _enumerate_citations(
            reaction.citations, f"{prefix}.agent_reactions[{reaction_index}]"
        )
    for outcome_index, outcome in enumerate(chain.outcomes):
        yield from _enumerate_citations(outcome.citations, f"{prefix}.outcomes[{outcome_index}]")
    for check_index, check in enumerate(chain.observed_checks):
        yield from _enumerate_citations(check.citations, f"{prefix}.observed_checks[{check_index}]")
    yield from _enumerate_citations(chain.terminal_state.citations, f"{prefix}.terminal_state")


def _enumerate_citations(
    citations: tuple[CitationSpan, ...],
    path: str,
) -> Iterator[tuple[str, CitationSpan]]:
    for index, span in enumerate(citations):
        yield f"{path}.citations[{index}].lines", span


def _reaction_lines(chain: EvidenceChain) -> frozenset[int]:
    lines: set[int] = set()
    for reaction in chain.agent_reactions:
        lines |= _span_lines(reaction.citations)
    return frozenset(lines)


def _span_lines(citations: tuple[CitationSpan, ...]) -> frozenset[int]:
    lines: set[int] = set()
    for span in citations:
        lines.update(range(span.start, span.end + 1))
    return frozenset(lines)


def _append_chain_to_card(
    *,
    card_path: Path,
    project_key: str,
    session_ref: str,
    chain: EvidenceChain,
    existing_card: dict[str, Any] | None,
) -> None:
    card = existing_card if existing_card is not None else _new_card(project_key, session_ref)
    chains = [*_existing_chains(card), evidence_chain_to_json(chain)]
    _write_card(card_path, {**card, "evidence_chains": chains})


def _new_card(project_key: str, session_ref: str) -> dict[str, Any]:
    return new_session_card(project_key, session_ref)


def _read_card(card_path: Path) -> dict[str, Any] | None:
    if not card_path.exists():
        return None
    raw: object = json.loads(card_path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _has_committed_turn(card: dict[str, Any] | None, turn_ref: str) -> bool:
    if card is None:
        return False
    return any(chain.get("turn_ref") == turn_ref for chain in _existing_chains(card))


def _existing_chains(card: dict[str, Any]) -> list[Any]:
    chains = card.get("evidence_chains")
    return cast("list[Any]", chains) if isinstance(chains, list) else []


def _write_card(card_path: Path, card: dict[str, Any]) -> None:
    card_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = card_path.with_name(card_path.name + ".tmp")
    tmp_path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(card_path)


def _invalid(path: str, message: str, hint: str) -> WriteEvidenceInvalidResult:
    return WriteEvidenceInvalidResult("invalid", (EvidenceWriteError(path, message, hint),))


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r}"


def _unknown_session_message(session_ref: str, project_key: str) -> str:
    return f"unknown session_ref {session_ref!r} for project {project_key!r}"


def _unknown_turn_message(turn_ref: str) -> str:
    return f"unknown turn_ref {turn_ref!r} in the session index"


def _duplicate_turn_message(turn_ref: str) -> str:
    return f"turn_ref {turn_ref!r} already has a committed evidence chain (duplicate)"


def _outside_turn_message(span: CitationSpan, turn_ref: str, turn_span: LineSpan) -> str:
    return (
        f"line span {span.start}-{span.end} is outside turn {turn_ref} span "
        f"{turn_span.start}-{turn_span.end}"
    )


_UNKNOWN_PROJECT_HINT = "use the project_key from the prepared workspace"
_UNKNOWN_SESSION_HINT = "use a session_ref listed in sessions.index.jsonl"
_UNKNOWN_TURN_HINT = "use the assigned turn_ref from the target turn"
_DUPLICATE_HINT = "each turn may have only one evidence chain"
_OUTSIDE_TURN_HINT = "cite only lines inside the indexed turn"
_ONLY_TRIGGER_MESSAGE = "a material outcome cites only trigger evidence"
_ONLY_TRIGGER_HINT = "cite agent reaction evidence, not only the trigger lines"
