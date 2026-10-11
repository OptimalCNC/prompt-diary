from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_inputs_include_only_model_relevant_metadata(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    record = json.loads(inputs.session_index_record)
    assert record == {"source": "codex"}
    assert json.loads(inputs.project_json) == {"project_label": "ReportGenerator"}
    assert inputs.project_key == PROJECT_KEY
    assert inputs.session_ref == SESSION_REF


def test_inputs_preserve_target_turn_spans_in_order(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert [turn.turn_ref for turn in inputs.turns] == ["T0001", "T0002"]
    first = json.loads(inputs.turns[0].target_turn_json)
    assert first["turn_ref"] == "T0001"
    assert first["turn_start_line"] == 2
    assert first["turn_end_line"] == 8
    assert "target_subagents" not in first
    assert inputs.turns[0].span.start == 2
    assert inputs.turns[0].span.end == 8
    assert "previous_context" not in first
    second = json.loads(inputs.turns[1].target_turn_json)
    assert second["previous_context"] == {
        "start_line": 2,
        "end_line": 8,
    }


def test_inputs_locate_unindexed_context_without_creating_an_extraction_target(
    tmp_path: Path,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    row = json.loads(index_path.read_text(encoding="utf-8"))
    row["turns"] = [row["turns"][1]]
    row["target_start_line"] = 9
    row["turns"][0]["previous_context"] = {"start_line": 2, "end_line": 8}
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert [turn.turn_ref for turn in inputs.turns] == ["T0002"]
    assignment = json.loads(inputs.turns[0].target_turn_json)
    assert assignment["previous_context"] == {"start_line": 2, "end_line": 8}
    assert "turn_ref" not in assignment["previous_context"]
    assert inputs.turns[0].span.start == 9


def test_inputs_prefer_prepared_context_over_previous_indexed_turn(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    row = json.loads(index_path.read_text(encoding="utf-8"))
    row["turns"][1]["previous_context"] = {"start_line": 7, "end_line": 8}
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assignment = json.loads(inputs.turns[1].target_turn_json)
    assert assignment["previous_context"] == {"start_line": 7, "end_line": 8}


@pytest.mark.parametrize(
    ("previous_context", "message"),
    [
        ("not-a-range", "missing object field 'previous_context'"),
        ({"start_line": 2}, "missing integer field 'end_line'"),
        ({"start_line": 0, "end_line": 1}, "start line must be positive"),
        ({"start_line": 7, "end_line": 6}, "end line must be >= start line"),
        ({"start_line": 2, "end_line": 9}, "must end before the assigned turn starts"),
    ],
)
def test_inputs_reject_invalid_prepared_context(
    tmp_path: Path, previous_context: dict[str, int] | str, message: str
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    row = json.loads(index_path.read_text(encoding="utf-8"))
    row["turns"][1]["previous_context"] = previous_context
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(PromptDiaryError, match=message):
        build_session_extraction_inputs(
            workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
        )


def test_inputs_exclude_child_locators_and_unrecognized_index_fields(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    row = json.loads(index_path.read_text(encoding="utf-8"))
    row["subagent_path"] = "sessions/codex/subagents/child-locator"
    row["unrecognized_field"] = "unrecognized-value"
    row["turns"][0]["target_subagents"] = [{"session_file": "child-locator.jsonl"}]
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert "child-locator" not in inputs.session_index_record
    assert "unrecognized-value" not in inputs.session_index_record
    assert "child-locator" not in inputs.turns[0].target_turn_json


def test_inputs_reject_unknown_session(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    with pytest.raises(PromptDiaryError, match="unknown session_ref"):
        build_session_extraction_inputs(
            workspace_path=workspace, project_key=PROJECT_KEY, session_ref="S9999"
        )


def test_inputs_reject_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    with pytest.raises(PromptDiaryError, match="unknown project_key"):
        build_session_extraction_inputs(
            workspace_path=workspace, project_key="Missing-000", session_ref=SESSION_REF
        )


def test_inputs_tolerate_blank_index_lines(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    index_path.write_text("\n" + index_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert [turn.turn_ref for turn in inputs.turns] == ["T0001", "T0002"]
