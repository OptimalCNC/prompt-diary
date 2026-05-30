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


def test_inputs_resolve_session_path_and_strip_turns(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert inputs.session_path == f"projects/{PROJECT_KEY}/sessions/codex/session-001.jsonl"
    record = json.loads(inputs.session_index_record)
    assert "turns" not in record
    assert record["session_ref"] == SESSION_REF
    assert json.loads(inputs.project_json)["project_key"] == PROJECT_KEY


def test_inputs_preserve_raw_target_turn_fields_in_order(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert [turn.turn_ref for turn in inputs.turns] == ["T0001", "T0002"]
    first = json.loads(inputs.turns[0].target_turn_json)
    assert first["turn_ref"] == "T0001"
    assert first["turn_start_line"] == 2
    assert first["turn_end_line"] == 8
    assert "target_subagents" in first
    assert inputs.turns[0].span.start == 2
    assert inputs.turns[0].span.end == 8


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
