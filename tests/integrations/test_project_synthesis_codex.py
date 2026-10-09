from __future__ import annotations

import shutil
from pathlib import Path
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
    from typing import Any

pytestmark = pytest.mark.codex_mcp

_DISPOSITION_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "project-synthesis" / "disposition-scope" / "workspace"
)
# Each session is an independent task; scope changes and recovery belong to that task.
_EXPECTED_DISPOSITIONS: tuple[tuple[str, int, str], ...] = (
    ("S0001", 2, "completed"),  # interruption followed by verified delivery
    ("S0002", 2, "completed"),  # failed check repaired and verified
    ("S0003", 2, "completed"),  # user replaces deployment with a prompt handoff
    ("S0004", 2, "blocked"),  # completed original scope, unmet added requirement
    ("S0005", 1, "in_progress"),  # observable active work at evidence cutoff
    ("S0006", 2, "cancelled"),  # user abandons delivery after partial progress
    ("S0007", 1, "unknown"),  # interruption label but no observed stop or execution result
    ("S0008", 1, "interrupted"),  # explicit stop with effective scope unfinished
    ("S0009", 1, "failed"),  # unsuccessful delivery with no later repair
)


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
    messages = {
        (entry["session_ref"], entry["turn_ref"]) for entry in envelope["source_user_messages"]
    }
    assert messages == set(COMMITTED_TURNS)
    # Every committed work item is well-formed.
    for item in envelope["work_items"]:
        assert isinstance(parse_work_item(item), ParsedWorkItem)


def test_real_agent_judges_final_scope_across_turns(tmp_path: Path) -> None:
    pytest.importorskip("openai_codex")
    workspace = tmp_path / "workspace"
    shutil.copytree(_DISPOSITION_FIXTURE, workspace)

    result = build_generation_workflow().run_phase(
        workspace_path=workspace,
        phase="project",
        project_key=PROJECT_KEY,
    )

    assert result.task_result.ok
    envelope = load_project_synthesis(workspace)
    assert envelope["schema_version"] == 2
    work_items: list[dict[str, Any]] = envelope["work_items"]
    # A task's earlier interruption/failure/scope must be considered with its later turns.
    # Splitting them into separate work items would conceal the semantic regression.
    for session_ref, turn_count, expected_type in _EXPECTED_DISPOSITIONS:
        expected_turns = {(session_ref, f"T{number:04d}") for number in range(1, turn_count + 1)}
        matching = [
            item
            for item in work_items
            if {(ref["session_ref"], ref["turn_ref"]) for ref in item["covered_turns"]}
            == expected_turns
        ]
        assert len(matching) == 1, f"expected one whole task for {session_ref}: {work_items!r}"
        item = matching[0]
        assert item["kind"] == "material_work_item"
        assert isinstance(parse_work_item(item), ParsedWorkItem)
        disposition = item["disposition"]
        assert disposition["type"] == expected_type, f"{session_ref}: {disposition!r}"
        assert disposition["scope"].strip()
        assert disposition["summary"].strip()
        cited = {(ref["session_ref"], ref["turn_ref"]) for ref in disposition["evidence_refs"]}
        assert cited <= expected_turns
        assert (session_ref, f"T{turn_count:04d}") in cited
    assert len(work_items) == len(_EXPECTED_DISPOSITIONS)
