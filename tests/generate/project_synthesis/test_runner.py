from __future__ import annotations

import asyncio
import json
import shutil
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.pipeline import (
    TaskSpec,
    project_synthesis_artifact,
    project_synthesis_task_id,
)
from prompt_diary.generate.project_synthesis.runner import ProjectSynthesisRunner
from tests.support.project_synthesis import (
    ALL_TURNS,
    COMMITTED_TURNS,
    PROJECT_KEY,
    copy_basic_project_workspace,
    load_project_synthesis,
    synthesis_path,
)
from tests.support.project_synthesis_agent import GroupingAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult


def _task() -> TaskSpec:
    return TaskSpec(
        task_id=project_synthesis_task_id(PROJECT_KEY),
        kind="project_synthesis",
        project_key=PROJECT_KEY,
        output_artifacts=(project_synthesis_artifact(PROJECT_KEY),),
    )


def _run(factory: GroupingAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = ProjectSynthesisRunner(agent_factory=factory)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(workspace_path=workspace, task=_task())

    return asyncio.run(run())


def test_runner_covers_every_turn_and_writes_envelope(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    factory = GroupingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 1
    envelope = load_project_synthesis(workspace)
    covered = {
        (ref["session_ref"], ref["turn_ref"])
        for item in envelope["work_items"]
        for ref in item["covered_turns"]
    }
    assert covered == set(ALL_TURNS)


def test_runner_populates_source_user_messages(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    _run(GroupingAgentSessionFactory(), workspace)

    messages = load_project_synthesis(workspace)["source_user_messages"]
    assert [(entry["session_ref"], entry["turn_ref"]) for entry in messages] == list(
        COMMITTED_TURNS
    )


def test_runner_buckets_gap_turn_as_evidence_gap_item(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    _run(GroupingAgentSessionFactory(), workspace)

    envelope = load_project_synthesis(workspace)
    gap_covered = {
        (ref["session_ref"], ref["turn_ref"])
        for item in envelope["work_items"]
        if item["kind"] == "evidence_gap_item"
        for ref in item["covered_turns"]
    }
    assert ("S0001", "T0003") in gap_covered


def test_runner_resets_a_preexisting_envelope(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    path = synthesis_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_key": PROJECT_KEY,
                "project_label": "ReportGenerator",
                "work_items": [
                    {
                        "work_item_ref": "W9999",
                        "kind": "material_work_item",
                        "title": "stale",
                        "covered_turns": [],
                        "confidence": "low",
                    }
                ],
                "source_user_messages": [],
            }
        ),
        encoding="utf-8",
    )

    result = _run(GroupingAgentSessionFactory(), workspace)

    assert result.status == "success"
    refs = [item["work_item_ref"] for item in load_project_synthesis(workspace)["work_items"]]
    assert "W9999" not in refs


def test_runner_fails_when_a_turn_is_left_uncovered(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = _run(GroupingAgentSessionFactory(cover_gaps=False), workspace)

    assert result.status == "failed"
    assert any("S0001/T0003" in error for error in result.errors)


def test_runner_writes_empty_envelope_for_zero_turn_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    _strip_turns_from_index(workspace)
    shutil.rmtree(workspace / "projects" / PROJECT_KEY / "evidence")
    factory = GroupingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert factory.runners == []
    envelope = load_project_synthesis(workspace)
    assert envelope["work_items"] == []
    assert envelope["source_user_messages"] == []


def test_runner_requires_project_scope(tmp_path: Path) -> None:
    runner = ProjectSynthesisRunner(agent_factory=GroupingAgentSessionFactory())
    task = TaskSpec(task_id="project:x", kind="project_synthesis")

    async def run() -> None:
        await runner.run(workspace_path=tmp_path, task=task)

    with pytest.raises(PromptDiaryError, match="requires project_key"):
        asyncio.run(run())


def _strip_turns_from_index(workspace: Path) -> None:
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        row["turns"] = []
    index_path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")
