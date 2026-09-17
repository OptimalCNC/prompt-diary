from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.agent_retry import AgentRetryPolicy
from prompt_diary.generate.agent_settings import AgentSettings
from prompt_diary.generate.evidence_extraction.mcp import write_evidence
from prompt_diary.generate.evidence_extraction.runner import EvidenceExtractionRunner
from prompt_diary.generate.pipeline import TaskSpec, evidence_card_artifact, evidence_task_id
from prompt_diary.progress.events import TurnAdvanced
from tests.support.evidence_agent import (
    EvidenceWritingAgentRunner,
    EvidenceWritingAgentSessionFactory,
)
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    build_evidence_chain,
    copy_basic_evidence_workspace,
    load_evidence_card,
)
from tests.support.progress import RecordingReporter

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from prompt_diary.agent import AgentTurnResult
    from prompt_diary.generate.pipeline import TaskResult

_FAST_RETRY_POLICY = AgentRetryPolicy(initial_backoff_seconds=0.0, max_backoff_seconds=0.0)


def _evidence_task() -> TaskSpec:
    return TaskSpec(
        task_id=evidence_task_id(PROJECT_KEY, SESSION_REF),
        kind="evidence_extraction",
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
        output_artifacts=(evidence_card_artifact(PROJECT_KEY, SESSION_REF),),
    )


def _run(factory: EvidenceWritingAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = EvidenceExtractionRunner(agent_factory=factory, retry_policy=_FAST_RETRY_POLICY)

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
    assert len(factory.runners) == 2
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]


def test_runner_reuses_complete_existing_card(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    first_factory = EvidenceWritingAgentSessionFactory()
    assert _run(first_factory, workspace).status == "success"
    second_factory = EvidenceWritingAgentSessionFactory()

    result = _run(second_factory, workspace)

    assert result.status == "success"
    assert second_factory.runners == []


def test_each_assignment_has_its_own_complete_conversation(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()

    _run(factory, workspace)

    assert [len(runner.prompts) for runner in factory.runners] == [1, 1]
    for runner in factory.runners:
        instructions = runner.config.base_instructions
        assert instructions is not None
        assert "## Role" in instructions
        assert "## Role" not in runner.prompts[0]
        assert PROJECT_KEY not in instructions
        assert runner.config.mcp_tools == ("read_session_lines", "write_evidence")
        assert f"- Project key: {PROJECT_KEY}" in runner.prompts[0]
        assert f"- Session reference: {SESSION_REF}" in runner.prompts[0]
    assert (
        factory.runners[0].config.base_instructions == factory.runners[1].config.base_instructions
    )
    assert factory.runners[0].target_turn is not None
    assert factory.runners[0].target_turn["turn_ref"] == "T0001"
    assert factory.runners[1].target_turn is not None
    assert factory.runners[1].target_turn["turn_ref"] == "T0002"


def test_runner_resets_a_preexisting_invalid_partial_card(tmp_path: Path) -> None:
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


def test_runner_resets_mismatched_or_extra_turn_card(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_key": "Other-000000000000",
                "session_ref": SESSION_REF,
                "evidence_chains": [
                    build_evidence_chain(turn_ref="T0001", span=(2, 8)),
                    build_evidence_chain(turn_ref="T0002", span=(9, 12), kind="no_material"),
                    build_evidence_chain(turn_ref="T9999", span=(2, 2), kind="no_material"),
                ],
            }
        ),
        encoding="utf-8",
    )
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    card = load_evidence_card(workspace)
    assert card["project_key"] == PROJECT_KEY
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]


