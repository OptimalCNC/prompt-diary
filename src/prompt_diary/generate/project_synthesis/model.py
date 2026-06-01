"""Typed work-item model and self-contained parsing for project synthesis.

This module owns the work-item data model and the chain-only validation that depends on nothing but
the submitted work item. Parsing an untrusted work-item dict either yields a fully typed
``WorkItem`` whose values are guaranteed to be well formed, or a structured list of
``WorkItemWriteError``.
Cross-artifact checks that need the prepared workspace (turn coverage, evidence references, coverage
exclusivity) live in the project synthesis MCP API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypeAlias, cast

from prompt_diary.generate.prompts import (
    EVIDENCE_OUTCOME_CATEGORIES,
    EVIDENCE_TERMINAL_STATES,
    PROJECT_WORK_ITEM_KINDS,
)

_WORK_ITEM_REF_RE = re.compile(r"^W\d{4}$")
_CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
_WORK_ITEM_KINDS = frozenset(item.value for item in PROJECT_WORK_ITEM_KINDS)
_OUTCOME_CATEGORIES = frozenset(item.value for item in EVIDENCE_OUTCOME_CATEGORIES)
_TERMINAL_STATES = frozenset(item.value for item in EVIDENCE_TERMINAL_STATES)

_MATERIAL = "material_work_item"
_EVIDENCE_GAP = "evidence_gap_item"
_EXCLUDED = "excluded_with_reason"
_NARRATIVE_EMPTY_KINDS = frozenset({_EVIDENCE_GAP, _EXCLUDED})


@dataclass(frozen=True)
class WorkItemWriteError:
    """Structured validation error returned by rejected work-item writes."""

    path: str
    message: str
    hint: str


@dataclass(frozen=True)
class TurnReference:
    """A reference to one indexed turn, as ``(session_ref, turn_ref)``."""

    session_ref: str
    turn_ref: str


@dataclass(frozen=True)
class TriggerBlock:
    """The earliest meaningful human trigger for a work item."""

    summary: str
    evidence_refs: tuple[TurnReference, ...]


@dataclass(frozen=True)
class AgentReactionBlock:
    """What the agent did across a work item."""

    summary: str
    main_actions: tuple[str, ...]


@dataclass(frozen=True)
class WorkItemOutcome:
    """One consolidated achievement of a work item."""

    category: str
    summary: str
    evidence_refs: tuple[TurnReference, ...]
    confidence: str


@dataclass(frozen=True)
class WorkItemTerminalState:
    """How a work item or one of its branches ended."""

    type: str
    summary: str
    evidence_refs: tuple[TurnReference, ...]


@dataclass(frozen=True)
class WorkItem:
    """One project-level work item parsed into a fully typed, well-formed node."""

    work_item_ref: str
    kind: str
    title: str
    covered_turns: tuple[TurnReference, ...]
    trigger: TriggerBlock | None
    agent_reaction: AgentReactionBlock | None
    outcomes: tuple[WorkItemOutcome, ...]
    terminal_states: tuple[WorkItemTerminalState, ...]
    limits: tuple[str, ...]
    reason: str | None
    confidence: str


@dataclass(frozen=True)
class ParsedWorkItem:
    """A successful parse holding a guaranteed well-formed work item."""

    work_item: WorkItem


@dataclass(frozen=True)
class InvalidWorkItem:
    """A rejected parse holding the structural errors found in the work item."""

    errors: tuple[WorkItemWriteError, ...]


WorkItemParseResult: TypeAlias = ParsedWorkItem | InvalidWorkItem


def parse_work_item(raw: dict[str, Any]) -> WorkItemParseResult:
    """Parse an untrusted work-item dict into a typed work item or structured errors."""
    errors: list[WorkItemWriteError] = []
    work_item = _parse_work_item(raw, errors)
    if errors:
        return InvalidWorkItem(tuple(errors))
    return ParsedWorkItem(work_item)


def work_item_to_json(item: WorkItem) -> dict[str, Any]:
    """Serialize a typed work item into the canonical envelope shape."""
    result: dict[str, Any] = {
        "work_item_ref": item.work_item_ref,
        "kind": item.kind,
        "title": item.title,
        "covered_turns": [_turn_ref_to_json(ref) for ref in item.covered_turns],
    }
    if item.trigger is not None:
        result["trigger"] = {
            "summary": item.trigger.summary,
            "evidence_refs": [_turn_ref_to_json(ref) for ref in item.trigger.evidence_refs],
        }
    if item.agent_reaction is not None:
        result["agent_reaction"] = {
            "summary": item.agent_reaction.summary,
            "main_actions": list(item.agent_reaction.main_actions),
        }
    result["outcomes"] = [_outcome_to_json(outcome) for outcome in item.outcomes]
    result["terminal_states"] = [_terminal_state_to_json(state) for state in item.terminal_states]
    result["limits"] = list(item.limits)
    if item.reason is not None:
        result["reason"] = item.reason
    result["confidence"] = item.confidence
    return result


def new_project_synthesis_envelope(project_key: str, project_label: str) -> dict[str, Any]:
    """Return the canonical empty project-synthesis envelope skeleton."""
    return {
        "schema_version": 1,
        "project_key": project_key,
        "project_label": project_label,
        "work_items": [],
        "source_user_messages": [],
    }


def _parse_work_item(raw: dict[str, Any], errors: list[WorkItemWriteError]) -> WorkItem:
    prefix = "work_item"
    kind = _parse_enum(
        raw.get("kind"),
        _WORK_ITEM_KINDS,
        errors,
        path=f"{prefix}.kind",
        controlled="work item kind",
    )
    trigger = _parse_optional_trigger(raw.get("trigger"), errors, path=f"{prefix}.trigger")
    agent_reaction = _parse_optional_reaction(
        raw.get("agent_reaction"), errors, path=f"{prefix}.agent_reaction"
    )
    outcomes = tuple(
        _parse_outcome(item, errors, path=f"{prefix}.outcomes[{index}]")
        for index, item in enumerate(_as_list(raw.get("outcomes")))
    )
    terminal_states = tuple(
        _parse_terminal_state(item, errors, path=f"{prefix}.terminal_states[{index}]")
        for index, item in enumerate(_as_list(raw.get("terminal_states")))
    )
    _check_required_by_kind(
        kind, trigger, agent_reaction, outcomes, terminal_states, errors, prefix=prefix
    )
    return WorkItem(
        work_item_ref=_parse_work_item_ref(
            raw.get("work_item_ref"), errors, path=f"{prefix}.work_item_ref"
        ),
        kind=kind,
        title=_parse_summary(raw.get("title"), errors, path=f"{prefix}.title"),
        covered_turns=_parse_turn_refs(
            raw.get("covered_turns"), errors, path=f"{prefix}.covered_turns", require_non_empty=True
        ),
        trigger=trigger,
        agent_reaction=agent_reaction,
        outcomes=outcomes,
        terminal_states=terminal_states,
        limits=_parse_str_list(raw.get("limits"), errors, path=f"{prefix}.limits"),
        reason=_parse_reason(raw.get("reason"), kind, errors, path=f"{prefix}.reason"),
        confidence=_parse_enum(
            raw.get("confidence"),
            _CONFIDENCE_VALUES,
            errors,
            path=f"{prefix}.confidence",
            controlled="confidence",
        ),
    )


def _parse_optional_trigger(
    raw: object, errors: list[WorkItemWriteError], *, path: str
) -> TriggerBlock | None:
    if raw is None:
        return None
    mapping = _as_mapping(raw)
    return TriggerBlock(
        summary=_parse_summary(mapping.get("summary"), errors, path=f"{path}.summary"),
        evidence_refs=_parse_turn_refs(
            mapping.get("evidence_refs"),
            errors,
            path=f"{path}.evidence_refs",
            require_non_empty=False,
        ),
    )


def _parse_optional_reaction(
    raw: object, errors: list[WorkItemWriteError], *, path: str
) -> AgentReactionBlock | None:
    if raw is None:
        return None
    mapping = _as_mapping(raw)
    return AgentReactionBlock(
        summary=_parse_summary(mapping.get("summary"), errors, path=f"{path}.summary"),
        main_actions=_parse_str_list(
            mapping.get("main_actions"), errors, path=f"{path}.main_actions"
        ),
    )


def _parse_outcome(raw: object, errors: list[WorkItemWriteError], *, path: str) -> WorkItemOutcome:
    outcome = _as_mapping(raw)
    return WorkItemOutcome(
        category=_parse_enum(
            outcome.get("category"),
            _OUTCOME_CATEGORIES,
            errors,
            path=f"{path}.category",
            controlled="outcome category",
        ),
        summary=_parse_summary(outcome.get("summary"), errors, path=f"{path}.summary"),
        evidence_refs=_parse_turn_refs(
            outcome.get("evidence_refs"),
            errors,
            path=f"{path}.evidence_refs",
            require_non_empty=False,
        ),
        confidence=_parse_enum(
            outcome.get("confidence"),
            _CONFIDENCE_VALUES,
            errors,
            path=f"{path}.confidence",
            controlled="confidence",
        ),
    )


def _parse_terminal_state(
    raw: object, errors: list[WorkItemWriteError], *, path: str
) -> WorkItemTerminalState:
    state = _as_mapping(raw)
    return WorkItemTerminalState(
        type=_parse_enum(
            state.get("type"),
            _TERMINAL_STATES,
            errors,
            path=f"{path}.type",
            controlled="terminal_state type",
        ),
        summary=_parse_summary(state.get("summary"), errors, path=f"{path}.summary"),
        evidence_refs=_parse_turn_refs(
            state.get("evidence_refs"),
            errors,
            path=f"{path}.evidence_refs",
            require_non_empty=False,
        ),
    )


def _parse_turn_refs(
    value: object, errors: list[WorkItemWriteError], *, path: str, require_non_empty: bool
) -> tuple[TurnReference, ...]:
    items = _as_list(value)
    if require_non_empty and not items:
        errors.append(WorkItemWriteError(path, _non_empty_list_message(path), _COVERED_TURNS_HINT))
    return tuple(
        _parse_turn_ref(item, errors, path=f"{path}[{index}]") for index, item in enumerate(items)
    )


def _parse_turn_ref(value: object, errors: list[WorkItemWriteError], *, path: str) -> TurnReference:
    mapping = _as_mapping(value)
    return TurnReference(
        session_ref=_parse_ref_field(
            mapping.get("session_ref"), errors, path=f"{path}.session_ref"
        ),
        turn_ref=_parse_ref_field(mapping.get("turn_ref"), errors, path=f"{path}.turn_ref"),
    )


def _parse_ref_field(value: object, errors: list[WorkItemWriteError], *, path: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    errors.append(WorkItemWriteError(path, _summary_message(path), _REF_HINT))
    return value if isinstance(value, str) else ""


def _parse_str_list(
    value: object, errors: list[WorkItemWriteError], *, path: str
) -> tuple[str, ...]:
    result: list[str] = []
    for index, item in enumerate(_as_list(value)):
        if isinstance(item, str) and item.strip():
            result.append(item)
        else:
            errors.append(
                WorkItemWriteError(
                    f"{path}[{index}]", _summary_message(f"{path}[{index}]"), _SUMMARY_HINT
                )
            )
    return tuple(result)


def _parse_reason(
    value: object, kind: str, errors: list[WorkItemWriteError], *, path: str
) -> str | None:
    if kind == _EXCLUDED:
        return _parse_summary(value, errors, path=path)
    return value if isinstance(value, str) else None


def _check_required_by_kind(
    kind: str,
    trigger: TriggerBlock | None,
    agent_reaction: AgentReactionBlock | None,
    outcomes: tuple[WorkItemOutcome, ...],
    terminal_states: tuple[WorkItemTerminalState, ...],
    errors: list[WorkItemWriteError],
    *,
    prefix: str,
) -> None:
    if kind == _MATERIAL:
        if trigger is None:
            errors.append(
                WorkItemWriteError(
                    f"{prefix}.trigger", _required_message("trigger"), _MATERIAL_HINT
                )
            )
        if agent_reaction is None:
            errors.append(
                WorkItemWriteError(
                    f"{prefix}.agent_reaction", _required_message("agent_reaction"), _MATERIAL_HINT
                )
            )
        if not outcomes and not terminal_states:
            errors.append(
                WorkItemWriteError(
                    f"{prefix}.outcomes", _MATERIAL_RESULT_MESSAGE, _MATERIAL_RESULT_HINT
                )
            )
        return
    if kind in _NARRATIVE_EMPTY_KINDS:
        if trigger is not None:
            errors.append(_narrative_error(kind, f"{prefix}.trigger", "trigger"))
        if agent_reaction is not None:
            errors.append(_narrative_error(kind, f"{prefix}.agent_reaction", "agent_reaction"))
        if outcomes:
            errors.append(_narrative_error(kind, f"{prefix}.outcomes", "outcomes"))
        if terminal_states:
            errors.append(_narrative_error(kind, f"{prefix}.terminal_states", "terminal_states"))


def _parse_work_item_ref(value: object, errors: list[WorkItemWriteError], *, path: str) -> str:
    if isinstance(value, str) and _WORK_ITEM_REF_RE.fullmatch(value):
        return value
    errors.append(WorkItemWriteError(path, _work_item_ref_message(path), _WORK_ITEM_REF_HINT))
    return value if isinstance(value, str) else ""


def _parse_summary(value: object, errors: list[WorkItemWriteError], *, path: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    errors.append(WorkItemWriteError(path, _summary_message(path), _SUMMARY_HINT))
    return value if isinstance(value, str) else ""


def _parse_enum(
    value: object,
    allowed: frozenset[str],
    errors: list[WorkItemWriteError],
    *,
    path: str,
    controlled: str,
) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    errors.append(
        WorkItemWriteError(path, _controlled_message(path, controlled), _controlled_hint(allowed))
    )
    return value if isinstance(value, str) else ""


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _turn_ref_to_json(ref: TurnReference) -> dict[str, str]:
    return {"session_ref": ref.session_ref, "turn_ref": ref.turn_ref}


def _outcome_to_json(outcome: WorkItemOutcome) -> dict[str, Any]:
    return {
        "category": outcome.category,
        "summary": outcome.summary,
        "evidence_refs": [_turn_ref_to_json(ref) for ref in outcome.evidence_refs],
        "confidence": outcome.confidence,
    }


def _terminal_state_to_json(state: WorkItemTerminalState) -> dict[str, Any]:
    return {
        "type": state.type,
        "summary": state.summary,
        "evidence_refs": [_turn_ref_to_json(ref) for ref in state.evidence_refs],
    }


def _summary_message(path: str) -> str:
    return f"{path} must be a non-empty string"


def _controlled_message(path: str, controlled: str) -> str:
    return f"{path} must be a controlled {controlled} value"


def _controlled_hint(allowed: frozenset[str]) -> str:
    return "use a controlled value such as " + ", ".join(sorted(allowed))


def _non_empty_list_message(path: str) -> str:
    return f"{path} must list at least one entry"


def _work_item_ref_message(path: str) -> str:
    return f"{path} must match W0001"


def _required_message(field: str) -> str:
    return f"material_work_item requires {field}"


def _narrative_error(kind: str, path: str, field: str) -> WorkItemWriteError:
    return WorkItemWriteError(path, f"{kind} must leave {field} empty", _NARRATIVE_EMPTY_HINT)


_SUMMARY_HINT = "provide a concise non-empty string"
_REF_HINT = 'reference a turn as {"session_ref": "S0001", "turn_ref": "T0001"}'
_COVERED_TURNS_HINT = "every work item must account for at least one indexed turn"
_WORK_ITEM_REF_HINT = "assign refs as W0001, W0002, and so on"
_MATERIAL_HINT = "material_work_item requires trigger and agent_reaction"
_MATERIAL_RESULT_MESSAGE = "material_work_item requires at least one outcome or terminal_state"
_MATERIAL_RESULT_HINT = "add a consolidated outcome or a terminal_state describing the result"
_NARRATIVE_EMPTY_HINT = (
    "evidence_gap_item and excluded_with_reason carry no trigger, agent_reaction, outcomes, or "
    "terminal_states"
)
