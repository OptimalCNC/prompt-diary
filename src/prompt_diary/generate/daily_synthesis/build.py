"""Build the deterministic daily-report skeleton.

The Build step assembles every deterministic field of ``daily-report.json`` — the header, all of
Work by Project except each project's ``summary``, and the whole Executive Summary — directly from
the prepared workspace and the per-project ``project-synthesis.json`` envelopes, with no AI. The
three ``synthesize`` slots (per-project ``summary``, ``engagement_assessment``, ``team_learning``)
are seeded ``null`` for the agent passes to patch, and ``overall_confidence`` is left ``null`` for
Finalize to fill. The assembled report is written to the workspace root and returned.

Every claim-bearing field is lifted verbatim from a validated upstream work item or resolved
through the session index, so a built report cannot drift from its evidence: the work-item view
copies summaries as-is, dispositions are derived from the work item's terminal states and outcomes,
and citations are the work item's ``evidence_refs`` resolved to their indexed-turn line ranges.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.citations import CitationResolver
from prompt_diary.generate.daily_synthesis.model import (
    CONFIDENCE_RANK,
    OPEN_DISPOSITIONS,
    derive_disposition,
)
from prompt_diary.generate.project_synthesis.model import (
    InvalidWorkItem,
    WorkItem,
    WorkItemOutcome,
    WorkItemTerminalState,
    parse_work_item,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.project_synthesis.model import TurnReference
    from prompt_diary.generate.workspace import PreparedProject, PreparedWorkspace

__all__ = ["build_daily_report"]

_REPORT_NAME = "daily-report.json"
_MATERIAL = "material_work_item"


@dataclass(frozen=True)
class _ProjectInput:
    """A workspace project paired with its parsed envelope work items and source messages."""

    project: PreparedProject
    work_items: tuple[WorkItem, ...]
    source_user_messages: list[dict[str, Any]]


@dataclass(frozen=True)
class _RankedOutcome:
    """An executive-summary outcome entry tagged with its sort keys.

    ``rank`` is the outcome confidence (high=3) and ``significance`` is its position in the
    project-then-work-item significance traversal; the list sorts by ``(-rank, significance)`` so
    the highest-confidence outcomes lead while ties keep significance order.
    """

    rank: int
    significance: int
    entry: dict[str, Any]


def build_daily_report(*, workspace_path: Path) -> dict[str, Any]:
    """Assemble the deterministic daily-report skeleton, write it, and return it."""
    workspace = load_prepared_workspace(workspace_path)
    resolver = CitationResolver.from_workspace(workspace)
    inputs = _load_inputs(workspace_path, workspace)
    ordered = _projects_in_significance_order(inputs)

    project_views = [_project_view(item, resolver) for item in ordered]
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_date": workspace.report_date,
        "status": workspace.status,
        "window": _window(workspace_path, workspace.timezone),
        "overall_confidence": None,
        "executive_summary": _executive_summary(ordered, resolver),
        "projects": project_views,
        "engagement_assessment": None,
        "team_learning": None,
    }
    _write_report(workspace_path, report)
    return report


def _load_inputs(workspace_path: Path, workspace: PreparedWorkspace) -> tuple[_ProjectInput, ...]:
    return tuple(_project_input(workspace_path, project) for project in workspace.projects)


def _project_input(workspace_path: Path, project: PreparedProject) -> _ProjectInput:
    envelope = _read_envelope(workspace_path, project.project_key)
    work_items = tuple(_parse_items(envelope, project.project_key))
    messages = [_as_mapping(entry) for entry in _as_list(envelope.get("source_user_messages"))]
    return _ProjectInput(project=project, work_items=work_items, source_user_messages=messages)


def _parse_items(envelope: dict[str, Any], project_key: str) -> list[WorkItem]:
    items: list[WorkItem] = []
    for index, raw in enumerate(_as_list(envelope.get("work_items"))):
        parsed = parse_work_item(_as_mapping(raw))
        # The envelope is written by the validated write_work_item tool, so every work item parses;
        # an InvalidWorkItem here is post-synthesis corruption. Fail loudly rather than drop it.
        if isinstance(parsed, InvalidWorkItem):
            ref = _as_str(_as_mapping(raw).get("work_item_ref")) or f"index {index}"
            raise PromptDiaryError(_corrupt_work_item_message(project_key, ref))
        items.append(parsed.work_item)
    return items


def _projects_in_significance_order(
    inputs: tuple[_ProjectInput, ...],
) -> tuple[_ProjectInput, ...]:
    return tuple(
        sorted(
            inputs,
            key=lambda item: (-_material_count(item.work_items), item.project.project_label),
        )
    )


def _material_count(work_items: tuple[WorkItem, ...]) -> int:
    return sum(1 for item in work_items if item.kind == _MATERIAL)


def _project_view(item: _ProjectInput, resolver: CitationResolver) -> dict[str, Any]:
    project_key = item.project.project_key
    ordered_items = _work_items_in_significance_order(item.work_items)
    return {
        "project_key": project_key,
        "project_label": item.project.project_label,
        "summary": None,
        "work_items": [_work_item_view(wi, project_key, resolver) for wi in ordered_items],
        "source_user_messages": item.source_user_messages,
    }


def _work_items_in_significance_order(work_items: tuple[WorkItem, ...]) -> list[WorkItem]:
    material = sorted(
        (item for item in work_items if item.kind == _MATERIAL),
        key=lambda item: (-CONFIDENCE_RANK[item.confidence], item.work_item_ref),
    )
    rest = sorted(
        (item for item in work_items if item.kind != _MATERIAL),
        key=lambda item: item.work_item_ref,
    )
    return [*material, *rest]


def _work_item_view(item: WorkItem, project_key: str, resolver: CitationResolver) -> dict[str, Any]:
    return {
        "work_item_ref": item.work_item_ref,
        "title": item.title,
        "kind": item.kind,
        "disposition": _disposition(item),
        "confidence": item.confidence,
        "covered_turns": [
            {"session_ref": ref.session_ref, "turn_ref": ref.turn_ref} for ref in item.covered_turns
        ],
        "trigger_summary": item.trigger.summary if item.trigger is not None else None,
        "agent_reaction_summary": (
            item.agent_reaction.summary if item.agent_reaction is not None else None
        ),
        "outcomes": [
            {
                "what_changed": outcome.summary,
                "confidence": outcome.confidence,
                "citations": _resolve_refs(outcome.evidence_refs, project_key, resolver),
            }
            for outcome in item.outcomes
        ],
        "terminal_states": [{"summary": state.summary} for state in item.terminal_states],
        "limits": list(item.limits),
    }


def _disposition(item: WorkItem) -> str | None:
    return derive_disposition(
        kind=item.kind,
        terminal_types=frozenset(state.type for state in item.terminal_states),
        has_outcomes=bool(item.outcomes),
    )


def _executive_summary(
    ordered: tuple[_ProjectInput, ...], resolver: CitationResolver
) -> dict[str, Any]:
    return {
        "top_outcomes": _top_outcomes(ordered, resolver),
        "open_items": _open_items(ordered, resolver),
    }


def _top_outcomes(
    ordered: tuple[_ProjectInput, ...], resolver: CitationResolver
) -> list[dict[str, Any]]:
    ranked = [
        _ranked_outcome(index, project_key, outcome, resolver)
        for index, project_key, item in _significant_work_items(ordered)
        if item.kind == _MATERIAL
        for outcome in item.outcomes
    ]
    # A curated headline must be cited: drop any outcome whose citations did not resolve (it still
    # appears, uncited, in Work by Project).
    cited = [entry for entry in ranked if entry.entry["citations"]]
    cited.sort(key=lambda entry: (-entry.rank, entry.significance))
    return [entry.entry for entry in cited]


def _ranked_outcome(
    significance: int, project_key: str, outcome: WorkItemOutcome, resolver: CitationResolver
) -> _RankedOutcome:
    return _RankedOutcome(
        rank=CONFIDENCE_RANK[outcome.confidence],
        significance=significance,
        entry={
            "text": outcome.summary,
            "citations": _resolve_refs(outcome.evidence_refs, project_key, resolver),
        },
    )


def _open_items(
    ordered: tuple[_ProjectInput, ...], resolver: CitationResolver
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for _, project_key, item in _significant_work_items(ordered):
        disposition = _disposition(item)
        if disposition not in OPEN_DISPOSITIONS:
            continue
        state = _terminal_state_for(item, disposition)
        if state is None:  # pragma: no cover - an open disposition always has its terminal state
            continue
        citations = _resolve_refs(state.evidence_refs, project_key, resolver)
        # A curated headline must be cited: drop an open item whose terminal-state citations did not
        # resolve (it still appears, uncited, in Work by Project).
        if not citations:
            continue
        items.append({"text": state.summary, "citations": citations})
    return items


def _significant_work_items(
    ordered: tuple[_ProjectInput, ...],
) -> list[tuple[int, str, WorkItem]]:
    flat: list[tuple[int, str, WorkItem]] = []
    index = 0
    for project_input in ordered:
        project_key = project_input.project.project_key
        for item in _work_items_in_significance_order(project_input.work_items):
            flat.append((index, project_key, item))
            index += 1
    return flat


def _terminal_state_for(item: WorkItem, disposition: str) -> WorkItemTerminalState | None:
    return next((state for state in item.terminal_states if state.type == disposition), None)


def _resolve_refs(
    refs: tuple[TurnReference, ...], project_key: str, resolver: CitationResolver
) -> list[dict[str, str]]:
    resolved: list[dict[str, str]] = []
    for ref in refs:
        hit = resolver.resolve(
            project_key=project_key, session_ref=ref.session_ref, turn_ref=ref.turn_ref
        )
        # Build resolves the validated work item's own evidence_refs; a ref that does not resolve
        # to an indexed turn would be an upstream corruption and is dropped, not emitted unresolved.
        if hit is not None:
            resolved.append(hit.to_json())
    return resolved


def _window(workspace_path: Path, timezone: str) -> dict[str, Any]:
    metadata = _load_json(workspace_path / "metadata.json")
    window_local = _as_mapping(metadata.get("report_window_local"))
    return {
        "start": window_local.get("start"),
        "end": window_local.get("end"),
        "timezone": timezone,
    }


def _read_envelope(workspace_path: Path, project_key: str) -> dict[str, Any]:
    path = workspace_path / "projects" / project_key / "project-synthesis.json"
    if not path.exists():  # pragma: no cover - a synthesized project always has an envelope
        return {}
    return _load_json(path)


def _write_report(workspace_path: Path, report: dict[str, Any]) -> None:
    path = workspace_path / _REPORT_NAME
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _corrupt_work_item_message(project_key: str, ref: str) -> str:
    return (
        f"project {project_key!r} has a structurally invalid work item ({ref}); "
        "re-run project synthesis to repair the envelope"
    )
