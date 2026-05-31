# Project Synthesis Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the project synthesis phase — the `write_work_item` MCP tool and the agent-driven runner that groups one project's evidence chains into project-level work items — replacing the `ProjectSynthesisRunner` stub.

**Architecture:** Mirror the evidence-extraction package exactly. A transport-independent API package `src/prompt_diary/generate/project_synthesis/` holds the typed work-item model + chain-only parse (`model.py`), the per-project evidence-card reader (`cards.py`), the prompt-input/paste builder (`inputs.py`), and the workspace-dependent write API (`mcp.py`). The MCP adapter is registered in `src/prompt_diary/mcp/server.py`. The phase runner (`runner.py`) builds the trimmed evidence-chain paste, runs **one** agent turn (the synthesizer self-loops on `write_work_item`'s `uncovered_turns`), then verifies the coverage invariant.

**Tech Stack:** Python 3.10+, `uv`, frozen dataclasses, `basedpyright` (strict), `ruff`, `pytest`. MCP via `FastMCP`. Agent seam via `prompt_diary.agent` (`AgentSessionFactory`/`AgentRunner`/`AgentConfig`/`AgentTurnResult`).

---

## Contracts (read before implementing)

- `docs/src/generate/project-synthesis.md` — the phase contract: work-item schema, kinds, coverage invariant, `source_user_messages`.
- `docs/src/generate/mcp-tools/project-synthesis.md` — the `write_work_item` tool contract: input schema, write behavior, structural rules, result shape.
- The committed prompt `src/prompt_diary/generate/prompts/project-synthesizer.md` is **done** and already tested by `tests/generate/test_prompts.py::test_project_synthesizer_prompt`. **Do not modify the prompt or `project_synthesizer_prompt(...)`.**

## Reference implementations to mirror (do not modify them)

| New file | Mirrors |
| --- | --- |
| `project_synthesis/model.py` | `src/prompt_diary/generate/evidence_extraction/model.py` |
| `project_synthesis/cards.py` | (new responsibility — reads evidence cards) |
| `project_synthesis/inputs.py` | `src/prompt_diary/generate/evidence_extraction/inputs.py` |
| `project_synthesis/mcp.py` | `src/prompt_diary/generate/evidence_extraction/mcp.py` |
| `project_synthesis/runner.py` | `src/prompt_diary/generate/evidence_extraction/runner.py` |
| `mcp/server.py` (edit) | existing `write_evidence` registration |
| `tests/support/project_synthesis.py` | `tests/support/evidence_extraction.py` |
| `tests/support/project_synthesis_agent.py` | `tests/support/evidence_agent.py` |
| `tests/generate/project_synthesis/test_*.py` | `tests/generate/evidence_extraction/test_*.py` |
| `tests/integrations/test_project_synthesis_codex.py` | `tests/integrations/test_evidence_extraction_codex.py` |

## Locked design decisions

1. **Single agent turn + verify coverage.** The runner renders the prompt with all committed chains pasted in, runs `await runner.turn(prompt)` once, then reads `project-synthesis.json` and computes uncovered turns. Empty → `success`; otherwise → `failed` listing the uncovered turns. This mirrors evidence extraction's "verify-after-turn-or-fail" and matches the prompt's self-looping procedure. No orchestrator-driven continuation prompt; **no new prompt file.**
2. **Gap turns live inside existing cards.** A "gap turn" is an indexed turn with no chain in its (existing) evidence card. The pipeline already requires every session's `evidence/<session_ref>.json` as a project-synthesis prerequisite, so an entirely-missing card blocks the task upstream; gaps therefore appear as turns absent from a present (partial/empty) card.
3. **Known limitation (documented, not handled): all-gap project.** If a project has indexed turns but *zero* committed chains, the agent cannot learn any turn ref (the paste is empty and `write_work_item` only reveals `uncovered_turns` in response to a committed item), so coverage cannot be bootstrapped and the runner returns `failed`. This is an extremely rare degenerate case (every session's extraction produced nothing) and is out of scope for this MVP. Add a one-line code comment noting it in `runner.py`.
4. **`write_work_item` registers only in `server.py`.** The whole `prompt_diary` MCP server is exposed to the agent via `report mcp serve`; `codex_config.py` needs no change.
5. **`confidence` is a fixed set** (`high`/`medium`/`low`), validated in `model.py` via a local frozenset — it is not a rendered prompt enum (the prompt hardcodes the literals). `kind`, outcome `category`, and terminal `type` reuse `PROJECT_WORK_ITEM_KINDS`, `EVIDENCE_OUTCOME_CATEGORIES`, `EVIDENCE_TERMINAL_STATES` from `prompts/__init__.py`.

## Validation commands (run after every task)

```bash
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest tests/generate/project_synthesis tests/mcp/test_server.py tests/generate/test_prompts.py -q
```

The real-agent integration test is opt-in and excluded by default:

```bash
uv run pytest tests/integrations/test_project_synthesis_codex.py --run-codex-mcp -q   # needs a working `codex` binary
```

---

# Part 1 — The `write_work_item` MCP tool

## Task 1: Work-item model and chain-only parsing

**Files:**
- Create: `src/prompt_diary/generate/project_synthesis/model.py`
- Create: `tests/generate/project_synthesis/__init__.py` (empty)
- Create: `tests/support/project_synthesis.py` (work-item builders only this task)
- Test: `tests/generate/project_synthesis/test_model.py`

**What it does:** Defines the typed `WorkItem` model and `parse_work_item`, which validates everything that depends only on the submitted dict (no workspace): `work_item_ref` shape, `kind`/`confidence`/`category`/`terminal type` enums, non-empty `title`/summaries, turn-ref shape, and required-fields-per-kind. Also `work_item_to_json` (serialize) and `new_project_synthesis_envelope`.

- [ ] **Step 1: Add work-item builders to the shared support module.**

Create `tests/support/project_synthesis.py`:

```python
from __future__ import annotations

from typing import Any

PROJECT_KEY = "ReportGenerator-e6ff7eeda632"
PROJECT_LABEL = "ReportGenerator"


def turn_ref(session_ref: str, turn: str) -> dict[str, str]:
    return {"session_ref": session_ref, "turn_ref": turn}


def valid_material_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0001",
        "kind": "material_work_item",
        "title": "Finalize the evidence-extraction contract",
        "covered_turns": [turn_ref("S0001", "T0001"), turn_ref("S0001", "T0002")],
        "trigger": {
            "summary": "User drove the evidence surface to turn_ref and finalized the choices.",
            "evidence_refs": [turn_ref("S0001", "T0001")],
        },
        "agent_reaction": {
            "summary": "Migrated the contract and prompt, then froze with a commit.",
            "main_actions": ["turn_ref migration", "freeze commit"],
        },
        "outcomes": [
            {
                "category": "document_outcome",
                "summary": "Evidence contract moved to top-level turn_ref.",
                "evidence_refs": [turn_ref("S0001", "T0001")],
                "confidence": "high",
            }
        ],
        "terminal_states": [
            {
                "type": "material_result",
                "summary": "Contract frozen as a checkpoint commit.",
                "evidence_refs": [turn_ref("S0001", "T0002")],
            }
        ],
        "limits": ["Prompt-test suite not confirmed green within these turns."],
        "confidence": "high",
    }


def valid_no_material_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0002",
        "kind": "no_material_work_item",
        "title": "Trivial connectivity and throwaway questions",
        "covered_turns": [turn_ref("S0002", "T0001")],
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def valid_evidence_gap_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0003",
        "kind": "evidence_gap_item",
        "title": "Indexed turns with no extractable evidence",
        "covered_turns": [turn_ref("S0001", "T0003")],
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def valid_excluded_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0004",
        "kind": "excluded_with_reason",
        "title": "Duplicate evidence already represented elsewhere",
        "covered_turns": [turn_ref("S0002", "T0002")],
        "reason": "Duplicate of W0001; the same edit is already represented there.",
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def work_item_with_value(path: tuple[str | int, ...], value: Any) -> dict[str, Any]:
    item = valid_material_work_item()
    target: Any = item
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    return item
```

- [ ] **Step 2: Write the failing model tests.**

Create `tests/generate/project_synthesis/__init__.py` (empty file) and `tests/generate/project_synthesis/test_model.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from prompt_diary.generate.project_synthesis.model import (
    InvalidWorkItem,
    ParsedWorkItem,
    TurnReference,
    new_project_synthesis_envelope,
    parse_work_item,
    work_item_to_json,
)
from tests.support.project_synthesis import (
    PROJECT_KEY,
    PROJECT_LABEL,
    valid_evidence_gap_work_item,
    valid_excluded_work_item,
    valid_material_work_item,
    valid_no_material_work_item,
    work_item_with_value,
)


def _errors(result: object) -> list[Any]:
    assert isinstance(result, InvalidWorkItem)
    return [error.path for error in result.errors]


@pytest.mark.parametrize(
    "factory",
    [
        valid_material_work_item,
        valid_no_material_work_item,
        valid_evidence_gap_work_item,
        valid_excluded_work_item,
    ],
)
def test_parse_accepts_every_valid_kind(factory: Any) -> None:
    result = parse_work_item(factory())
    assert isinstance(result, ParsedWorkItem)


def test_parse_typed_fields_round_trip() -> None:
    result = parse_work_item(valid_material_work_item())
    assert isinstance(result, ParsedWorkItem)
    item = result.work_item
    assert item.work_item_ref == "W0001"
    assert item.kind == "material_work_item"
    assert item.covered_turns == (
        TurnReference("S0001", "T0001"),
        TurnReference("S0001", "T0002"),
    )
    assert item.trigger is not None
    assert item.trigger.evidence_refs == (TurnReference("S0001", "T0001"),)
    assert item.outcomes[0].category == "document_outcome"
    assert item.terminal_states[0].type == "material_result"


def test_work_item_to_json_is_canonical_and_omits_absent_blocks() -> None:
    parsed = parse_work_item(valid_no_material_work_item())
    assert isinstance(parsed, ParsedWorkItem)
    payload = work_item_to_json(parsed.work_item)
    assert payload["work_item_ref"] == "W0002"
    assert payload["covered_turns"] == [{"session_ref": "S0002", "turn_ref": "T0001"}]
    assert "trigger" not in payload
    assert "agent_reaction" not in payload
    assert "reason" not in payload
    assert payload["outcomes"] == []


def test_excluded_to_json_includes_reason() -> None:
    parsed = parse_work_item(valid_excluded_work_item())
    assert isinstance(parsed, ParsedWorkItem)
    payload = work_item_to_json(parsed.work_item)
    assert payload["reason"].startswith("Duplicate")


@pytest.mark.parametrize(
    ("path", "value", "error_path"),
    [
        (("work_item_ref",), "WX", "work_item.work_item_ref"),
        (("work_item_ref",), "1", "work_item.work_item_ref"),
        (("kind",), "material", "work_item.kind"),
        (("title",), "  ", "work_item.title"),
        (("confidence",), "definitely", "work_item.confidence"),
        (("outcomes", 0, "category"), "documentation", "work_item.outcomes[0].category"),
        (("outcomes", 0, "confidence"), "huge", "work_item.outcomes[0].confidence"),
        (("terminal_states", 0, "type"), "done", "work_item.terminal_states[0].type"),
        (("covered_turns", 0, "turn_ref"), "  ", "work_item.covered_turns[0].turn_ref"),
        (("covered_turns",), [], "work_item.covered_turns"),
    ],
)
def test_parse_rejects_structural_violations(
    path: tuple[Any, ...], value: Any, error_path: str
) -> None:
    assert error_path in _errors(parse_work_item(work_item_with_value(path, value)))


def test_material_requires_trigger_reaction_and_a_result() -> None:
    item = valid_material_work_item()
    del item["trigger"]
    del item["agent_reaction"]
    item["outcomes"] = []
    item["terminal_states"] = []
    paths = _errors(parse_work_item(item))
    assert "work_item.trigger" in paths
    assert "work_item.agent_reaction" in paths
    assert "work_item.outcomes" in paths


def test_excluded_requires_reason() -> None:
    item = valid_excluded_work_item()
    del item["reason"]
    assert "work_item.reason" in _errors(parse_work_item(item))


def test_new_envelope_skeleton() -> None:
    envelope = new_project_synthesis_envelope(PROJECT_KEY, PROJECT_LABEL)
    assert envelope == {
        "schema_version": 1,
        "project_key": PROJECT_KEY,
        "project_label": PROJECT_LABEL,
        "work_items": [],
        "source_user_messages": [],
    }
```

- [ ] **Step 3: Run the tests; verify they fail with ModuleNotFoundError.**

Run: `uv run pytest tests/generate/project_synthesis/test_model.py -q`
Expected: FAIL — `No module named 'prompt_diary.generate.project_synthesis.model'`.

- [ ] **Step 4: Implement `model.py`.**

Create `src/prompt_diary/generate/project_synthesis/model.py`:

```python
"""Typed work-item model and self-contained parsing for project synthesis.

This module owns the work-item data model and the chain-only validation that depends on nothing but
the submitted work item. Parsing an untrusted work-item dict either yields a fully typed ``WorkItem``
whose values are guaranteed to be well formed, or a structured list of ``WorkItemWriteError``.
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
_EXCLUDED = "excluded_with_reason"


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
    """The earliest meaningful human trigger for a work thread."""

    summary: str
    evidence_refs: tuple[TurnReference, ...]


@dataclass(frozen=True)
class AgentReactionBlock:
    """What the agent did across a work thread."""

    summary: str
    main_actions: tuple[str, ...]


@dataclass(frozen=True)
class WorkItemOutcome:
    """One consolidated achievement of a work thread."""

    category: str
    summary: str
    evidence_refs: tuple[TurnReference, ...]
    confidence: str


@dataclass(frozen=True)
class WorkItemTerminalState:
    """How a work thread or one of its branches ended."""

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
        raw.get("kind"), _WORK_ITEM_KINDS, errors, path=f"{prefix}.kind", controlled="work item kind"
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
            mapping.get("evidence_refs"), errors, path=f"{path}.evidence_refs", require_non_empty=False
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
        main_actions=_parse_str_list(mapping.get("main_actions"), errors, path=f"{path}.main_actions"),
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
            outcome.get("evidence_refs"), errors, path=f"{path}.evidence_refs", require_non_empty=False
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
            state.get("evidence_refs"), errors, path=f"{path}.evidence_refs", require_non_empty=False
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
        session_ref=_parse_ref_field(mapping.get("session_ref"), errors, path=f"{path}.session_ref"),
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
                WorkItemWriteError(f"{path}[{index}]", _summary_message(f"{path}[{index}]"), _SUMMARY_HINT)
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
    if kind != _MATERIAL:
        return
    if trigger is None:
        errors.append(
            WorkItemWriteError(f"{prefix}.trigger", _required_message("trigger"), _MATERIAL_HINT)
        )
    if agent_reaction is None:
        errors.append(
            WorkItemWriteError(
                f"{prefix}.agent_reaction", _required_message("agent_reaction"), _MATERIAL_HINT
            )
        )
    if not outcomes and not terminal_states:
        errors.append(
            WorkItemWriteError(f"{prefix}.outcomes", _MATERIAL_RESULT_MESSAGE, _MATERIAL_RESULT_HINT)
        )


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


_SUMMARY_HINT = "provide a concise non-empty string"
_REF_HINT = 'reference a turn as {"session_ref": "S0001", "turn_ref": "T0001"}'
_COVERED_TURNS_HINT = "every work item must account for at least one indexed turn"
_WORK_ITEM_REF_HINT = "assign refs as W0001, W0002, and so on"
_MATERIAL_HINT = "material_work_item requires trigger and agent_reaction"
_MATERIAL_RESULT_MESSAGE = "material_work_item requires at least one outcome or terminal_state"
_MATERIAL_RESULT_HINT = "add a consolidated outcome or a terminal_state describing the result"
```

- [ ] **Step 5: Run the model tests; verify they pass.**

Run: `uv run pytest tests/generate/project_synthesis/test_model.py -q`
Expected: PASS (all).

- [ ] **Step 6: Gates.**

Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: clean. (If `ruff format` wants changes, run `uv run ruff format src/prompt_diary/generate/project_synthesis/model.py tests/support/project_synthesis.py tests/generate/project_synthesis/test_model.py` and re-check.)

- [ ] **Step 7: Commit.**

```bash
git add src/prompt_diary/generate/project_synthesis/model.py tests/support/project_synthesis.py tests/generate/project_synthesis/
git commit -m "feat(project-synthesis): typed work-item model and chain-only parsing"
```

## Task 2: Per-project evidence-card reader + the shared fixture

**Files:**
- Create: `src/prompt_diary/generate/project_synthesis/cards.py`
- Create fixture: `tests/fixtures/project-synthesis/basic/workspace/metadata.json`
- Create fixture: `tests/fixtures/project-synthesis/basic/workspace/projects/ReportGenerator-e6ff7eeda632/project.json`
- Create fixture: `tests/fixtures/project-synthesis/basic/workspace/projects/ReportGenerator-e6ff7eeda632/sessions.index.jsonl`
- Create fixture: `tests/fixtures/project-synthesis/basic/workspace/projects/ReportGenerator-e6ff7eeda632/evidence/S0001.json`
- Create fixture: `tests/fixtures/project-synthesis/basic/workspace/projects/ReportGenerator-e6ff7eeda632/evidence/S0002.json`
- Modify: `tests/support/project_synthesis.py` (add fixture copy + envelope loaders)
- Test: `tests/generate/project_synthesis/test_cards.py`

**What it does:** `load_committed_chains(workspace_path, project_key)` reads every `evidence/<session_ref>.json` card for the project and returns typed `CommittedChain`s in (session-index order, card order). Both the paste builder and `write_work_item` read cards through here, so the committed-turn set and the paste always agree. The fixture is a post-extraction workspace: 2 sessions, **4 indexed turns**, **3 committed chains**, and **one gap turn** (`S0001/T0003`, indexed but absent from a present partial card).

> **Note on session transcripts:** the fixture deliberately omits `sessions/*.jsonl` files. Nothing in project synthesis reads transcripts; `load_prepared_workspace` validates only the shape of `session_path` (it never opens the file), and the project-synthesis pipeline prerequisites are `metadata.json`, `project.json`, `sessions.index.jsonl`, and the evidence cards — not transcripts.

- [ ] **Step 1: Write the fixture files.**

`tests/fixtures/project-synthesis/basic/workspace/metadata.json`:

```json
{
  "schema_version": 2,
  "report_date": "2026-05-28",
  "timezone": "Asia/Shanghai",
  "status": "final",
  "prepared_at": "2026-05-29T12:04:14+08:00",
  "report_window_local": {
    "start": "2026-05-28T00:00:00+08:00",
    "end": "2026-05-29T00:00:00+08:00"
  },
  "report_window_utc": {
    "start": "2026-05-27T16:00:00Z",
    "end": "2026-05-28T16:00:00Z"
  }
}
```

`.../projects/ReportGenerator-e6ff7eeda632/project.json`:

```json
{
  "schema_version": 2,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "project_label": "ReportGenerator"
}
```

`.../projects/ReportGenerator-e6ff7eeda632/sessions.index.jsonl` (exactly two lines, each one JSON object):

```jsonl
{"session_path": "sessions/codex/session-001.jsonl", "session_ref": "S0001", "source": "codex", "source_session_id": "019e6c3a-aaaa-78b0-8fe5-50ee8ae8d001", "subagent_path": "", "target_start_line": 2, "target_end_line": 15, "turns": [{"turn_ref": "T0001", "turn_start_line": 2, "turn_end_line": 8, "target_subagents": []}, {"turn_ref": "T0002", "turn_start_line": 9, "turn_end_line": 12, "target_subagents": []}, {"turn_ref": "T0003", "turn_start_line": 13, "turn_end_line": 15, "target_subagents": []}]}
{"session_path": "sessions/codex/session-002.jsonl", "session_ref": "S0002", "source": "codex", "source_session_id": "019e6c3a-bbbb-78b0-8fe5-50ee8ae8d002", "subagent_path": "", "target_start_line": 2, "target_end_line": 6, "turns": [{"turn_ref": "T0001", "turn_start_line": 2, "turn_end_line": 6, "target_subagents": []}]}
```

`.../projects/ReportGenerator-e6ff7eeda632/evidence/S0001.json` (chains for `T0001`, `T0002`; **`T0003` intentionally absent** → gap):

```json
{
  "schema_version": 1,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "evidence_chains": [
    {
      "turn_ref": "T0001",
      "trigger": {
        "type": "explicit_user_message",
        "summary": "User asked to simplify the MCP evidence tools and remove chain_ref.",
        "quoted_messages": [
          {"text": "Please simplify the MCP evidence tools and drop chain_ref.", "citations": [{"lines": "2-2"}]}
        ],
        "citations": [{"lines": "2-2"}]
      },
      "agent_reactions": [
        {"summary": "Updated the MCP tools page, evidence contract, and extractor prompt.", "citations": [{"lines": "3-5"}]}
      ],
      "outcomes": [
        {"category": "document_outcome", "summary": "Top-level turn_ref adopted; chain_ref removed.", "citations": [{"lines": "5-5"}]}
      ],
      "observed_checks": [],
      "terminal_state": {"type": "material_result", "summary": "Extraction surface updated to turn_ref identity.", "citations": [{"lines": "8-8"}]},
      "materiality": "material"
    },
    {
      "turn_ref": "T0002",
      "trigger": {
        "type": "user_correction",
        "summary": "User asked whether the placeholder wording was misleading.",
        "quoted_messages": [
          {"text": "Is that placeholder misleading?", "citations": [{"lines": "9-9"}]}
        ],
        "citations": [{"lines": "9-9"}]
      },
      "agent_reactions": [
        {"summary": "Discussed the wording and chose a direction.", "citations": [{"lines": "10-10"}]}
      ],
      "outcomes": [],
      "observed_checks": [],
      "terminal_state": {"type": "clarification_only", "summary": "Wording direction chosen; no artifact produced.", "citations": [{"lines": "12-12"}]},
      "materiality": "minor"
    }
  ]
}
```

`.../projects/ReportGenerator-e6ff7eeda632/evidence/S0002.json`:

```json
{
  "schema_version": 1,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0002",
  "evidence_chains": [
    {
      "turn_ref": "T0001",
      "trigger": {
        "type": "explicit_user_message",
        "summary": "User asked to design the evidence-extraction QA approach.",
        "quoted_messages": [
          {"text": "Design the QA approach for evidence extraction.", "citations": [{"lines": "2-2"}]}
        ],
        "citations": [{"lines": "2-2"}]
      },
      "agent_reactions": [
        {"summary": "Outlined a three-layer test strategy and wrote the QA notes.", "citations": [{"lines": "3-5"}]}
      ],
      "outcomes": [
        {"category": "process_outcome", "summary": "Three-layer QA strategy delivered.", "citations": [{"lines": "5-5"}]}
      ],
      "observed_checks": [],
      "terminal_state": {"type": "material_result", "summary": "QA design delivered.", "citations": [{"lines": "6-6"}]},
      "materiality": "material"
    }
  ]
}
```

- [ ] **Step 2: Extend the support module with fixture copy + loaders.**

Add to the **top imports** of `tests/support/project_synthesis.py`:

```python
import copy
import json
import shutil
from pathlib import Path
from typing import cast
```

Add these module-level definitions and functions to `tests/support/project_synthesis.py`:

```python
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "project-synthesis" / "basic"

# Indexed-turn universe of the basic fixture, in (session, turn) order. S0001/T0003 is the gap turn.
ALL_TURNS: tuple[tuple[str, str], ...] = (
    ("S0001", "T0001"),
    ("S0001", "T0002"),
    ("S0001", "T0003"),
    ("S0002", "T0001"),
)
GAP_TURNS: tuple[tuple[str, str], ...] = (("S0001", "T0003"),)
COMMITTED_TURNS: tuple[tuple[str, str], ...] = (
    ("S0001", "T0001"),
    ("S0001", "T0002"),
    ("S0002", "T0001"),
)


def copy_basic_project_workspace(tmp_path: Path) -> Path:
    """Copy the post-extraction project-synthesis fixture into a writable test directory."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE_ROOT / "workspace", workspace)
    return workspace


def synthesis_path(workspace_path: Path) -> Path:
    return workspace_path / "projects" / PROJECT_KEY / "project-synthesis.json"


def load_project_synthesis(workspace_path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(synthesis_path(workspace_path).read_text(encoding="utf-8")))


def project_synthesis_text(workspace_path: Path) -> str:
    return synthesis_path(workspace_path).read_text(encoding="utf-8")


def deep_copy_json(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)
```

- [ ] **Step 3: Write the failing card-reader tests.**

Create `tests/generate/project_synthesis/test_cards.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from tests.support.project_synthesis import (
    COMMITTED_TURNS,
    PROJECT_KEY,
    copy_basic_project_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_load_returns_committed_chains_in_index_then_card_order(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    chains = load_committed_chains(workspace, PROJECT_KEY)

    assert [(chain.session_ref, chain.turn_ref) for chain in chains] == [
        ("S0001", "T0001"),
        ("S0001", "T0002"),
        ("S0002", "T0001"),
    ]


def test_load_skips_the_gap_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    keys = committed_turn_keys(load_committed_chains(workspace, PROJECT_KEY))

    assert keys == set(COMMITTED_TURNS)
    assert ("S0001", "T0003") not in keys


def test_committed_chain_carries_trimmed_fields_and_verbatim_quotes(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    chains = load_committed_chains(workspace, PROJECT_KEY)
    first = chains[0]

    assert first.materiality == "material"
    assert "simplify" in first.trigger_summary
    assert first.reaction_summaries == ("Updated the MCP tools page, evidence contract, and extractor prompt.",)
    assert first.outcomes[0].category == "document_outcome"
    assert first.terminal_type == "material_result"
    assert first.quoted_messages == (
        {"text": "Please simplify the MCP evidence tools and drop chain_ref.", "citations": [{"lines": "2-2"}]},
    )


def test_load_tolerates_a_missing_card(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    (workspace / "projects" / PROJECT_KEY / "evidence" / "S0002.json").unlink()

    keys = committed_turn_keys(load_committed_chains(workspace, PROJECT_KEY))

    assert keys == {("S0001", "T0001"), ("S0001", "T0002")}


def test_load_returns_empty_for_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    assert load_committed_chains(workspace, "Missing-000000000000") == ()
```

- [ ] **Step 4: Run; verify failure (ModuleNotFoundError for `cards`).**

Run: `uv run pytest tests/generate/project_synthesis/test_cards.py -q`
Expected: FAIL — `No module named 'prompt_diary.generate.project_synthesis.cards'`.

- [ ] **Step 5: Implement `cards.py`.**

Create `src/prompt_diary/generate/project_synthesis/cards.py`:

```python
"""Read one project's committed evidence cards into typed chains.

Project synthesis consumes the evidence cards produced by extraction. This module reads every
``projects/<project_key>/evidence/<session_ref>.json`` card for the project and returns its committed
chains as typed ``CommittedChain`` values, in session-index order then card order. Both the prompt
paste builder and the ``write_work_item`` API read cards through here, so the committed-turn universe
and the pasted summaries always agree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import PreparedProject, PreparedWorkspace


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
    terminal_type: str
    terminal_summary: str
    quoted_messages: tuple[dict[str, Any], ...]


def load_committed_chains(workspace_path: Path, project_key: str) -> tuple[CommittedChain, ...]:
    """Return the project's committed chains in (session index order, card order)."""
    workspace = load_prepared_workspace(workspace_path)
    project = _find_project(workspace, project_key)
    if project is None:
        return ()
    chains: list[CommittedChain] = []
    for session in project.sessions:
        card_path = (
            workspace_path / "projects" / project_key / "evidence" / f"{session.session_ref}.json"
        )
        for raw in _card_chains(card_path):
            chains.append(_committed_chain(session.session_ref, raw))
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


def _committed_chain(session_ref: str, raw: dict[str, Any]) -> CommittedChain:
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
        terminal_type=_as_str(terminal.get("type")),
        terminal_summary=_as_str(terminal.get("summary")),
        quoted_messages=tuple(
            cast("dict[str, Any]", item)
            for item in _as_list(trigger.get("quoted_messages"))
            if isinstance(item, dict)
        ),
    )


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""
```

- [ ] **Step 6: Run the card tests; verify pass. Then gates.**

Run: `uv run pytest tests/generate/project_synthesis/test_cards.py -q`
Expected: PASS.
Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: clean.

- [ ] **Step 7: Commit.**

```bash
git add src/prompt_diary/generate/project_synthesis/cards.py tests/fixtures/project-synthesis tests/support/project_synthesis.py tests/generate/project_synthesis/test_cards.py
git commit -m "feat(project-synthesis): per-project evidence-card reader and fixture"
```

## Task 3: `write_work_item` API (workspace validation, envelope IO, `source_user_messages`)

**Files:**
- Create: `src/prompt_diary/generate/project_synthesis/mcp.py`
- Modify: `tests/support/project_synthesis.py` (add API call helper, `result_to_dict`, assert helpers)
- Test: `tests/generate/project_synthesis/test_write_api.py`

**What it does:** `write_work_item(*, workspace_path, project_key, work_item)` parses the work item (chain-only), then runs workspace-dependent checks — project exists, every `covered_turns` is a real indexed turn, kind-vs-committed-chain coverage (`evidence_gap_item` ↔ gap turns; other kinds ↔ committed turns), coverage exclusivity across calls, `work_item_ref` uniqueness, and `evidence_refs` ⊆ this item's covered turns that have a committed chain. On success it appends to `project-synthesis.json` (creating the envelope and populating `source_user_messages` on first write), writes atomically, and returns the `uncovered_turns`.

- [ ] **Step 1: Extend the support module with the API call + result helpers.**

Add to `tests/support/project_synthesis.py` imports:

```python
from collections.abc import Mapping

import pytest

from prompt_diary.generate.project_synthesis.mcp import (
    WriteWorkItemAppendedResult,
    WriteWorkItemInvalidResult,
    WriteWorkItemResult,
    write_work_item,
)
```

Add these functions to `tests/support/project_synthesis.py`:

```python
def call_write_work_item_api(
    *,
    workspace_path: Path,
    project_key: str = PROJECT_KEY,
    work_item: dict[str, Any] | None = None,
) -> WriteWorkItemResult:
    return write_work_item(
        workspace_path=workspace_path,
        project_key=project_key,
        work_item=valid_material_work_item() if work_item is None else work_item,
    )


def result_to_dict(result: object) -> dict[str, Any]:
    if isinstance(result, WriteWorkItemAppendedResult):
        return {
            "status": result.status,
            "project_key": result.project_key,
            "work_item_ref": result.work_item_ref,
            "uncovered_turns": [
                {"session_ref": ref.session_ref, "turn_ref": ref.turn_ref}
                for ref in result.uncovered_turns
            ],
        }
    if isinstance(result, WriteWorkItemInvalidResult):
        return {
            "status": result.status,
            "errors": [
                {"path": error.path, "message": error.message, "hint": error.hint}
                for error in result.errors
            ],
        }
    if isinstance(result, Mapping):
        return dict(cast("Mapping[str, Any]", result))
    pytest.fail(f"result must be a write work item result or mapping, got {type(result)!r}")


def assert_appended_result(
    result: object, *, work_item_ref: str, uncovered: list[tuple[str, str]]
) -> None:
    payload = result_to_dict(result)
    assert payload["status"] == "appended"
    assert payload["project_key"] == PROJECT_KEY
    assert payload["work_item_ref"] == work_item_ref
    assert payload["uncovered_turns"] == [
        {"session_ref": session_ref, "turn_ref": turn} for session_ref, turn in uncovered
    ]


def assert_invalid_result(
    result: object,
    *,
    path: str,
    message_contains: str | None = None,
    hint_contains: str | None = None,
) -> None:
    payload = result_to_dict(result)
    assert payload["status"] == "invalid"
    errors_obj = payload["errors"]
    assert isinstance(errors_obj, list)
    matching: list[Mapping[str, Any]] = []
    for error_obj in cast("list[object]", errors_obj):
        if isinstance(error_obj, Mapping):
            error = cast("Mapping[str, Any]", error_obj)
            if error.get("path") == path:
                matching.append(error)
    assert matching, f"expected an invalid error at path {path!r}: {errors_obj!r}"
    error = matching[0]
    message = error.get("message")
    hint = error.get("hint")
    assert isinstance(message, str) and message
    assert isinstance(hint, str) and hint
    if message_contains is not None:
        assert message_contains in message
    if hint_contains is not None:
        assert hint_contains in hint
```

- [ ] **Step 2: Write the failing write-API tests.**

Create `tests/generate/project_synthesis/test_write_api.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from tests.support.project_synthesis import (
    PROJECT_KEY,
    assert_appended_result,
    assert_invalid_result,
    call_write_work_item_api,
    copy_basic_project_workspace,
    deep_copy_json,
    load_project_synthesis,
    project_synthesis_text,
    turn_ref,
    valid_evidence_gap_work_item,
    valid_material_work_item,
    valid_no_material_work_item,
    work_item_with_value,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_first_write_creates_envelope_and_populates_source_user_messages(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())

    assert_appended_result(
        result, work_item_ref="W0001", uncovered=[("S0001", "T0003"), ("S0002", "T0001")]
    )
    envelope = load_project_synthesis(workspace)
    assert envelope["schema_version"] == 1
    assert envelope["project_key"] == PROJECT_KEY
    assert envelope["project_label"] == "ReportGenerator"
    assert [item["work_item_ref"] for item in envelope["work_items"]] == ["W0001"]
    messages = envelope["source_user_messages"]
    assert [(entry["session_ref"], entry["turn_ref"]) for entry in messages] == [
        ("S0001", "T0001"),
        ("S0001", "T0002"),
        ("S0002", "T0001"),
    ]
    assert messages[0]["quoted_messages"][0]["text"].startswith("Please simplify")
    assert messages[0]["quoted_messages"][0]["citations"] == [{"lines": "2-2"}]


def test_appends_second_work_item_without_modifying_first(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    first = deep_copy_json(load_project_synthesis(workspace)["work_items"][0])
    messages_before = deep_copy_json({"m": load_project_synthesis(workspace)["source_user_messages"]})

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=valid_no_material_work_item()
    )

    assert_appended_result(result, work_item_ref="W0002", uncovered=[("S0001", "T0003")])
    envelope = load_project_synthesis(workspace)
    assert [item["work_item_ref"] for item in envelope["work_items"]] == ["W0001", "W0002"]
    assert envelope["work_items"][0] == first
    assert {"m": envelope["source_user_messages"]} == messages_before


def test_full_coverage_returns_empty_uncovered(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    call_write_work_item_api(workspace_path=workspace, work_item=valid_no_material_work_item())

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=valid_evidence_gap_work_item()
    )

    assert_appended_result(result, work_item_ref="W0003", uncovered=[])


def test_rejects_duplicate_work_item_ref(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    before = project_synthesis_text(workspace)
    duplicate = valid_no_material_work_item()
    duplicate["work_item_ref"] = "W0001"

    result = call_write_work_item_api(workspace_path=workspace, work_item=duplicate)

    assert_invalid_result(
        result, path="work_item.work_item_ref", message_contains="W0001", hint_contains="unique"
    )
    assert project_synthesis_text(workspace) == before


def test_rejects_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = call_write_work_item_api(
        workspace_path=workspace,
        project_key="Missing-000000000000",
        work_item=valid_material_work_item(),
    )

    assert_invalid_result(result, path="project_key")


def test_rejects_unknown_covered_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    item = valid_no_material_work_item()
    item["covered_turns"] = [turn_ref("S0009", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=item)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="indexed",
        hint_contains="sessions.index",
    )


def test_rejects_coverage_exclusivity_violation(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    before = project_synthesis_text(workspace)
    clash = valid_no_material_work_item()
    clash["covered_turns"] = [turn_ref("S0001", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=clash)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="already",
        hint_contains="exactly one",
    )
    assert project_synthesis_text(workspace) == before


def test_rejects_evidence_gap_item_covering_committed_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    gap = valid_evidence_gap_work_item()
    gap["covered_turns"] = [turn_ref("S0001", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=gap)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="evidence chain",
        hint_contains="evidence_gap_item",
    )


def test_rejects_non_gap_item_covering_gap_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    item = valid_no_material_work_item()
    item["covered_turns"] = [turn_ref("S0001", "T0003")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=item)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="no committed evidence chain",
        hint_contains="evidence_gap_item",
    )


def test_rejects_evidence_ref_not_in_covered_turns(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    item = valid_material_work_item()
    item["outcomes"][0]["evidence_refs"] = [turn_ref("S0002", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=item)

    assert_invalid_result(
        result,
        path="work_item.outcomes[0].evidence_refs[0]",
        message_contains="covered",
        hint_contains="covered_turns",
    )


def test_rejects_evidence_gap_item_that_cites_its_gap_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    gap = valid_evidence_gap_work_item()
    gap["terminal_states"] = [
        {
            "type": "evidence_gap",
            "summary": "No content was extractable.",
            "evidence_refs": [turn_ref("S0001", "T0003")],
        }
    ]

    result = call_write_work_item_api(workspace_path=workspace, work_item=gap)

    assert_invalid_result(
        result,
        path="work_item.terminal_states[0].evidence_refs[0]",
        message_contains="no committed evidence chain",
        hint_contains="cannot be cited",
    )


def test_rejected_write_leaves_envelope_unchanged(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    before = project_synthesis_text(workspace)
    bad = valid_no_material_work_item()
    bad["covered_turns"] = [turn_ref("S0001", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=bad)

    assert_invalid_result(result, path="work_item.covered_turns[0]")
    assert project_synthesis_text(workspace) == before


def test_rejects_structurally_invalid_without_workspace_checks(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=work_item_with_value(("kind",), "material")
    )

    assert_invalid_result(result, path="work_item.kind")
```

- [ ] **Step 3: Run; verify failure (ModuleNotFoundError for `mcp`).**

Run: `uv run pytest tests/generate/project_synthesis/test_write_api.py -q`
Expected: FAIL — `No module named 'prompt_diary.generate.project_synthesis.mcp'`.

- [ ] **Step 4: Implement `mcp.py`.**

Create `src/prompt_diary/generate/project_synthesis/mcp.py`:

```python
"""Transport-independent project synthesis MCP tool APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from prompt_diary.generate.project_synthesis.model import (
    InvalidWorkItem,
    TurnReference,
    WorkItem,
    WorkItemWriteError,
    new_project_synthesis_envelope,
    parse_work_item,
    work_item_to_json,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from prompt_diary.generate.project_synthesis.cards import CommittedChain
    from prompt_diary.generate.workspace import PreparedProject, PreparedWorkspace

__all__ = [
    "WorkItemWriteError",
    "WriteWorkItemAppendedResult",
    "WriteWorkItemInvalidResult",
    "WriteWorkItemResult",
    "write_work_item",
]


@dataclass(frozen=True)
class WriteWorkItemAppendedResult:
    """Successful work-item write result."""

    status: Literal["appended"]
    project_key: str
    work_item_ref: str
    uncovered_turns: tuple[TurnReference, ...]


@dataclass(frozen=True)
class WriteWorkItemInvalidResult:
    """Rejected work-item write result."""

    status: Literal["invalid"]
    errors: tuple[WorkItemWriteError, ...]


WriteWorkItemResult: TypeAlias = WriteWorkItemAppendedResult | WriteWorkItemInvalidResult


def write_work_item(
    *,
    workspace_path: Path,
    project_key: str,
    work_item: dict[str, Any],
) -> WriteWorkItemResult:
    """Validate and append one work item to the project synthesis envelope.

    Chain-only structure is parsed first; workspace-dependent checks (project, indexed turns, kind
    vs. committed-chain coverage, coverage exclusivity, and evidence references) run against the
    prepared workspace and the existing envelope. A rejected write returns structured errors and
    never touches the canonical envelope file. The first accepted write creates the envelope and
    populates ``source_user_messages`` verbatim from the committed cards.
    """
    parsed = parse_work_item(work_item)
    if isinstance(parsed, InvalidWorkItem):
        return WriteWorkItemInvalidResult("invalid", parsed.errors)
    item = parsed.work_item

    workspace = load_prepared_workspace(workspace_path)
    project = _find_project(workspace, project_key)
    if project is None:
        return _invalid("project_key", _unknown_project_message(project_key), _UNKNOWN_PROJECT_HINT)

    universe = _indexed_turn_universe(project)
    chains = load_committed_chains(workspace_path, project_key)
    committed = committed_turn_keys(chains)
    envelope_path = _envelope_path(workspace_path, project_key)
    envelope = _read_envelope(envelope_path)
    existing_items = _existing_work_items(envelope)

    errors = _validate_against_workspace(
        item,
        universe=frozenset((ref.session_ref, ref.turn_ref) for ref in universe),
        committed=committed,
        already_covered=_covered_keys(existing_items),
        existing_refs=frozenset(_as_str(row.get("work_item_ref")) for row in existing_items),
    )
    if errors:
        return WriteWorkItemInvalidResult("invalid", tuple(errors))

    committed_envelope = _commit(
        envelope_path=envelope_path,
        envelope=envelope,
        project=project,
        item=item,
        chains=chains,
    )
    uncovered = _uncovered(universe, _covered_keys(_existing_work_items(committed_envelope)))
    return WriteWorkItemAppendedResult("appended", project_key, item.work_item_ref, uncovered)


def _validate_against_workspace(
    item: WorkItem,
    *,
    universe: frozenset[tuple[str, str]],
    committed: frozenset[tuple[str, str]],
    already_covered: frozenset[tuple[str, str]],
    existing_refs: frozenset[str],
) -> list[WorkItemWriteError]:
    errors: list[WorkItemWriteError] = []
    if item.work_item_ref in existing_refs:
        errors.append(
            WorkItemWriteError(
                "work_item.work_item_ref",
                _duplicate_ref_message(item.work_item_ref),
                _DUPLICATE_REF_HINT,
            )
        )
    is_gap = item.kind == "evidence_gap_item"
    covered_here: set[tuple[str, str]] = set()
    for index, ref in enumerate(item.covered_turns):
        key = (ref.session_ref, ref.turn_ref)
        path = f"work_item.covered_turns[{index}]"
        if key not in universe:
            errors.append(WorkItemWriteError(path, _unknown_turn_message(ref), _UNKNOWN_TURN_HINT))
            continue
        if key in already_covered or key in covered_here:
            errors.append(
                WorkItemWriteError(path, _already_covered_message(ref), _EXCLUSIVITY_HINT)
            )
        covered_here.add(key)
        has_chain = key in committed
        if is_gap and has_chain:
            errors.append(WorkItemWriteError(path, _gap_with_chain_message(ref), _GAP_HINT))
        if not is_gap and not has_chain:
            errors.append(WorkItemWriteError(path, _nongap_without_chain_message(ref), _NONGAP_HINT))
    _validate_evidence_refs(item, covered_here, committed, errors)
    return errors


def _validate_evidence_refs(
    item: WorkItem,
    covered_here: set[tuple[str, str]],
    committed: frozenset[tuple[str, str]],
    errors: list[WorkItemWriteError],
) -> None:
    for path, ref in _iter_evidence_refs(item):
        key = (ref.session_ref, ref.turn_ref)
        if key not in covered_here:
            errors.append(WorkItemWriteError(path, _ref_not_covered_message(ref), _REF_COVERED_HINT))
        elif key not in committed:
            errors.append(WorkItemWriteError(path, _ref_no_chain_message(ref), _REF_CHAIN_HINT))


def _iter_evidence_refs(item: WorkItem) -> Iterator[tuple[str, TurnReference]]:
    if item.trigger is not None:
        for index, ref in enumerate(item.trigger.evidence_refs):
            yield f"work_item.trigger.evidence_refs[{index}]", ref
    for outcome_index, outcome in enumerate(item.outcomes):
        for index, ref in enumerate(outcome.evidence_refs):
            yield f"work_item.outcomes[{outcome_index}].evidence_refs[{index}]", ref
    for state_index, state in enumerate(item.terminal_states):
        for index, ref in enumerate(state.evidence_refs):
            yield f"work_item.terminal_states[{state_index}].evidence_refs[{index}]", ref


def _commit(
    *,
    envelope_path: Path,
    envelope: dict[str, Any] | None,
    project: PreparedProject,
    item: WorkItem,
    chains: tuple[CommittedChain, ...],
) -> dict[str, Any]:
    base = envelope
    if base is None:
        base = new_project_synthesis_envelope(project.project_key, project.project_label)
        base["source_user_messages"] = _source_user_messages(chains)
    work_items = [*_existing_work_items(base), work_item_to_json(item)]
    new_envelope = {**base, "work_items": work_items}
    _write_envelope(envelope_path, new_envelope)
    return new_envelope


def _source_user_messages(chains: tuple[CommittedChain, ...]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for chain in sorted(chains, key=lambda item: (item.session_ref, item.turn_ref)):
        if chain.quoted_messages:
            entries.append(
                {
                    "session_ref": chain.session_ref,
                    "turn_ref": chain.turn_ref,
                    "quoted_messages": [dict(message) for message in chain.quoted_messages],
                }
            )
    return entries


def _indexed_turn_universe(project: PreparedProject) -> tuple[TurnReference, ...]:
    return tuple(
        TurnReference(session.session_ref, turn.turn_ref)
        for session in project.sessions
        for turn in session.turns
    )


def _covered_keys(work_items: list[dict[str, Any]]) -> frozenset[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in work_items:
        for ref in _as_list(row.get("covered_turns")):
            mapping = _as_mapping(ref)
            keys.add((_as_str(mapping.get("session_ref")), _as_str(mapping.get("turn_ref"))))
    return frozenset(keys)


def _uncovered(
    universe: tuple[TurnReference, ...], covered: frozenset[tuple[str, str]]
) -> tuple[TurnReference, ...]:
    return tuple(ref for ref in universe if (ref.session_ref, ref.turn_ref) not in covered)


def _existing_work_items(envelope: dict[str, Any] | None) -> list[dict[str, Any]]:
    if envelope is None:
        return []
    items = envelope.get("work_items")
    rows = cast("list[Any]", items) if isinstance(items, list) else []
    return [cast("dict[str, Any]", row) for row in rows if isinstance(row, dict)]


def _find_project(workspace: PreparedWorkspace, project_key: str) -> PreparedProject | None:
    return next((item for item in workspace.projects if item.project_key == project_key), None)


def _envelope_path(workspace_path: Path, project_key: str) -> Path:
    return workspace_path / "projects" / project_key / "project-synthesis.json"


def _read_envelope(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _invalid(path: str, message: str, hint: str) -> WriteWorkItemInvalidResult:
    return WriteWorkItemInvalidResult("invalid", (WorkItemWriteError(path, message, hint),))


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r}"


def _duplicate_ref_message(work_item_ref: str) -> str:
    return f"work_item_ref {work_item_ref!r} is already used in the envelope"


def _unknown_turn_message(ref: TurnReference) -> str:
    return f"covered turn {ref.session_ref}/{ref.turn_ref} is not an indexed turn"


def _already_covered_message(ref: TurnReference) -> str:
    return f"turn {ref.session_ref}/{ref.turn_ref} is already covered by another work item"


def _gap_with_chain_message(ref: TurnReference) -> str:
    return (
        f"evidence_gap_item cannot cover {ref.session_ref}/{ref.turn_ref}, "
        "which has a committed evidence chain"
    )


def _nongap_without_chain_message(ref: TurnReference) -> str:
    return (
        f"{ref.session_ref}/{ref.turn_ref} has no committed evidence chain; "
        "only an evidence_gap_item may cover it"
    )


def _ref_not_covered_message(ref: TurnReference) -> str:
    return f"evidence ref {ref.session_ref}/{ref.turn_ref} must be one of this work item's covered_turns"


def _ref_no_chain_message(ref: TurnReference) -> str:
    return (
        f"evidence ref {ref.session_ref}/{ref.turn_ref} has no committed evidence chain "
        "and cannot be cited"
    )


_UNKNOWN_PROJECT_HINT = "use the project_key from the prepared workspace"
_DUPLICATE_REF_HINT = "each work_item_ref must be unique within the envelope"
_UNKNOWN_TURN_HINT = "cover only turns listed in sessions.index.jsonl"
_EXCLUSIVITY_HINT = "every indexed turn belongs to exactly one work item"
_GAP_HINT = "an evidence_gap_item covers only turns with no committed chain"
_NONGAP_HINT = "cover turns without a chain using an evidence_gap_item"
_REF_COVERED_HINT = "cite only turns in this item's covered_turns"
_REF_CHAIN_HINT = "a turn with no chain cannot be cited"
```

- [ ] **Step 5: Run the write-API tests; verify pass. Then gates.**

Run: `uv run pytest tests/generate/project_synthesis/test_write_api.py -q`
Expected: PASS (all 13).
Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add src/prompt_diary/generate/project_synthesis/mcp.py tests/support/project_synthesis.py tests/generate/project_synthesis/test_write_api.py
git commit -m "feat(project-synthesis): write_work_item validation, envelope IO, source_user_messages"
```

## Task 4: Register `write_work_item` on the MCP server

**Files:**
- Modify: `src/prompt_diary/mcp/server.py`
- Test: `tests/mcp/test_server.py` (append new tests; do not modify existing ones)

**What it does:** Adds the thin `write_work_item` MCP adapter (resolve workspace → call API) and registers it on the server. No `codex_config.py` change is needed — the whole `prompt_diary` server is exposed to the agent via `report mcp serve`.

- [ ] **Step 1: Append the failing MCP-surface tests.**

Add to the imports of `tests/mcp/test_server.py`:

```python
from tests.support.project_synthesis import (
    PROJECT_KEY as PS_PROJECT_KEY,
    assert_appended_result as assert_work_item_appended,
    assert_invalid_result as assert_work_item_invalid,
    call_write_work_item_api,
    copy_basic_project_workspace,
    result_to_dict as work_item_result_to_dict,
    valid_material_work_item,
    work_item_with_value,
)
```

Append these tests to `tests/mcp/test_server.py`:

```python
def test_write_work_item_is_registered_by_mcp_server() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())

    assert "write_work_item" in [tool.name for tool in tools]


def test_write_work_item_mcp_input_shape_contains_contract_fields() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())
    write_tool = next(tool for tool in tools if tool.name == "write_work_item")

    properties = write_tool.inputSchema["properties"]
    assert {"project_key", "work_item"} <= set(properties)
    assert {"project_key", "work_item"} <= set(write_tool.inputSchema["required"])


