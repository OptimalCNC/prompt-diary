"""Tests for the daily-synthesis prompt-input builders."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.inputs import (
    build_project_summary_inputs,
    build_report_inputs,
)
from tests.support.daily_synthesis import (
    PROJECT_KEY,
    TWO_PROJECTS_KEY_A,
    TWO_PROJECTS_KEY_B,
    copy_basic_daily_workspace,
    copy_corrupt_daily_workspace,
    copy_two_projects_daily_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_build_project_summary_inputs_renders_work_items(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    inputs = build_project_summary_inputs(workspace_path=workspace, project_key=PROJECT_KEY)

    assert inputs.project_key == PROJECT_KEY
    assert PROJECT_KEY in inputs.project_json
    assert "W0001" in inputs.work_items
    assert "material_work_item" in inputs.work_items
    # The passes are told to account for a work item's limits, so they must be rendered.
    assert "Prompt-test suite not confirmed green within these turns." in inputs.work_items


def test_build_report_inputs_labels_each_item_and_message_with_project(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    inputs = build_report_inputs(workspace_path=workspace)

    assert f"{PROJECT_KEY} · W0001" in inputs.work_items
    assert "Prompt-test suite not confirmed green within these turns." in inputs.work_items
    assert f"{PROJECT_KEY} · S0001/T0001" in inputs.source_user_messages
    assert "simplify" in inputs.source_user_messages.lower()


def test_build_report_inputs_labels_work_items_from_both_projects(tmp_path: Path) -> None:
    workspace = copy_two_projects_daily_workspace(tmp_path)

    inputs = build_report_inputs(workspace_path=workspace)

    # Each project's work item is labelled with its own project_key, even though both reuse the same
    # S0001/T0001 ref — the label is what lets a cross-project pass cite the right project.
    assert f"{TWO_PROJECTS_KEY_A} · W0001" in inputs.work_items
    assert f"{TWO_PROJECTS_KEY_B} · W0001" in inputs.work_items
    assert f"{TWO_PROJECTS_KEY_A} · S0001/T0001" in inputs.source_user_messages
    assert f"{TWO_PROJECTS_KEY_B} · S0001/T0001" in inputs.source_user_messages


def test_build_project_summary_inputs_renders_empty_work_items(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    envelope_path = workspace / "projects" / PROJECT_KEY / "project-synthesis.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["work_items"] = []
    envelope_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")

    inputs = build_project_summary_inputs(workspace_path=workspace, project_key=PROJECT_KEY)

    assert inputs.work_items == "(No synthesized work items for this project.)"


def test_build_project_summary_inputs_unknown_project_raises(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    with pytest.raises(PromptDiaryError):
        build_project_summary_inputs(workspace_path=workspace, project_key="Missing-000000000000")


def test_build_report_inputs_raises_on_corrupt_envelope(tmp_path: Path) -> None:
    workspace = copy_corrupt_daily_workspace(tmp_path)

    with pytest.raises(PromptDiaryError):
        build_report_inputs(workspace_path=workspace)
