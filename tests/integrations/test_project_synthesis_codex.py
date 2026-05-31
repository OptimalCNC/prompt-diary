from __future__ import annotations

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
    from pathlib import Path

pytestmark = pytest.mark.codex_mcp


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