def test_write_work_item_mcp_success_returns_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    server = mcp_server.build_mcp_server()

    result = asyncio.run(
        _call_mcp_tool(
            server,
            "write_work_item",
            {"project_key": PS_PROJECT_KEY, "work_item": valid_material_work_item()},
        )
    )

    assert_work_item_appended(
        result, work_item_ref="W0001", uncovered=[("S0001", "T0003"), ("S0002", "T0001")]
    )


def test_write_work_item_mcp_invalid_result_matches_api_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_workspace = copy_basic_project_workspace(tmp_path / "api")
    mcp_workspace = copy_basic_project_workspace(tmp_path / "mcp")
    invalid = work_item_with_value(("kind",), "material")
    api_result = call_write_work_item_api(workspace_path=api_workspace, work_item=invalid)
    monkeypatch.chdir(mcp_workspace)
    server = mcp_server.build_mcp_server()

    mcp_result = asyncio.run(
        _call_mcp_tool(
            server,
            "write_work_item",
            {"project_key": PS_PROJECT_KEY, "work_item": invalid},
        )
    )

    assert mcp_result == work_item_result_to_dict(api_result)
    assert_work_item_invalid(mcp_result, path="work_item.kind")


def test_write_work_item_uses_workspace_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = copy_basic_project_workspace(tmp_path / "ws")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROMPT_DIARY_WORKSPACE", str(workspace))

    result = mcp_server.write_work_item(PS_PROJECT_KEY, valid_material_work_item())

    assert work_item_result_to_dict(result)["status"] == "appended"
