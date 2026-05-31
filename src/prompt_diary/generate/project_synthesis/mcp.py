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

    Chain-only structure is parsed first; workspace-dependent checks (project, indexed turns,
    kind vs. committed-chain coverage, coverage exclusivity, and evidence references) run against
    the prepared workspace and the existing envelope. A rejected write returns structured errors
    and never touches the canonical envelope file. The first accepted write creates the envelope
    and populates ``source_user_messages`` verbatim from the committed cards.
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
            errors.append(
                WorkItemWriteError(path, _nongap_without_chain_message(ref), _NONGAP_HINT)
            )
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
            errors.append(
                WorkItemWriteError(path, _ref_not_covered_message(ref), _REF_COVERED_HINT)
            )
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
            entries.append(  # noqa: PERF401 — guarded append keeps the plan's branch explicit
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
    return (
        f"evidence ref {ref.session_ref}/{ref.turn_ref} must be one of "
        "this work item's covered_turns"
    )


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
