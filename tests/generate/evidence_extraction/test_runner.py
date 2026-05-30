from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.evidence_extraction.runner import EvidenceExtractionRunner
from prompt_diary.generate.pipeline import TaskSpec, evidence_card_artifact, evidence_task_id
from tests.support.evidence_agent import EvidenceWritingAgentSessionFactory
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
    load_evidence_card,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult


def _evidence_task() -> TaskSpec:
    return TaskSpec(
        task_id=evidence_task_id(PROJECT_KEY, SESSION_REF),
        kind="evidence_extraction",
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
        output_artifacts=(evidence_card_artifact(PROJECT_KEY, SESSION_REF),),
    )


def _run(factory: EvidenceWritingAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = EvidenceExtractionRunner(agent_factory=factory)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(workspace_path=workspace, task=_evidence_task())

    return asyncio.run(run())


def test_runner_extracts_all_turns_in_index_order(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert factory.processed == [(SESSION_REF, "T0001"), (SESSION_REF, "T0002")]
    assert len(factory.runners) == 1
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]


def test_runner_second_turn_uses_next_turn_prompt_with_prior_result(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()

    _run(factory, workspace)

    prompts = factory.runners[0].prompts
    assert len(prompts) == 2
    assert "## Role" in prompts[0]
    assert "The previous turn was written successfully." in prompts[1]
    assert '"turn_ref": "T0001"' in prompts[1]
    assert '"turn_ref": "T0002"' in prompts[1]


def test_runner_resets_a_preexisting_partial_card(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_key": PROJECT_KEY,
                "session_ref": SESSION_REF,
                "evidence_chains": [{"turn_ref": "T0001", "stale": True}],
            }
        ),
        encoding="utf-8",
    )
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]
    assert all("stale" not in chain for chain in card["evidence_chains"])


def test_runner_fails_when_a_turn_is_not_committed(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory(fail_turns=frozenset({"T0002"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("T0002" in error for error in result.errors)
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001"]


def test_runner_writes_empty_card_for_zero_turn_session(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    _strip_turns_from_index(workspace)
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert factory.processed == []
    card = load_evidence_card(workspace)
    assert card["evidence_chains"] == []


def test_runner_requires_project_and_session_scope(tmp_path: Path) -> None:
    runner = EvidenceExtractionRunner(agent_factory=EvidenceWritingAgentSessionFactory())
    task = TaskSpec(task_id="evidence:x", kind="evidence_extraction")

    async def run() -> None:
        await runner.run(workspace_path=tmp_path, task=task)

    with pytest.raises(PromptDiaryError, match="requires project_key and session_ref"):
        asyncio.run(run())


def test_runner_fails_when_first_turn_not_committed(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory(fail_turns=frozenset({"T0001"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("T0001" in error for error in result.errors)
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    assert not card_path.exists()


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