```

> Note: `_call_mcp_tool` already returns a plain dict (it routes the FastMCP structured result through the evidence `result_to_dict`, whose `Mapping` fall-through passes work-item dicts through unchanged). The work-item assert helpers then re-normalize via the project-synthesis `result_to_dict`. If a future FastMCP version serializes nested `TurnReference` differently, prefer reading the content-block JSON — but the existing `write_evidence` nested-error tests prove the structured path works.

- [ ] **Step 2: Run; verify failure.**

Run: `uv run pytest tests/mcp/test_server.py -q`
Expected: FAIL — `write_work_item` not registered / `mcp_server.write_work_item` missing.

- [ ] **Step 3: Implement the server adapter + registration.**

In `src/prompt_diary/mcp/server.py`, add the import next to the existing evidence import:

```python
from prompt_diary.generate.project_synthesis.mcp import write_work_item as write_work_item_api
```

Add the adapter function after `write_evidence`:

```python
def write_work_item(
    project_key: str,
    work_item: dict[str, object],
) -> object:
    """Validate and append one work item from the resolved prepared workspace."""
    return write_work_item_api(
        workspace_path=_resolve_workspace(),
        project_key=project_key,
        work_item=work_item,
    )
```

Register it in `build_mcp_server` (add the line after `server.tool()(write_evidence)`):

```python
    server.tool()(write_work_item)
