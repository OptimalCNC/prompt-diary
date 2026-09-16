from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from prompt_diary.generate.project_synthesis.completeness import (
    ProjectSynthesisCheckpoint,
    inspect_project_synthesis,
)
from prompt_diary.generate.project_synthesis.mcp import write_work_item
from tests.support.project_synthesis import (
    PROJECT_KEY,
    copy_complete_project_workspace,
    synthesis_path,
    valid_material_work_item,
    valid_no_material_work_item,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_inspector_reports_unknown_project_and_missing_envelope(tmp_path: Path) -> None:
    workspace = copy_complete_project_workspace(tmp_path)

    unknown = inspect_project_synthesis(
        workspace_path=workspace,
        project_key="Missing-000000000000",
    )
    missing = inspect_project_synthesis(workspace_path=workspace, project_key=PROJECT_KEY)

    assert not unknown.complete
    assert "unknown project_key" in unknown.errors[0]
    assert not missing.complete
    assert "missing project synthesis envelope" in missing.errors[0]


def test_inspector_reports_malformed_json_envelope(tmp_path: Path) -> None:
    workspace = copy_complete_project_workspace(tmp_path)
    synthesis_path(workspace).write_text("{", encoding="utf-8")

    inspection = inspect_project_synthesis(workspace_path=workspace, project_key=PROJECT_KEY)

    assert not inspection.complete
    assert "schema_version must be 1" in inspection.errors
    assert "work_items must be a list" in inspection.errors


def test_inspector_reports_envelope_shape_errors(tmp_path: Path) -> None:
    workspace = copy_complete_project_workspace(tmp_path)
    _write_envelope(
        workspace,
        {
            "schema_version": 3,
            "project_key": "Other-000000000000",
            "project_label": "Other",
            "work_items": "not-a-list",
            "source_user_messages": "not-a-list",
        },
    )

    inspection = inspect_project_synthesis(workspace_path=workspace, project_key=PROJECT_KEY)

    assert not inspection.complete
    assert "schema_version must be 1" in inspection.errors
    assert f"project_key must be {PROJECT_KEY!r}" in inspection.errors
    assert "project_label must be 'ReportGenerator'" in inspection.errors
    assert "work_items must be a list" in inspection.errors
    assert "source_user_messages must be a list" in inspection.errors


def test_inspector_reports_non_object_work_item(tmp_path: Path) -> None:
    workspace = copy_complete_project_workspace(tmp_path)
    _write_envelope(
        workspace,
        {
            "schema_version": 1,
            "project_key": PROJECT_KEY,
            "project_label": "ReportGenerator",
            "work_items": ["not-an-object"],
            "source_user_messages": [],
        },
    )

    inspection = inspect_project_synthesis(workspace_path=workspace, project_key=PROJECT_KEY)

    assert not inspection.complete
    assert "work_items[0] must be a JSON object" in inspection.errors


def test_valid_checkpoint_distinguishes_missing_coverage_from_corruption(tmp_path: Path) -> None:
    workspace = copy_complete_project_workspace(tmp_path)
    assert (
        write_work_item(
            workspace_path=workspace, project_key=PROJECT_KEY, work_item=valid_material_work_item()
        ).status
        == "appended"
    )

    partial = inspect_project_synthesis(workspace_path=workspace, project_key=PROJECT_KEY)

    assert isinstance(partial, ProjectSynthesisCheckpoint)
    assert not partial.complete
    assert partial.covered_keys == frozenset({("S0001", "T0001"), ("S0001", "T0002")})
    assert partial.errors == ("missing covered turn(s): S0001/T0003, S0002/T0001",)

    remaining = valid_no_material_work_item()
    remaining["covered_turns"].append({"session_ref": "S0001", "turn_ref": "T0003"})
    assert (
        write_work_item(
            workspace_path=workspace, project_key=PROJECT_KEY, work_item=remaining
        ).status
        == "appended"
    )
    complete = inspect_project_synthesis(workspace_path=workspace, project_key=PROJECT_KEY)
    assert isinstance(complete, ProjectSynthesisCheckpoint)
    assert complete.complete
    assert complete.errors == ()


def _write_envelope(workspace: Path, envelope: dict[str, Any]) -> None:
    path = synthesis_path(workspace)
    path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
