from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from prompt_diary.generate.project_synthesis.mcp import (
    WriteWorkItemAppendedResult,
    WriteWorkItemInvalidResult,
    WriteWorkItemResult,
    write_work_item,
)

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
    return cast(
        "dict[str, Any]", json.loads(synthesis_path(workspace_path).read_text(encoding="utf-8"))
    )


def project_synthesis_text(workspace_path: Path) -> str:
    return synthesis_path(workspace_path).read_text(encoding="utf-8")


def deep_copy_json(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)


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
    assert isinstance(message, str) and message  # noqa: PT018
    assert isinstance(hint, str) and hint  # noqa: PT018
    if message_contains is not None:
        assert message_contains in message
    if hint_contains is not None:
        assert hint_contains in hint