def test_runner_fails_when_a_turn_is_not_committed(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory(fail_turns=frozenset({"T0002"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("T0002" in error for error in result.errors)
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    assert card_path.exists()
    assert [chain["turn_ref"] for chain in load_evidence_card(workspace)["evidence_chains"]] == [
        "T0001"
    ]


def test_runner_resumes_valid_partial_card_without_repeating_committed_turn(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    result = write_evidence(
        workspace_path=workspace,
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
        evidence_chain=build_evidence_chain(turn_ref="T0001", span=(2, 8)),
    )
    assert result.status == "appended"
    first_chain = load_evidence_card(workspace)["evidence_chains"][0]
    factory = EvidenceWritingAgentSessionFactory()

    assert _run(factory, workspace).status == "success"

    assert factory.processed == [(SESSION_REF, "T0002")]
    assert len(factory.runners) == 1
    assert load_evidence_card(workspace)["evidence_chains"][0] == first_chain


def test_runner_reuses_commits_after_later_turn_exhausts_retries(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    failing = EvidenceWritingAgentSessionFactory(fail_turns=frozenset({"T0002"}))
    assert _run(failing, workspace).status == "failed"
    first_chain = load_evidence_card(workspace)["evidence_chains"][0]
    recovered = EvidenceWritingAgentSessionFactory()

    assert _run(recovered, workspace).status == "success"

    assert recovered.processed == [(SESSION_REF, "T0002")]
    assert load_evidence_card(workspace)["evidence_chains"][0] == first_chain


def test_runner_fails_when_later_assignment_removes_an_earlier_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    assert (
        write_evidence(
            workspace_path=workspace,
            project_key=PROJECT_KEY,
            session_ref=SESSION_REF,
            evidence_chain=build_evidence_chain(turn_ref="T0001", span=(2, 8)),
        ).status
        == "appended"
    )
    original_turn = EvidenceWritingAgentRunner.turn

    async def clobber_previous_commit(
        self: EvidenceWritingAgentRunner,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        result = await original_turn(
            self, prompt, timeout_seconds=timeout_seconds, output_schema=output_schema
        )
        card = load_evidence_card(workspace)
        card["evidence_chains"] = [card["evidence_chains"][-1]]
        card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
        card_path.write_text(json.dumps(card), encoding="utf-8")
        return result

    monkeypatch.setattr(EvidenceWritingAgentRunner, "turn", clobber_previous_commit)
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert factory.processed == [(SESSION_REF, "T0002")]
    assert result.status == "failed"
    assert result.errors == ("missing turn_ref(s): T0001",)


@pytest.mark.parametrize("corruption", ["schema", "chain", "citation", "json"])
def test_runner_does_not_accept_invalid_committed_looking_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    original_turn = EvidenceWritingAgentRunner.turn

    async def tampered_turn(
        self: EvidenceWritingAgentRunner,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        result = await original_turn(
            self, prompt, timeout_seconds=timeout_seconds, output_schema=output_schema
        )
        card: dict[str, Any] = load_evidence_card(workspace)
        if corruption == "schema":
            card["schema_version"] = 99
        elif corruption == "chain":
            del card["evidence_chains"][0]["terminal_state"]
        elif corruption == "citation":
            card["evidence_chains"][0]["trigger"]["citations"] = [{"lines": "1-1"}]
        card_path.write_text("{" if corruption == "json" else json.dumps(card), encoding="utf-8")
        return result

    monkeypatch.setattr(EvidenceWritingAgentRunner, "turn", tampered_turn)
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert len(factory.runners[0].prompts) == 3
    assert all(turn_ref == "T0001" for _, turn_ref in factory.processed)


def test_runner_resumes_failed_turns_on_same_runner(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory(raise_once_turns=frozenset({"T0001", "T0002"}))

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 2
    assert factory.processed == [(SESSION_REF, "T0001"), (SESSION_REF, "T0002")]
    assert [len(runner.prompts) for runner in factory.runners] == [2, 2]
    for runner in factory.runners:
        assert "Continue this assigned evidence extraction turn" in runner.prompts[1]
        assert "## Role" not in runner.prompts[1]


def test_retry_reminder_does_not_append_large_original_prompt(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    project_path = workspace / "projects" / PROJECT_KEY / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    sentinel = "ASSIGNMENT_CONTEXT_SENTINEL" * 1000
    project["project_label"] = sentinel
    project_path.write_text(json.dumps(project), encoding="utf-8")
    factory = EvidenceWritingAgentSessionFactory(raise_once_turns=frozenset({"T0001"}))

    assert _run(factory, workspace).status == "success"

    initial, retry = factory.runners[0].prompts
    assert sentinel in initial
    assert "ASSIGNMENT_CONTEXT_SENTINEL" not in retry
    assert "T0001" in retry


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


def test_runner_emits_turn_advanced_per_committed_turn(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()
    reporter = RecordingReporter()
    runner = EvidenceExtractionRunner(agent_factory=factory, retry_policy=_FAST_RETRY_POLICY)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(
                workspace_path=workspace, task=_evidence_task(), reporter=reporter
            )

    result = asyncio.run(run())

    assert result.status == "success"
    turns = [event for event in reporter.events if isinstance(event, TurnAdvanced)]
    assert [(event.turn_index, event.total_turns) for event in turns] == [(1, 2), (2, 2)]
    assert [event.turn_ref for event in turns] == ["T0001", "T0002"]


def test_runner_uses_packaged_agent_settings(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()

    _run(factory, workspace)

    assert [
        (runner.config.model, runner.config.reasoning_effort) for runner in factory.runners
    ] == [
        ("gpt-5.6-terra", "medium"),
        ("gpt-5.6-terra", "medium"),
    ]


def test_runner_passes_agent_settings_to_every_conversation(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()
    runner = EvidenceExtractionRunner(
        agent_factory=factory,
        settings=AgentSettings(model="extraction-model", reasoning_effort="high"),
    )

    async def run() -> None:
        async with factory:
            await runner.run(workspace_path=workspace, task=_evidence_task())

    asyncio.run(run())

    assert [
        (runner.config.model, runner.config.reasoning_effort) for runner in factory.runners
    ] == [
        ("extraction-model", "high"),
        ("extraction-model", "high"),
    ]


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