```

- [ ] **Step 4: Run the MCP tests; verify pass. Then gates.**

Run: `uv run pytest tests/mcp/test_server.py -q`
Expected: PASS (existing + new).
Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit.**

```bash
git add src/prompt_diary/mcp/server.py tests/mcp/test_server.py
git commit -m "feat(project-synthesis): register write_work_item MCP tool"
```

**Part 1 complete:** the `write_work_item` tool is fully implemented, validated, and exposed over MCP.

---

# Part 2 — The project synthesis pipeline (runner + integration)

## Task 5: The trimmed evidence-chain paste (`inputs.py`)

**Files:**
- Create: `src/prompt_diary/generate/project_synthesis/inputs.py`
- Test: `tests/generate/project_synthesis/test_inputs.py`

**What it does:** `build_project_synthesis_inputs(*, workspace_path, project_key)` returns the three values the prompt needs: `project_key`, the normalized `project_json`, and `evidence_chains` — the committed chains rendered into the session-grouped, trimmed paste (summaries only; no citations, no quoted text). The paste format matches `tests/generate/test_prompts.py::test_project_synthesizer_prompt`.

- [ ] **Step 1: Write the failing inputs/paste tests.**

Create `tests/generate/project_synthesis/test_inputs.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.generate.project_synthesis.cards import load_committed_chains
from prompt_diary.generate.project_synthesis.inputs import (
    build_project_synthesis_inputs,
    render_evidence_chains,
)
from tests.support.project_synthesis import PROJECT_KEY, copy_basic_project_workspace

