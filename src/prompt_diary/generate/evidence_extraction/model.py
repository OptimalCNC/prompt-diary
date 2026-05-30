"""Typed evidence-chain model and self-contained parsing for evidence extraction.

This module owns the evidence data model and the chain-only validation that depends on nothing but
the submitted chain. Parsing an untrusted chain dict either yields a fully typed ``EvidenceChain``
whose values are guaranteed to be well formed, or a structured list of ``EvidenceWriteError``.
Cross-artifact checks that need the prepared workspace live in the evidence extraction MCP API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias, cast

from prompt_diary.generate.prompts import (
    EVIDENCE_CHECK_TYPES,
    EVIDENCE_MATERIALITY_VALUES,
    EVIDENCE_OUTCOME_CATEGORIES,
    EVIDENCE_TERMINAL_STATES,
    EVIDENCE_TRIGGER_TYPES,
)


@dataclass(frozen=True)
class EvidenceWriteError:
    """Structured validation error returned by rejected evidence writes."""

    path: str
    message: str
    hint: str


@dataclass(frozen=True)
class CitationSpan:
    """Inclusive 1-based citation line span inside a copied session file."""

    start: int
    end: int


@dataclass(frozen=True)
class QuotedMessage:
    """One preserved user-authored message inside a trigger."""

    text: str
    citations: tuple[CitationSpan, ...]


@dataclass(frozen=True)
class Trigger:
    """What user message or user-managed context drove the agent reaction."""

    type: str
    summary: str
    quoted_messages: tuple[QuotedMessage, ...]
    citations: tuple[CitationSpan, ...]


@dataclass(frozen=True)
class AgentReaction:
    """What the agent actually did in response to the trigger."""

    summary: str
    citations: tuple[CitationSpan, ...]


@dataclass(frozen=True)
class Outcome:
    """One evidence-backed result the agent reaction produced."""

    category: str
    summary: str
    citations: tuple[CitationSpan, ...]


@dataclass(frozen=True)
class ObservedCheck:
    """One visible check or feedback recorded from the transcript."""

    type: str
    summary: str
    citations: tuple[CitationSpan, ...]


@dataclass(frozen=True)
class TerminalState:
    """How the turn-centered chain ended."""

    type: str
    summary: str
    citations: tuple[CitationSpan, ...]


@dataclass(frozen=True)
class EvidenceChain:
    """One indexed turn parsed into a fully typed, well-formed evidence chain."""

    turn_ref: str
    trigger: Trigger
    agent_reactions: tuple[AgentReaction, ...]
    outcomes: tuple[Outcome, ...]
    observed_checks: tuple[ObservedCheck, ...]
    terminal_state: TerminalState
    materiality: str


@dataclass(frozen=True)
class ParsedEvidenceChain:
    """A successful parse holding a guaranteed well-formed evidence chain."""

    chain: EvidenceChain


@dataclass(frozen=True)
class InvalidEvidenceChain:
    """A rejected parse holding the structural errors found in the chain."""

    errors: tuple[EvidenceWriteError, ...]


EvidenceChainParseResult: TypeAlias = ParsedEvidenceChain | InvalidEvidenceChain


_TRIGGER_TYPES = frozenset(item.value for item in EVIDENCE_TRIGGER_TYPES)
_OUTCOME_CATEGORIES = frozenset(item.value for item in EVIDENCE_OUTCOME_CATEGORIES)
_CHECK_TYPES = frozenset(item.value for item in EVIDENCE_CHECK_TYPES)
_TERMINAL_STATES = frozenset(item.value for item in EVIDENCE_TERMINAL_STATES)
_MATERIALITY_VALUES = frozenset(item.value for item in EVIDENCE_MATERIALITY_VALUES)

_MATERIAL_RESULT = "material_result"


def parse_evidence_chain(raw: dict[str, Any]) -> EvidenceChainParseResult:
    """Parse an untrusted chain dict into a typed chain or structured errors."""
    errors: list[EvidenceWriteError] = []
    chain = _parse_chain(raw, errors)
    if errors:
        return InvalidEvidenceChain(tuple(errors))
    return ParsedEvidenceChain(chain)


def evidence_chain_to_json(chain: EvidenceChain) -> dict[str, Any]:
    """Serialize a typed evidence chain into the canonical card chain shape."""
    return {
        "turn_ref": chain.turn_ref,
        "trigger": _trigger_to_json(chain.trigger),
        "agent_reactions": [_reaction_to_json(reaction) for reaction in chain.agent_reactions],
        "outcomes": [_outcome_to_json(outcome) for outcome in chain.outcomes],
        "observed_checks": [_check_to_json(check) for check in chain.observed_checks],
        "terminal_state": _terminal_state_to_json(chain.terminal_state),
        "materiality": chain.materiality,
    }


def _parse_chain(raw: dict[str, Any], errors: list[EvidenceWriteError]) -> EvidenceChain:
    prefix = "evidence_chain"
    outcomes = tuple(
        _parse_outcome(item, errors, path=f"{prefix}.outcomes[{index}]")
        for index, item in enumerate(_as_list(raw.get("outcomes")))
    )
    terminal_state = _parse_terminal_state(
        _as_mapping(raw.get("terminal_state")),
        errors,
        path=f"{prefix}.terminal_state",
    )
    _check_outcomes_present(outcomes, terminal_state, errors, path=f"{prefix}.outcomes")
    return EvidenceChain(
        turn_ref=_parse_turn_ref(raw.get("turn_ref"), errors, path=f"{prefix}.turn_ref"),
        trigger=_parse_trigger(_as_mapping(raw.get("trigger")), errors, path=f"{prefix}.trigger"),
        agent_reactions=tuple(
            _parse_reaction(item, errors, path=f"{prefix}.agent_reactions[{index}]")
            for index, item in enumerate(_as_list(raw.get("agent_reactions")))
        ),
        outcomes=outcomes,
        observed_checks=tuple(
            _parse_check(item, errors, path=f"{prefix}.observed_checks[{index}]")
            for index, item in enumerate(_as_list(raw.get("observed_checks")))
        ),
        terminal_state=terminal_state,
        materiality=_parse_enum(
            raw.get("materiality"),
            _MATERIALITY_VALUES,
            errors,
            path=f"{prefix}.materiality",
            controlled="materiality",
        ),
    )


def _parse_trigger(
    raw: dict[str, Any],
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> Trigger:
    return Trigger(
        type=_parse_enum(
            raw.get("type"),
            _TRIGGER_TYPES,
            errors,
            path=f"{path}.type",
            controlled="trigger.type",
        ),
        summary=_parse_summary(raw.get("summary"), errors, path=f"{path}.summary"),
        quoted_messages=tuple(
            _parse_quoted_message(item, errors, path=f"{path}.quoted_messages[{index}]")
            for index, item in enumerate(_as_list(raw.get("quoted_messages")))
        ),
        citations=_parse_citations(raw.get("citations"), errors, path=path),
    )


def _parse_quoted_message(
    raw: object,
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> QuotedMessage:
    message = _as_mapping(raw)
    return QuotedMessage(
        text=_parse_summary(message.get("text"), errors, path=f"{path}.text"),
        citations=_parse_citations(message.get("citations"), errors, path=path),
    )


def _parse_reaction(
    raw: object,
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> AgentReaction:
    reaction = _as_mapping(raw)
    return AgentReaction(
        summary=_parse_summary(reaction.get("summary"), errors, path=f"{path}.summary"),
        citations=_parse_citations(reaction.get("citations"), errors, path=path),
    )


def _parse_outcome(
    raw: object,
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> Outcome:
    outcome = _as_mapping(raw)
    return Outcome(
        category=_parse_enum(
            outcome.get("category"),
            _OUTCOME_CATEGORIES,
            errors,
            path=f"{path}.category",
            controlled="outcome category",
        ),
        summary=_parse_summary(outcome.get("summary"), errors, path=f"{path}.summary"),
        citations=_parse_citations(outcome.get("citations"), errors, path=path),
    )


def _parse_check(
    raw: object,
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> ObservedCheck:
    check = _as_mapping(raw)
    return ObservedCheck(
        type=_parse_enum(
            check.get("type"),
            _CHECK_TYPES,
            errors,
            path=f"{path}.type",
            controlled="check type",
        ),
        summary=_parse_summary(check.get("summary"), errors, path=f"{path}.summary"),
        citations=_parse_citations(check.get("citations"), errors, path=path),
    )


def _parse_terminal_state(
    raw: dict[str, Any],
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> TerminalState:
    return TerminalState(
        type=_parse_enum(
            raw.get("type"),
            _TERMINAL_STATES,
            errors,
            path=f"{path}.type",
            controlled="terminal_state.type",
        ),
        summary=_parse_summary(raw.get("summary"), errors, path=f"{path}.summary"),
        citations=_parse_citations(raw.get("citations"), errors, path=path),
    )


def _check_outcomes_present(
    outcomes: tuple[Outcome, ...],
    terminal_state: TerminalState,
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> None:
    if not outcomes and terminal_state.type == _MATERIAL_RESULT:
        errors.append(
            EvidenceWriteError(
                path,
                _MATERIAL_RESULT_OUTCOME_MESSAGE,
                _MATERIAL_RESULT_OUTCOME_HINT,
            )
        )


def _parse_turn_ref(value: object, errors: list[EvidenceWriteError], *, path: str) -> str:
    return _parse_summary(value, errors, path=path)


def _parse_summary(value: object, errors: list[EvidenceWriteError], *, path: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    errors.append(EvidenceWriteError(path, _summary_message(path), _SUMMARY_HINT))
    return value if isinstance(value, str) else ""


def _parse_enum(
    value: object,
    allowed: frozenset[str],
    errors: list[EvidenceWriteError],
    *,
    path: str,
    controlled: str,
) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    errors.append(
        EvidenceWriteError(path, _controlled_message(path, controlled), _controlled_hint(allowed))
    )
    return value if isinstance(value, str) else ""


def _parse_citations(
    value: object,
    errors: list[EvidenceWriteError],
    *,
    path: str,
) -> tuple[CitationSpan, ...]:
    return tuple(
        _parse_span(item, errors, path=f"{path}.citations[{index}].lines")
        for index, item in enumerate(_as_list(value))
    )


def _parse_span(value: object, errors: list[EvidenceWriteError], *, path: str) -> CitationSpan:
    span = _to_span(value)
    if span is not None:
        return span
    errors.append(EvidenceWriteError(path, _span_message(path), _SPAN_HINT))
    return CitationSpan(0, 0)


def _to_span(value: object) -> CitationSpan | None:
    lines = _as_mapping(value).get("lines")
    if not isinstance(lines, str):
        return None
    start_text, separator, end_text = lines.partition("-")
    if not separator or not start_text.isdigit() or not end_text.isdigit():
        return None
    start, end = int(start_text), int(end_text)
    return CitationSpan(start, end) if start <= end else None


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _trigger_to_json(trigger: Trigger) -> dict[str, Any]:
    return {
        "type": trigger.type,
        "summary": trigger.summary,
        "quoted_messages": [
            {"text": message.text, "citations": _citations_to_json(message.citations)}
            for message in trigger.quoted_messages
        ],
        "citations": _citations_to_json(trigger.citations),
    }


def _reaction_to_json(reaction: AgentReaction) -> dict[str, Any]:
    return {"summary": reaction.summary, "citations": _citations_to_json(reaction.citations)}


def _outcome_to_json(outcome: Outcome) -> dict[str, Any]:
    return {
        "category": outcome.category,
        "summary": outcome.summary,
        "citations": _citations_to_json(outcome.citations),
    }


def _check_to_json(check: ObservedCheck) -> dict[str, Any]:
    return {
        "type": check.type,
        "summary": check.summary,
        "citations": _citations_to_json(check.citations),
    }


def _terminal_state_to_json(terminal_state: TerminalState) -> dict[str, Any]:
    return {
        "type": terminal_state.type,
        "summary": terminal_state.summary,
        "citations": _citations_to_json(terminal_state.citations),
    }


def _citations_to_json(citations: tuple[CitationSpan, ...]) -> list[dict[str, str]]:
    return [{"lines": f"{citation.start}-{citation.end}"} for citation in citations]


def new_session_card(project_key: str, session_ref: str) -> dict[str, Any]:
    """Return the canonical empty session-evidence-card skeleton."""
    return {
        "schema_version": 1,
        "project_key": project_key,
        "session_ref": session_ref,
        "evidence_chains": [],
    }


def _summary_message(path: str) -> str:
    return f"{path} must be a non-empty string"


def _controlled_message(path: str, controlled: str) -> str:
    return f"{path} must be a controlled {controlled} value"


def _controlled_hint(allowed: frozenset[str]) -> str:
    return "use a controlled value such as " + ", ".join(sorted(allowed))


def _span_message(path: str) -> str:
    return f"{path} must be a numeric line span"


_SUMMARY_HINT = "provide a concise non-empty summary"
_SPAN_HINT = 'write the span as "start-end" with start <= end'
_MATERIAL_RESULT_OUTCOME_MESSAGE = (
    "material_result terminal_state requires at least one material outcome; empty outcomes is "
    "allowed only when terminal_state explains a non-success ending"
)
_MATERIAL_RESULT_OUTCOME_HINT = (
    "add a material outcome, or use a terminal_state.type that explains the non-success ending"
)
