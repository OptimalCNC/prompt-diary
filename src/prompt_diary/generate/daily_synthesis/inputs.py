"""Build the daily-synthesis pass prompt inputs from the per-project envelopes.

Each daily-synthesis pass works only from strings pasted into its prompt — it opens no envelope of
its own. This module reads the per-project ``project-synthesis.json`` envelopes, parses each work
item with :func:`parse_work_item`, and renders the readable strings the passes need:

- the per-project summary pass receives one project's ``project.json`` text and a rendering of that
  project's work items;
- the engagement and team-learning passes receive ALL projects' work items, each labelled with its
  ``project_key`` (session refs repeat across projects, so a cross-project citation must name the
  project), and every project's ``source_user_messages``, each labelled with its project, turn, and
  verbatim messages.

Rendering follows :func:`render_evidence_chains`: a work item becomes a labelled block of trimmed
summaries — ref, kind, title, trigger and reaction summaries, outcomes (category, summary,
confidence), terminal states, its limits, the turns it covers, and its confidence. The limits matter
because the passes are told to account for them, so a synthesized section does not overstate
unverified work. The strings are display material; resolution and scope checks live in the write
tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.project_synthesis.model import (
    InvalidWorkItem,
    WorkItem,
    parse_work_item,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import PreparedWorkspace

__all__ = [
    "ProjectSummaryInputs",
    "ReportInputs",
    "build_project_summary_inputs",
    "build_report_inputs",
]

_EMPTY_WORK_ITEMS = "(No synthesized work items for this project.)"
_EMPTY_REPORT_WORK_ITEMS = "(No synthesized work items in any project.)"
_EMPTY_MESSAGES = "(No source user messages for any project.)"


@dataclass(frozen=True)
class ProjectSummaryInputs:
    """Rendered-ready inputs for the per-project summary pass."""

    project_key: str
    project_json: str
    work_items: str


@dataclass(frozen=True)
class ReportInputs:
    """Rendered-ready inputs for the engagement and team-learning passes."""

    work_items: str
    source_user_messages: str


def build_project_summary_inputs(*, workspace_path: Path, project_key: str) -> ProjectSummaryInputs:
    """Build the summary pass inputs for one project from its envelope."""
    workspace = load_prepared_workspace(workspace_path)
    _require_project(workspace, project_key)
    project_dir = workspace_path / "projects" / project_key
    work_items = _parse_work_items(_read_envelope(workspace_path, project_key), project_key)
    return ProjectSummaryInputs(
        project_key=project_key,
        project_json=_normalized_json(project_dir / "project.json"),
        work_items=_render_project_work_items(work_items),
    )


def build_report_inputs(*, workspace_path: Path) -> ReportInputs:
    """Build the cross-project engagement and team-learning inputs from every envelope."""
    workspace = load_prepared_workspace(workspace_path)
    work_item_sections: list[str] = []
    message_sections: list[str] = []
    for project in workspace.projects:
        envelope = _read_envelope(workspace_path, project.project_key)
        items = _parse_work_items(envelope, project.project_key)
        work_item_sections.extend(
            _render_labelled_item(item, project.project_key) for item in items
        )
        message_sections.extend(
            _render_messages(entry, project.project_key)
            for entry in _as_list(envelope.get("source_user_messages"))
        )
    return ReportInputs(
        work_items=_join_or_empty(work_item_sections, _EMPTY_REPORT_WORK_ITEMS),
        source_user_messages=_join_or_empty(message_sections, _EMPTY_MESSAGES),
    )


def _render_project_work_items(work_items: tuple[WorkItem, ...]) -> str:
    if not work_items:
        return _EMPTY_WORK_ITEMS
    blocks = [_render_item_block(item, header=f"**{item.work_item_ref}**") for item in work_items]
    return "\n\n".join(blocks)


def _render_labelled_item(item: WorkItem, project_key: str) -> str:
    return _render_item_block(item, header=f"**{project_key} · {item.work_item_ref}**")


def _render_item_block(item: WorkItem, *, header: str) -> str:
    lines = [
        f"{header} [{item.kind}] (confidence: {item.confidence})",
        f"title: {item.title}",
        f"covered_turns: {_render_turns(item.covered_turns)}",
    ]
    if item.trigger is not None:
        lines.append(f"trigger: {item.trigger.summary}")
    if item.agent_reaction is not None:
        lines.append(f"reaction: {item.agent_reaction.summary}")
    if item.outcomes:
        lines.append("outcomes:")
        lines.extend(
            f"- {outcome.category}: {outcome.summary} (confidence: {outcome.confidence})"
            for outcome in item.outcomes
        )
    if item.terminal_states:
        lines.append("terminal states:")
        lines.extend(f"- {state.type}: {state.summary}" for state in item.terminal_states)
    if item.limits:
        lines.append("limits:")
        lines.extend(f"- {limit}" for limit in item.limits)
    return "\n".join(lines)


def _render_messages(entry: object, project_key: str) -> str:
    mapping = _as_mapping(entry)
    session_ref = _as_str(mapping.get("session_ref"))
    turn_ref = _as_str(mapping.get("turn_ref"))
    header = f"**{project_key} · {session_ref}/{turn_ref}**"
    messages = [text for text in _as_list(mapping.get("messages")) if isinstance(text, str)]
    body = "\n".join(messages) if messages else "(no message text)"
    return f"{header}\n{body}"


def _render_turns(turns: tuple[Any, ...]) -> str:
    return ", ".join(f"{ref.session_ref}/{ref.turn_ref}" for ref in turns)


def _parse_work_items(envelope: dict[str, Any], project_key: str) -> tuple[WorkItem, ...]:
    items: list[WorkItem] = []
    for index, raw in enumerate(_as_list(envelope.get("work_items"))):
        parsed = parse_work_item(_as_mapping(raw))
        # The envelope is written by the validated write_work_item tool, so every work item parses;
        # an InvalidWorkItem here is post-synthesis corruption. Fail loudly, like Build does.
        if isinstance(parsed, InvalidWorkItem):
            ref = _as_str(_as_mapping(raw).get("work_item_ref")) or f"index {index}"
            raise PromptDiaryError(_corrupt_work_item_message(project_key, ref))
        items.append(parsed.work_item)
    return tuple(items)


def _read_envelope(workspace_path: Path, project_key: str) -> dict[str, Any]:
    path = workspace_path / "projects" / project_key / "project-synthesis.json"
    if not path.exists():  # pragma: no cover - a synthesized project always has an envelope
        return {}
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _require_project(workspace: PreparedWorkspace, project_key: str) -> None:
    if not any(item.project_key == project_key for item in workspace.projects):
        raise PromptDiaryError(_unknown_project_message(project_key))


def _normalized_json(path: Path) -> str:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(raw, indent=2, ensure_ascii=False)


def _join_or_empty(sections: list[str], empty: str) -> str:
    return "\n\n".join(sections) if sections else empty


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


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"