if TYPE_CHECKING:
    from pathlib import Path


def test_inputs_expose_project_key_and_normalized_project_json(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    inputs = build_project_synthesis_inputs(workspace_path=workspace, project_key=PROJECT_KEY)

    assert inputs.project_key == PROJECT_KEY
    assert '"project_label": "ReportGenerator"' in inputs.project_json


def test_paste_groups_by_session_with_labelled_turns(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    inputs = build_project_synthesis_inputs(workspace_path=workspace, project_key=PROJECT_KEY)
    paste = inputs.evidence_chains

    assert "#### Session S0001 (2 chains)" in paste
    assert "#### Session S0002 (1 chain)" in paste
    assert "**S0001/T0001** [material]" in paste
    assert "**S0001/T0002** [minor]" in paste
    assert "**S0002/T0001** [material]" in paste
    # The gap turn S0001/T0003 has no committed chain, so it never appears in the paste.
    assert "T0003" not in paste


def test_paste_is_trimmed_to_summaries(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    paste = build_project_synthesis_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY
    ).evidence_chains

    assert "trigger: User asked to simplify the MCP evidence tools" in paste
    assert "reaction: Updated the MCP tools page" in paste
    assert "- document_outcome: Top-level turn_ref adopted" in paste
    assert "terminal: material_result: Extraction surface updated" in paste
    # No citations or quoted message text leak into the paste.
    assert "lines" not in paste
    assert "Please simplify the MCP evidence tools and drop chain_ref." not in paste


def test_paste_omits_empty_reaction_and_outcomes(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    chains = load_committed_chains(workspace, PROJECT_KEY)
    minor = next(chain for chain in chains if chain.turn_ref == "T0002")

    block = render_evidence_chains((minor,))

    assert "**S0001/T0002** [minor]" in block
    assert "terminal: clarification_only:" in block
    assert "outcomes:" not in block


def test_render_empty_when_no_committed_chains() -> None:
    assert render_evidence_chains(()) == "(No extracted evidence chains for this project.)"
```

- [ ] **Step 2: Run; verify failure.**

Run: `uv run pytest tests/generate/project_synthesis/test_inputs.py -q`
Expected: FAIL — `No module named 'prompt_diary.generate.project_synthesis.inputs'`.

- [ ] **Step 3: Implement `inputs.py`.**

Create `src/prompt_diary/generate/project_synthesis/inputs.py`:

```python
"""Build the project synthesizer prompt inputs for one project.

The synthesizer agent has no file access: it works only from the chains pasted into its prompt. This
module reads the project's committed evidence chains and renders them, trimmed to summaries, grouped
by session under a ``#### Session <session_ref>`` heading with each chain labelled
``<session_ref>/<turn_ref>``. Citations and quoted message text are dropped — the agent references
turns and the summaries are sufficient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.project_synthesis.cards import load_committed_chains
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.project_synthesis.cards import CommittedChain
    from prompt_diary.generate.workspace import PreparedWorkspace

_EMPTY_PASTE = "(No extracted evidence chains for this project.)"


@dataclass(frozen=True)
class ProjectSynthesisInputs:
    """Rendered-ready inputs for synthesizing one project's work items."""

    project_key: str
    project_json: str
    evidence_chains: str


def build_project_synthesis_inputs(
    *,
    workspace_path: Path,
    project_key: str,
) -> ProjectSynthesisInputs:
    """Build prompt inputs for one project from the prepared workspace."""
    workspace = load_prepared_workspace(workspace_path)
    _require_project(workspace, project_key)
    project_dir = workspace_path / "projects" / project_key
    return ProjectSynthesisInputs(
        project_key=project_key,
        project_json=_normalized_json(project_dir / "project.json"),
        evidence_chains=render_evidence_chains(load_committed_chains(workspace_path, project_key)),
    )


def render_evidence_chains(chains: tuple[CommittedChain, ...]) -> str:
    """Render committed chains into the session-grouped, trimmed paste."""
    if not chains:
        return _EMPTY_PASTE
    sections: list[str] = []
    for session_ref in _session_order(chains):
        session_chains = tuple(chain for chain in chains if chain.session_ref == session_ref)
        sections.append(_render_session(session_ref, session_chains))
    return "\n\n".join(sections)


def _render_session(session_ref: str, chains: tuple[CommittedChain, ...]) -> str:
    heading = f"#### Session {session_ref} ({_count_label(len(chains))})"
    blocks = [_render_chain(chain) for chain in chains]
    return heading + "\n\n" + "\n\n".join(blocks)


def _render_chain(chain: CommittedChain) -> str:
    lines = [
        f"**{chain.session_ref}/{chain.turn_ref}** [{chain.materiality}]",
        f"trigger: {chain.trigger_summary}",
    ]
    if chain.reaction_summaries:
        lines.append(f"reaction: {' '.join(chain.reaction_summaries)}")
    if chain.outcomes:
        lines.append("outcomes:")
        lines.extend(f"- {outcome.category}: {outcome.summary}" for outcome in chain.outcomes)
    lines.append(f"terminal: {chain.terminal_type}: {chain.terminal_summary}")
    return "\n".join(lines)


def _session_order(chains: tuple[CommittedChain, ...]) -> tuple[str, ...]:
    order: list[str] = []
    for chain in chains:
        if chain.session_ref not in order:
            order.append(chain.session_ref)
    return tuple(order)


def _count_label(count: int) -> str:
    return f"{count} chain" if count == 1 else f"{count} chains"


def _require_project(workspace: PreparedWorkspace, project_key: str) -> None:
    if not any(item.project_key == project_key for item in workspace.projects):
        raise PromptDiaryError(_unknown_project_message(project_key))


def _normalized_json(path: Path) -> str:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(raw, indent=2, ensure_ascii=False)


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"
```

- [ ] **Step 4: Run the inputs tests; verify pass. Then gates.**

Run: `uv run pytest tests/generate/project_synthesis/test_inputs.py -q`
Expected: PASS.
Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit.**

```bash
git add src/prompt_diary/generate/project_synthesis/inputs.py tests/generate/project_synthesis/test_inputs.py
git commit -m "feat(project-synthesis): trimmed session-grouped evidence-chain paste"
```

## Task 6: The phase runner + mocked-agent test layer

**Files:**
- Modify (replace stub): `src/prompt_diary/generate/project_synthesis/runner.py`
- Create: `tests/support/project_synthesis_agent.py` (the grouping fake agent)
- Test: `tests/generate/project_synthesis/test_runner.py`

**What it does:** `ProjectSynthesisRunner.run` builds the inputs, resets `project-synthesis.json`, computes the indexed-turn universe, runs **one** agent turn (the agent self-loops on `write_work_item`), then verifies coverage: empty uncovered → `success`, otherwise `failed`. Zero-turn projects short-circuit to an empty envelope without invoking the agent. The fake agent (`GroupingAgentSessionFactory`) reads the pasted chains, writes one material work item per session via the **real** `write_work_item` API, then buckets the uncovered gap turns into an `evidence_gap_item` — exercising the real tool, the `uncovered_turns` loop, and `source_user_messages` population.

- [ ] **Step 1: Write the grouping fake agent.**

Create `tests/support/project_synthesis_agent.py`:

```python
"""Prompt-reading fake agent that synthesizes work items via the real write_work_item API."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prompt_diary.agent import AgentTurnResult
from prompt_diary.generate.project_synthesis.mcp import (
    WriteWorkItemAppendedResult,
    WriteWorkItemResult,
    write_work_item,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prompt_diary.agent import AgentConfig

_PROJECT_KEY_RE = re.compile(r"^- Project key: (.+)$", re.MULTILINE)
_CHAIN_RE = re.compile(r"^\*\*(S\d{4})/(T\d{4})\*\* \[", re.MULTILINE)


@dataclass
class _RefCounter:
    value: int = 0

    def next(self) -> str:
        self.value += 1
        return f"W{self.value:04d}"


@dataclass
class GroupingAgentRunner:
    """One fake conversation that groups committed turns and buckets gap turns."""

    config: AgentConfig
    cover_gaps: bool
    processed: list[str]
    prompts: list[str] = field(default_factory=list)

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del timeout_seconds, output_schema
        self.prompts.append(prompt)
        project_key = _require_project_key(prompt)
        counter = _RefCounter()
        uncovered = self._cover_committed(project_key, _committed_by_session(prompt), counter)
        if self.cover_gaps:
            self._cover_gaps(project_key, uncovered, counter)
        return AgentTurnResult(assistant_text="synthesized", events=())

    def _cover_committed(
        self,
        project_key: str,
        grouped: tuple[tuple[str, tuple[str, ...]], ...],
        counter: _RefCounter,
    ) -> tuple[tuple[str, str], ...]:
        uncovered: tuple[tuple[str, str], ...] = ()
        for session_ref, turn_refs in grouped:
            item = _material_work_item(counter.next(), session_ref, turn_refs)
            result = self._write(project_key, item)
            uncovered = _uncovered_of(result)
        return uncovered

    def _cover_gaps(
        self,
        project_key: str,
        uncovered: tuple[tuple[str, str], ...],
        counter: _RefCounter,
    ) -> None:
        remaining = uncovered
        while remaining:
            result = self._write(project_key, _gap_work_item(counter.next(), remaining))
            remaining = _uncovered_of(result)

    def _write(self, project_key: str, work_item: dict[str, Any]) -> WriteWorkItemResult:
        result = write_work_item(
            workspace_path=self.config.working_directory,
            project_key=project_key,
            work_item=work_item,
        )
        self.processed.append(str(work_item["work_item_ref"]))
        return result


@dataclass
class GroupingAgentSessionFactory:
    """Mints grouping fake runners off a shared record; never starts Codex."""

    cover_gaps: bool = True
    entered: int = 0
    exited: int = 0
    processed: list[str] = field(default_factory=list)
    runners: list[GroupingAgentRunner] = field(default_factory=list)

    async def __aenter__(self) -> GroupingAgentSessionFactory:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None:
        del exc_type, exc, traceback
        self.exited += 1

    async def runner(self, config: AgentConfig) -> GroupingAgentRunner:
        new_runner = GroupingAgentRunner(
            config=config, cover_gaps=self.cover_gaps, processed=self.processed
        )
        self.runners.append(new_runner)
        return new_runner


def _committed_by_session(prompt: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    order: list[str] = []
    turns: dict[str, list[str]] = {}
    for session_ref, turn in _CHAIN_RE.findall(prompt):
        if session_ref not in turns:
            turns[session_ref] = []
            order.append(session_ref)
        turns[session_ref].append(turn)
    return tuple((session_ref, tuple(turns[session_ref])) for session_ref in order)


def _material_work_item(
    work_item_ref: str, session_ref: str, turn_refs: tuple[str, ...]
) -> dict[str, Any]:
    cite = [{"session_ref": session_ref, "turn_ref": turn_refs[0]}]
    return {
        "work_item_ref": work_item_ref,
        "kind": "material_work_item",
        "title": f"Work thread in {session_ref}",
        "covered_turns": [{"session_ref": session_ref, "turn_ref": turn} for turn in turn_refs],
        "trigger": {"summary": f"User drove work in {session_ref}.", "evidence_refs": cite},
        "agent_reaction": {"summary": "Agent acted across the thread.", "main_actions": ["work"]},
        "outcomes": [
            {
                "category": "process_outcome",
                "summary": "Thread progressed.",
                "evidence_refs": cite,
                "confidence": "medium",
            }
        ],
        "terminal_states": [
            {"type": "material_result", "summary": "Thread concluded.", "evidence_refs": cite}
        ],
        "limits": [],
        "confidence": "medium",
    }


def _gap_work_item(work_item_ref: str, uncovered: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {
        "work_item_ref": work_item_ref,
        "kind": "evidence_gap_item",
        "title": "Indexed turns with no extractable evidence",
        "covered_turns": [
            {"session_ref": session_ref, "turn_ref": turn} for session_ref, turn in uncovered
        ],
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def _uncovered_of(result: WriteWorkItemResult) -> tuple[tuple[str, str], ...]:
    if isinstance(result, WriteWorkItemAppendedResult):
        return tuple((ref.session_ref, ref.turn_ref) for ref in result.uncovered_turns)
    raise AssertionError(f"fake agent write_work_item was rejected: {result!r}")


def _require_project_key(prompt: str) -> str:
    match = _PROJECT_KEY_RE.search(prompt)
    if match is None:
        raise AssertionError("fake agent could not find the project key in the prompt")
    return match.group(1).strip()
```

- [ ] **Step 2: Write the failing runner tests.**

Create `tests/generate/project_synthesis/test_runner.py`:

```python
from __future__ import annotations

import asyncio
import json
import shutil
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.pipeline import (
    TaskSpec,
    project_synthesis_artifact,
    project_synthesis_task_id,
)
from prompt_diary.generate.project_synthesis.runner import ProjectSynthesisRunner
from tests.support.project_synthesis import (
    ALL_TURNS,
    COMMITTED_TURNS,
    PROJECT_KEY,
    copy_basic_project_workspace,
    load_project_synthesis,
    synthesis_path,
)
from tests.support.project_synthesis_agent import GroupingAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult


def _task() -> TaskSpec:
    return TaskSpec(
        task_id=project_synthesis_task_id(PROJECT_KEY),
        kind="project_synthesis",
        project_key=PROJECT_KEY,
        output_artifacts=(project_synthesis_artifact(PROJECT_KEY),),
    )


def _run(factory: GroupingAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = ProjectSynthesisRunner(agent_factory=factory)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(workspace_path=workspace, task=_task())

    return asyncio.run(run())


def test_runner_covers_every_turn_and_writes_envelope(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    factory = GroupingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 1
    envelope = load_project_synthesis(workspace)
    covered = {
        (ref["session_ref"], ref["turn_ref"])
        for item in envelope["work_items"]
        for ref in item["covered_turns"]
    }
    assert covered == set(ALL_TURNS)


def test_runner_populates_source_user_messages(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    _run(GroupingAgentSessionFactory(), workspace)

    messages = load_project_synthesis(workspace)["source_user_messages"]
    assert [(entry["session_ref"], entry["turn_ref"]) for entry in messages] == list(COMMITTED_TURNS)


def test_runner_buckets_gap_turn_as_evidence_gap_item(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    _run(GroupingAgentSessionFactory(), workspace)

    envelope = load_project_synthesis(workspace)
    gap_covered = {
        (ref["session_ref"], ref["turn_ref"])
        for item in envelope["work_items"]
        if item["kind"] == "evidence_gap_item"
        for ref in item["covered_turns"]
    }
    assert ("S0001", "T0003") in gap_covered


def test_runner_resets_a_preexisting_envelope(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    path = synthesis_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_key": PROJECT_KEY,
                "project_label": "ReportGenerator",
                "work_items": [
                    {
                        "work_item_ref": "W9999",
                        "kind": "material_work_item",
                        "title": "stale",
                        "covered_turns": [],
                        "confidence": "low",
                    }
                ],
                "source_user_messages": [],
            }
        ),
        encoding="utf-8",
    )

    result = _run(GroupingAgentSessionFactory(), workspace)

    assert result.status == "success"
    refs = [item["work_item_ref"] for item in load_project_synthesis(workspace)["work_items"]]
    assert "W9999" not in refs


def test_runner_fails_when_a_turn_is_left_uncovered(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = _run(GroupingAgentSessionFactory(cover_gaps=False), workspace)

    assert result.status == "failed"
    assert any("S0001/T0003" in error for error in result.errors)


def test_runner_writes_empty_envelope_for_zero_turn_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    _strip_turns_from_index(workspace)
    shutil.rmtree(workspace / "projects" / PROJECT_KEY / "evidence")
    factory = GroupingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert factory.runners == []
    envelope = load_project_synthesis(workspace)
    assert envelope["work_items"] == []
    assert envelope["source_user_messages"] == []


def test_runner_requires_project_scope(tmp_path: Path) -> None:
    runner = ProjectSynthesisRunner(agent_factory=GroupingAgentSessionFactory())
    task = TaskSpec(task_id="project:x", kind="project_synthesis")

    async def run() -> None:
        await runner.run(workspace_path=tmp_path, task=task)

    with pytest.raises(PromptDiaryError, match="requires project_key"):
        asyncio.run(run())


def _strip_turns_from_index(workspace: Path) -> None:
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        row["turns"] = []
    index_path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")
```

- [ ] **Step 3: Run; verify failure (the stub raises "not implemented yet").**

Run: `uv run pytest tests/generate/project_synthesis/test_runner.py -q`
Expected: FAIL — runner raises `project synthesis phase runner is not implemented yet` (and the import of `GroupingAgentSessionFactory` requires Step 1 to exist first).

- [ ] **Step 4: Replace the stub `runner.py`.**

Overwrite `src/prompt_diary/generate/project_synthesis/runner.py`:

```python
"""Project synthesis phase runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentConfig
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.pipeline import TaskResult, project_synthesis_artifact
from prompt_diary.generate.project_synthesis.inputs import build_project_synthesis_inputs
from prompt_diary.generate.project_synthesis.model import (
    TurnReference,
    new_project_synthesis_envelope,
)
from prompt_diary.generate.prompts import project_synthesizer_prompt
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskSpec
    from prompt_diary.generate.workspace import PreparedProject
    from prompt_diary.progress.reporter import ProgressReporter


@dataclass(frozen=True)
class ProjectSynthesisRunner:
    """Drive an agent to group one project's evidence chains into work items."""

    agent_factory: AgentSessionFactory

    async def run(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> TaskResult:
        """Run one project synthesis task."""
        del reporter
        project_key = _require_scope(task)
        project = _require_project(workspace_path, project_key)
        inputs = build_project_synthesis_inputs(
            workspace_path=workspace_path, project_key=project_key
        )
        output_path = workspace_path / project_synthesis_artifact(project_key).path
        if output_path.exists():
            output_path.unlink()

        universe = _indexed_turn_universe(project)
        if not universe:
            _write_empty_envelope(output_path, project_key, project.project_label)
            return TaskResult(task_id=task.task_id, status="success")

        # The synthesizer self-loops on write_work_item's uncovered_turns within one turn. An
        # all-gap project (zero committed chains) cannot be bootstrapped this way and fails the
        # coverage check below; that degenerate case is out of MVP scope.
        runner = await self.agent_factory.runner(
            AgentConfig(
                working_directory=workspace_path,
                approval_mode="auto_review",
                sandbox="workspace-write",
            )
        )
        await runner.turn(
            project_synthesizer_prompt(
                project_key=inputs.project_key,
                project_json=inputs.project_json,
                evidence_chains=inputs.evidence_chains,
            )
        )
        uncovered = _uncovered_turns(output_path, universe)
        if uncovered:
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=(_uncovered_message(project_key, uncovered),),
            )
        return TaskResult(task_id=task.task_id, status="success")


def _require_scope(task: TaskSpec) -> str:
    if task.project_key is None:
        raise PromptDiaryError(_missing_scope_message(task.task_id))
    return task.project_key


def _require_project(workspace_path: Path, project_key: str) -> PreparedProject:
    workspace = load_prepared_workspace(workspace_path)
    project = next((item for item in workspace.projects if item.project_key == project_key), None)
    if project is None:
        raise PromptDiaryError(_unknown_project_message(project_key))
    return project


def _indexed_turn_universe(project: PreparedProject) -> tuple[TurnReference, ...]:
    return tuple(
        TurnReference(session.session_ref, turn.turn_ref)
        for session in project.sessions
        for turn in session.turns
    )


def _uncovered_turns(
    output_path: Path, universe: tuple[TurnReference, ...]
) -> tuple[TurnReference, ...]:
    covered = _covered_keys(output_path)
    return tuple(ref for ref in universe if (ref.session_ref, ref.turn_ref) not in covered)


def _covered_keys(output_path: Path) -> frozenset[tuple[str, str]]:
    if not output_path.exists():
        return frozenset()
    raw: object = json.loads(output_path.read_text(encoding="utf-8"))
    envelope = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    items = envelope.get("work_items")
    rows = cast("list[Any]", items) if isinstance(items, list) else []
    keys: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        covered = cast("dict[str, Any]", row).get("covered_turns")
        for ref in cast("list[Any]", covered) if isinstance(covered, list) else []:
            if isinstance(ref, dict):
                mapping = cast("dict[str, Any]", ref)
                keys.add((_as_str(mapping.get("session_ref")), _as_str(mapping.get("turn_ref"))))
    return frozenset(keys)


def _write_empty_envelope(output_path: Path, project_key: str, project_label: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            new_project_synthesis_envelope(project_key, project_label),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _missing_scope_message(task_id: str) -> str:
    return f"project synthesis task {task_id} requires project_key"


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"


def _uncovered_message(project_key: str, uncovered: tuple[TurnReference, ...]) -> str:
    listed = ", ".join(f"{ref.session_ref}/{ref.turn_ref}" for ref in uncovered)
    return f"project synthesis for {project_key} left {len(uncovered)} indexed turn(s) uncovered: {listed}"
```

- [ ] **Step 5: Run the runner tests; verify pass. Then full project-synthesis + MCP + prompt suite + gates.**

Run: `uv run pytest tests/generate/project_synthesis tests/mcp/test_server.py tests/generate/test_prompts.py -q`
Expected: PASS.
Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add src/prompt_diary/generate/project_synthesis/runner.py tests/support/project_synthesis_agent.py tests/generate/project_synthesis/test_runner.py
git commit -m "feat(project-synthesis): phase runner with single-turn self-looping synthesis"
```

## Task 7: Real-agent integration test (opt-in)

**Files:**
- Test: `tests/integrations/test_project_synthesis_codex.py`

**What it does:** The third test layer — a live Codex agent runs the `project` phase end-to-end through `build_generation_workflow().run_phase(...)`, calling the real `write_work_item` MCP tool. Gated behind the `codex_mcp` marker (skipped unless `--run-codex-mcp`) and `importorskip("openai_codex")`, mirroring `test_evidence_extraction_codex.py`.

- [ ] **Step 1: Write the integration test.**

Create `tests/integrations/test_project_synthesis_codex.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from prompt_diary.cmds.generate import build_generation_workflow
from prompt_diary.generate.project_synthesis.model import ParsedWorkItem, parse_work_item
from tests.support.project_synthesis import (
    ALL_TURNS,
    COMMITTED_TURNS,
    PROJECT_KEY,
    copy_basic_project_workspace,
    load_project_synthesis,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.codex_mcp


def test_real_agent_synthesizes_work_items_for_fixture_project(tmp_path: Path) -> None:
    pytest.importorskip("openai_codex")
    workspace = copy_basic_project_workspace(tmp_path)

    result = build_generation_workflow().run_phase(
        workspace_path=workspace,
        phase="project",
        project_key=PROJECT_KEY,
    )

    assert result.task_result.ok
    envelope = load_project_synthesis(workspace)
    covered = [
        (ref["session_ref"], ref["turn_ref"])
        for item in envelope["work_items"]
        for ref in item["covered_turns"]
    ]
    # Coverage invariant: every indexed turn covered exactly once.
    assert sorted(covered) == sorted(ALL_TURNS)
    assert len(covered) == len(set(covered))
    # source_user_messages populated for the committed turns.
    messages = {(entry["session_ref"], entry["turn_ref"]) for entry in envelope["source_user_messages"]}
    assert messages == set(COMMITTED_TURNS)
    # Every committed work item is well-formed.
    for item in envelope["work_items"]:
        assert isinstance(parse_work_item(item), ParsedWorkItem)
```

- [ ] **Step 2: Verify it is collected but skipped by default.**

Run: `uv run pytest tests/integrations/test_project_synthesis_codex.py -q`
Expected: `1 skipped` (needs `--run-codex-mcp`).

- [ ] **Step 3 (optional, if a `codex` binary is available): run it live.**

Run: `uv run pytest tests/integrations/test_project_synthesis_codex.py --run-codex-mcp -q`
Expected: PASS (or `skipped` if `openai_codex`/`codex` is unavailable).

- [ ] **Step 4: Gates + commit.**

Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
```bash
git add tests/integrations/test_project_synthesis_codex.py
git commit -m "test(project-synthesis): opt-in real-agent integration test"
```

## Task 8: Full verification and branch finish

**Files:** none (verification + plan commit).

- [ ] **Step 1: Run the full default suite.**

Run: `uv run pytest -q`
Expected: PASS; the `codex_mcp`-marked tests report as skipped. No failures, no warnings.

- [ ] **Step 2: Run every gate.**

Run: `uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: clean (`0 errors` from basedpyright).

- [ ] **Step 3: Build the docs (verify no broken includes/links).**

Run: `mdbook build docs`
Expected: success.

- [ ] **Step 4: Commit the plan document.**

```bash
git add docs/superpowers/plans/2026-06-01-project-synthesis-phase.md
git commit -m "docs(project-synthesis): implementation plan for the phase"
```

- [ ] **Step 5: Finish the development branch.**

Use **superpowers:finishing-a-development-branch**: summarize the work, confirm the tree is clean and green, and decide with the user whether to fast-forward `main` to `design/project-synthesis-phase` (or open a PR). The README needs no change — the phase runs through the existing `report generate project --project-key <key>` command and the existing `report mcp serve` server; no new commands, tooling, or supported Python versions were introduced.

---

## Plan self-review

- **Spec coverage.** `write_work_item` input schema, write behavior, and every structural rule in `mcp-tools/project-synthesis.md` map to Task 3 (`_validate_against_workspace`, `_commit`, `_source_user_messages`) and Task 4 (registration). The work-item schema, kinds, required-fields-per-kind, coverage invariant, and `source_user_messages` shape in `project-synthesis.md` map to Tasks 1 (model) and 3 (workspace rules). The runner/paste/loop map to Tasks 5–6; the three-layer test strategy (direct API, mocked agent, real agent) maps to Tasks 3, 6, 7.
- **No placeholders.** Every production module and test file is given complete. No "TBD"/"similar to"/"add validation" steps.
- **Type/name consistency.** `TurnReference`, `WorkItem*`, `WriteWorkItem*Result`, `WorkItemWriteError`, `CommittedChain`, `ProjectSynthesisInputs`, `parse_work_item`, `work_item_to_json`, `new_project_synthesis_envelope`, `load_committed_chains`, `committed_turn_keys`, `build_project_synthesis_inputs`, `render_evidence_chains`, `write_work_item`, `ProjectSynthesisRunner` are used identically across producer and consumer tasks. Pipeline symbols reused: `project_synthesis_artifact`, `project_synthesis_task_id`, `TaskResult`, `TaskSpec`. Prompt enums reused: `PROJECT_WORK_ITEM_KINDS`, `EVIDENCE_OUTCOME_CATEGORIES`, `EVIDENCE_TERMINAL_STATES`. Prompt function `project_synthesizer_prompt(project_key, project_json, evidence_chains)` matches the committed signature.
- **Known limitation surfaced.** The all-gap-project bootstrap gap is documented in design decision 3 and as a code comment in `runner.py`.

