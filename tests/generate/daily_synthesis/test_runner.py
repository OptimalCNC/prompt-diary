"""Tests for the daily-synthesis runner orchestration against a mock agent.

The runner runs Build, the per-project summary passes, the engagement and team-learning passes, and
Finalize. These tests drive it with a prompt-reading fake agent that fills each slot through the
real write tools, so the runner's sequencing, slot checks, and the empty-report short-circuit are
exercised end to end without Codex. The runner builds only the model (``daily-report.json``); the
separate Rendering phase projects it into the reader-facing views (see
``tests/generate/rendering/test_runner.py``).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.agent_retry import AgentRetryPolicy
from prompt_diary.generate.daily_synthesis.runner import DailySynthesisRunner
from prompt_diary.generate.pipeline import (
    TaskSpec,
    daily_report_model_artifact,
    daily_synthesis_task_id,
)
from tests.support.daily_synthesis import (
    PROJECT_KEY,
    TWO_PROJECTS_KEY_A,
    TWO_PROJECTS_KEY_B,
    copy_basic_daily_workspace,
    copy_corrupt_daily_workspace,
    copy_two_projects_daily_workspace,
    daily_report_path,
    empty_daily_workspace,
    load_daily_report,
    rewrite_envelope_gap_only,
)
from tests.support.daily_synthesis_agent import DailySynthesisAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult

_FAST_RETRY_POLICY = AgentRetryPolicy(initial_backoff_seconds=0.0, max_backoff_seconds=0.0)


def _task() -> TaskSpec:
    return TaskSpec(
        task_id=daily_synthesis_task_id(),
        kind="daily_synthesis",
        output_artifacts=(daily_report_model_artifact(),),
    )


def _run(factory: DailySynthesisAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = DailySynthesisRunner(agent_factory=factory, retry_policy=_FAST_RETRY_POLICY)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(workspace_path=workspace, task=_task())

    return asyncio.run(run())


# --- happy path ----------------------------------------------------------------------------------


def test_runner_fills_all_slots(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    report = load_daily_report(workspace)
    assert report["report_title"] is not None
    assert report["projects"][0]["summary"] is not None
    assert report["engagement_assessment"] is not None
    assert report["team_learning"] is not None
    assert report["overall_confidence"] is not None


def test_runner_returns_the_model_output_artifact(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    result = _run(DailySynthesisAgentSessionFactory(), workspace)

    paths = {str(artifact.path) for artifact in result.output_artifacts}
    assert paths == {"daily-report.json"}


def test_runner_runs_one_pass_per_project_plus_two(tmp_path: Path) -> None:
    # The basic fixture has one work-bearing project: one summary pass + title + engagement +
    # team-learning.
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    _run(factory, workspace)

    assert len(factory.runners) == 4
    assert len(factory.prompts) == 4


def test_runner_reuses_completed_daily_slots(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    first_factory = DailySynthesisAgentSessionFactory()
    assert _run(first_factory, workspace).status == "success"
    report_before = load_daily_report(workspace)
    second_factory = DailySynthesisAgentSessionFactory()

    result = _run(second_factory, workspace)

    assert result.status == "success"
    assert second_factory.runners == []
    assert load_daily_report(workspace) == report_before


def test_runner_ignores_malformed_existing_daily_report(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    daily_report_path(workspace).write_text("{", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 4


def test_runner_handles_existing_report_missing_project_entry(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    daily_report_path(workspace).write_text(
        json.dumps({"schema_version": 1, "projects": []}, indent=2) + "\n",
        encoding="utf-8",
    )
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 4


@pytest.mark.parametrize(
    ("case_name", "citation_update"),
    [
        ("missing-project", {"project_key": None}),
        ("wrong-project", {"project_key": "Other-000000000000"}),
        ("uncommitted-turn", {"turn_ref": "T0003", "lines": "13-15"}),
    ],
)
def test_runner_discards_unsound_existing_project_summary_citations(
    tmp_path: Path,
    case_name: str,
    citation_update: dict[str, str | None],
) -> None:
    workspace = copy_basic_daily_workspace(tmp_path / case_name)
    assert _run(DailySynthesisAgentSessionFactory(), workspace).status == "success"
    report = load_daily_report(workspace)
    citation = report["projects"][0]["summary"]["citations"][0]
    for key, value in citation_update.items():
        if value is None:
            del citation[key]
        else:
            citation[key] = value
    daily_report_path(workspace).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert any("write_project_summary" in prompt for prompt in factory.prompts)


def test_runner_skips_engagement_but_runs_missing_team_learning(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    assert _run(DailySynthesisAgentSessionFactory(), workspace).status == "success"
    report = load_daily_report(workspace)
    report["team_learning"] = None
    daily_report_path(workspace).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 1
    assert "write_team_learning" in factory.prompts[0]


def test_runner_regenerates_invalid_existing_slot(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    assert _run(DailySynthesisAgentSessionFactory(), workspace).status == "success"
    report = load_daily_report(workspace)
    report["engagement_assessment"]["overall_reading"]["citations"] = []
    daily_report_path(workspace).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 1
    assert "write_engagement" in factory.prompts[0]
    assert load_daily_report(workspace)["engagement_assessment"] is not None


def test_runner_discards_daily_slots_when_project_work_items_change(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    assert _run(DailySynthesisAgentSessionFactory(), workspace).status == "success"
    envelope_path = workspace / "projects" / PROJECT_KEY / "project-synthesis.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["work_items"][0]["title"] = "Simplify the MCP evidence tools after resume"
    envelope_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 4
    assert any("write_project_summary" in prompt for prompt in factory.prompts)
    assert any("write_report_title" in prompt for prompt in factory.prompts)
    assert any("write_engagement" in prompt for prompt in factory.prompts)
    assert any("write_team_learning" in prompt for prompt in factory.prompts)


def _summary_pass_project_keys(factory: DailySynthesisAgentSessionFactory) -> list[str]:
    keys: list[str] = []
    for prompt in factory.prompts:
        if "write_project_summary" not in prompt:
            continue
        match = re.search(r"^- Project key: (.+)$", prompt, re.MULTILINE)
        assert match is not None
        keys.append(match.group(1).strip())
    return keys


def test_runner_two_projects_runs_two_summaries_plus_two(tmp_path: Path) -> None:
    # Two work-bearing projects: one summary pass each, plus the shared title, engagement, and
    # team-learning passes — five passes total, each summary pass naming a distinct project.
    workspace = copy_two_projects_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 5
    assert sorted(_summary_pass_project_keys(factory)) == [
        TWO_PROJECTS_KEY_A,
        TWO_PROJECTS_KEY_B,
    ]


def test_runner_two_projects_fills_both_summaries(tmp_path: Path) -> None:
    workspace = copy_two_projects_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    report = load_daily_report(workspace)
    summaries = {
        project["project_key"]: project["summary"] is not None for project in report["projects"]
    }
    assert summaries == {TWO_PROJECTS_KEY_A: True, TWO_PROJECTS_KEY_B: True}
    assert report["report_title"] is not None
    assert report["engagement_assessment"] is not None
    assert report["team_learning"] is not None


def test_runner_report_title_prompt_uses_summary_context(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    _run(factory, workspace)

    title_prompts = [prompt for prompt in factory.prompts if "write_report_title" in prompt]
    assert len(title_prompts) == 1
    prompt = title_prompts[0]
    assert f"Summary of {PROJECT_KEY} for the day." in prompt
    assert "Simplify the MCP evidence tools and drop chain_ref" in prompt
    assert "Please simplify the MCP evidence tools" not in prompt


def test_runner_uses_a_fresh_conversation_per_pass(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    _run(factory, workspace)

    # Each pass is its own agent conversation, each running exactly one turn.
    assert [len(runner.prompts) for runner in factory.runners] == [1, 1, 1, 1]


def test_runner_resumes_failed_pass_on_same_runner(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(fail_once_passes=frozenset({"engagement"}))

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 4
    assert [len(runner.prompts) for runner in factory.runners] == [1, 1, 2, 1]
    assert "Continue the same engagement pass" in factory.runners[2].prompts[1]
    report = load_daily_report(workspace)
    assert report["engagement_assessment"] is not None


def test_runner_uses_medium_reasoning_effort_by_default(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    _run(factory, workspace)

    assert factory.runners[0].config.reasoning_effort == "medium"


def test_runner_reasoning_effort_is_overridable(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()
    runner = DailySynthesisRunner(agent_factory=factory, reasoning_effort="high")

    async def run() -> None:
        async with factory:
            await runner.run(workspace_path=workspace, task=_task())

    asyncio.run(run())

    assert factory.runners[0].config.reasoning_effort == "high"


# --- pass failures -------------------------------------------------------------------------------


def test_runner_fails_when_summary_pass_writes_nothing(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"project_summary"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("summary" in error and PROJECT_KEY in error for error in result.errors)


def test_runner_fails_when_engagement_pass_writes_nothing(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"engagement"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("engagement_assessment" in error for error in result.errors)
    # Summary and title ran, the engagement pass ran (and wrote nothing), team-learning never ran.
    assert len(factory.runners) == 3


def test_runner_fails_when_report_title_pass_writes_nothing(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"report_title"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("report_title" in error for error in result.errors)
    # The title pass ran after the summary and blocked later report-level passes.
    assert len(factory.runners) == 2


def test_runner_fails_when_team_learning_pass_writes_nothing(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"team_learning"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("team_learning" in error for error in result.errors)
    assert len(factory.runners) == 4


def test_runner_build_raises_on_corrupt_envelope(tmp_path: Path) -> None:
    # Build fails loudly on a structurally-invalid post-synthesis work item (a non-controlled kind);
    # the error propagates out of run (the pipeline marks the task failed) before any pass runs.
    workspace = copy_corrupt_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()
    runner = DailySynthesisRunner(agent_factory=factory)

    async def run() -> None:
        async with factory:
            await runner.run(workspace_path=workspace, task=_task())

    with pytest.raises(PromptDiaryError):
        asyncio.run(run())

    # Build raised before any pass ran, so no synthesize conversation was minted.
    assert factory.runners == []


def test_runner_fails_when_finalize_rejects(tmp_path: Path) -> None:
    # All three passes write valid slots, but the team-learning turn leaves a malformed citation
    # (missing its resolved lines); Finalize rejects, and the runner surfaces that as a failure.
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(tamper_citation=True)

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert result.errors


# --- empty report --------------------------------------------------------------------------------


def test_runner_empty_workspace_runs_no_passes(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    # No work item anywhere: not one synthesize pass runs.
    assert factory.runners == []
    report = load_daily_report(workspace)
    assert report["engagement_assessment"] is None
    assert report["team_learning"] is None
    assert report["overall_confidence"] is None


def test_runner_gap_only_project_runs_no_passes(tmp_path: Path) -> None:
    # A project whose only work item is an evidence_gap_item has no committed, citable turn: the
    # runner must treat it as no reportable work — no summary, engagement, or team-learning pass —
    # rather than failing on a summary it could never cite.
    workspace = copy_basic_daily_workspace(tmp_path)
    rewrite_envelope_gap_only(workspace)
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert factory.runners == []
    report = load_daily_report(workspace)
    assert report["projects"][0]["summary"] is None
    assert report["engagement_assessment"] is None
    assert report["team_learning"] is None
    assert report["overall_confidence"] is None
