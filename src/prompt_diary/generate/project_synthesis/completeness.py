"""Completeness inspection for durable project-synthesis envelopes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from prompt_diary.generate.project_synthesis.mcp import validate_work_item_against_workspace
from prompt_diary.generate.project_synthesis.model import (
    InvalidWorkItem,
    TurnReference,
    parse_work_item,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import PreparedProject


@dataclass(frozen=True)
class ProjectSynthesisInspection:
    """Result of inspecting one project synthesis artifact."""

    complete: bool
    errors: tuple[str, ...] = ()


def inspect_project_synthesis(
    *,
    workspace_path: Path,
    project_key: str,
) -> ProjectSynthesisInspection:
    """Check whether a project envelope is complete for the current prepared workspace."""
    workspace = load_prepared_workspace(workspace_path)
    project = next((item for item in workspace.projects if item.project_key == project_key), None)
    if project is None:
        return _incomplete(_unknown_project_message(project_key))
    envelope_path = workspace_path / "projects" / project_key / "project-synthesis.json"
    envelope = _read_envelope(envelope_path)
    if envelope is None:
        return _incomplete(_missing_envelope_message(envelope_path))
    return inspect_project_synthesis_envelope(
        workspace_path=workspace_path,
        project=project,
        envelope=envelope,
    )


def inspect_project_synthesis_envelope(
    *,
    workspace_path: Path,
    project: PreparedProject,
    envelope: dict[str, Any],
) -> ProjectSynthesisInspection:
    """Check one parsed project envelope against current index and write rules."""
    universe = _indexed_turn_universe(project)
    universe_keys = frozenset((ref.session_ref, ref.turn_ref) for ref in universe)
    committed = committed_turn_keys(load_committed_chains(workspace_path, project.project_key))
    errors = _envelope_errors(envelope, project)
    work_items = _as_list(envelope.get("work_items"))
    covered: set[tuple[str, str]] = set()
    existing_refs: set[str] = set()

    for index, raw_item in enumerate(work_items):
        if not isinstance(raw_item, dict):
            errors.append(_work_item_object_message(index))
            continue
        parsed = parse_work_item(cast("dict[str, Any]", raw_item))
        if isinstance(parsed, InvalidWorkItem):
            errors.extend(error.message for error in parsed.errors)
            continue
        item = parsed.work_item
        validation_errors = validate_work_item_against_workspace(
            item,
            universe=universe_keys,
            committed=committed,
            already_covered=frozenset(covered),
            existing_refs=frozenset(existing_refs),
        )
        errors.extend(error.message for error in validation_errors)
        existing_refs.add(item.work_item_ref)
        covered.update((ref.session_ref, ref.turn_ref) for ref in item.covered_turns)

    missing = tuple(ref for ref in universe if (ref.session_ref, ref.turn_ref) not in covered)
    if missing:
        errors.append(_missing_turns_message(missing))

    return ProjectSynthesisInspection(complete=not errors, errors=tuple(errors))


def _read_envelope(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _envelope_errors(envelope: dict[str, Any], project: PreparedProject) -> list[str]:
    errors: list[str] = []
    if envelope.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if envelope.get("project_key") != project.project_key:
        errors.append(_project_mismatch_message(project.project_key))
    if envelope.get("project_label") != project.project_label:
        errors.append(_label_mismatch_message(project.project_label))
    if not isinstance(envelope.get("work_items"), list):
        errors.append("work_items must be a list")
    if not isinstance(envelope.get("source_user_messages"), list):
        errors.append("source_user_messages must be a list")
    return errors


def _indexed_turn_universe(project: PreparedProject) -> tuple[TurnReference, ...]:
    return tuple(
        TurnReference(session.session_ref, turn.turn_ref)
        for session in project.sessions
        for turn in session.turns
    )


def _incomplete(error: str) -> ProjectSynthesisInspection:
    return ProjectSynthesisInspection(complete=False, errors=(error,))


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"


def _missing_envelope_message(path: Path) -> str:
    return f"missing project synthesis envelope: {path}"


def _project_mismatch_message(project_key: str) -> str:
    return f"project_key must be {project_key!r}"


def _label_mismatch_message(project_label: str) -> str:
    return f"project_label must be {project_label!r}"


def _work_item_object_message(index: int) -> str:
    return f"work_items[{index}] must be a JSON object"


def _missing_turns_message(turn_refs: tuple[TurnReference, ...]) -> str:
    rendered = ", ".join(f"{ref.session_ref}/{ref.turn_ref}" for ref in turn_refs)
    return f"missing covered turn(s): {rendered}"
