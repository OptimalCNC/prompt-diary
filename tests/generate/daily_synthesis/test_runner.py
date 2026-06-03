"""Tests for the daily-synthesis runner orchestration against a mock agent.

The runner runs Build, the per-project summary passes, the engagement and team-learning passes,
Finalize, and the Markdown render. These tests drive it with a prompt-reading fake agent that fills
each slot through the real write tools, so the runner's sequencing, slot checks, and the
empty-report short-circuit are exercised end to end without Codex.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.runner import DailySynthesisRunner
from prompt_diary.generate.pipeline import (
    TaskSpec,
    daily_report_model_artifact,
    daily_synthesis_task_id,
    markdown_report_artifact,
    notion_report_artifact,
)
from tests.support.daily_synthesis import (
    PROJECT_KEY,
    TWO_PROJECTS_KEY_A,
    TWO_PROJECTS_KEY_B,
    copy_basic_daily_workspace,
    copy_corrupt_daily_workspace,
    copy_two_projects_daily_workspace,
    empty_daily_workspace,
    load_daily_report,
    rewrite_envelope_gap_only,
)
from tests.support.daily_synthesis_agent import DailySynthesisAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult

# Raised by the fake renderer to exercise the runner's transactional render cleanup; kept as a
# constant so the raise site avoids ruff's long-inline-message rule (TRY003).
_RENDER_FAILED = "notion render failed"


def _task() -> TaskSpec:
    return TaskSpec(
        task_id=daily_synthesis_task_id(),
        kind="daily_synthesis",
        output_artifacts=(
            daily_report_model_artifact(),
            markdown_report_artifact(),
            notion_report_artifact(),
        ),
    )


def _run(factory: DailySynthesisAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = DailySynthesisRunner(agent_factory=factory)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(workspace_path=workspace, task=_task())

    return asyncio.run(run())


def _report_md(workspace: Path) -> Path:
    return workspace / "report.md"


def _report_notion(workspace: Path) -> Path:
    return workspace / "report.notion.json"


# --- happy path ----------------------------------------------------------------------------------


def test_runner_fills_all_slots_and_renders(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    report = load_daily_report(workspace)
    assert report["projects"][0]["summary"] is not None
    assert report["engagement_assessment"] is not None
    assert report["team_learning"] is not None
    assert report["overall_confidence"] is not None
    assert _report_md(workspace).read_text(encoding="utf-8").strip()


def test_runner_returns_all_output_artifacts(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    result = _run(DailySynthesisAgentSessionFactory(), workspace)

    paths = {str(artifact.path) for artifact in result.output_artifacts}
    assert paths == {"daily-report.json", "report.md", "report.notion.json"}


def test_runner_renders_notion_payload(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    result = _run(DailySynthesisAgentSessionFactory(), workspace)

    assert result.status == "success"
    # The Notion payload is written beside report.md as a well-formed page payload.
    payload = json.loads(_report_notion(workspace).read_text(encoding="utf-8"))
    assert payload["title"] == "Prompt Diary Report — 2026-05-28"
    assert payload["children"]


def test_runner_clears_stale_notion_payload_when_run_fails(tmp_path: Path) -> None:
    # A previous run left a report.notion.json; this run fails (a summary pass writes nothing), so
    # the stale Notion payload must be cleared before Build, like the stale report.md.
    workspace = copy_basic_daily_workspace(tmp_path)
    stale = _report_notion(workspace)
    stale.write_text("{}\n", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"project_summary"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert not stale.exists()


def test_runner_clears_both_reports_when_a_render_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If a renderer raises after Finalize, the run leaves neither rendered report behind: the
    # Markdown render succeeds and writes report.md, then the Notion render fails, and the runner's
    # transactional cleanup removes both before the error propagates (the pipeline turns it failed).
    workspace = copy_basic_daily_workspace(tmp_path)

    def _boom(*, workspace_path: Path) -> Path:
        del workspace_path
        raise RuntimeError(_RENDER_FAILED)

    monkeypatch.setattr(
        "prompt_diary.generate.daily_synthesis.runner.render_notion_artifact", _boom
    )

    with pytest.raises(RuntimeError, match=_RENDER_FAILED):
        _run(DailySynthesisAgentSessionFactory(), workspace)

    assert not _report_md(workspace).exists()
    assert not _report_notion(workspace).exists()


def test_runner_runs_one_pass_per_project_plus_two(tmp_path: Path) -> None:
    # The basic fixture has one work-bearing project: one summary pass + engagement + team-learning.
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    _run(factory, workspace)

    assert len(factory.runners) == 3
    assert len(factory.prompts) == 3


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
    # Two work-bearing projects: one summary pass each, plus the shared engagement and
    # team-learning passes — four passes total, each summary pass naming a distinct project.
    workspace = copy_two_projects_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert len(factory.runners) == 4
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
    assert report["engagement_assessment"] is not None
    assert report["team_learning"] is not None
    assert _report_md(workspace).read_text(encoding="utf-8").strip()


def test_runner_uses_a_fresh_conversation_per_pass(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory()

    _run(factory, workspace)

    # Each pass is its own agent conversation, each running exactly one turn.
    assert [len(runner.prompts) for runner in factory.runners] == [1, 1, 1]


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
    assert not _report_md(workspace).exists()


def test_runner_fails_when_engagement_pass_writes_nothing(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"engagement"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("engagement_assessment" in error for error in result.errors)
    assert not _report_md(workspace).exists()
    # The summary pass ran, the engagement pass ran (and wrote nothing), team-learning never ran.
    assert len(factory.runners) == 2


def test_runner_fails_when_team_learning_pass_writes_nothing(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"team_learning"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("team_learning" in error for error in result.errors)
    assert not _report_md(workspace).exists()
    assert len(factory.runners) == 3


def test_runner_clears_stale_report_md_when_run_fails(tmp_path: Path) -> None:
    # A previous successful run left a report.md; this run fails (the summary pass writes nothing),
    # so the stale rendered report must not survive beside the new, partial daily-report.json.
    workspace = copy_basic_daily_workspace(tmp_path)
    stale = _report_md(workspace)
    stale.write_text("# stale report from a previous run\n", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory(skip_pass=frozenset({"project_summary"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert not stale.exists()


def test_runner_clears_stale_report_md_when_build_raises(tmp_path: Path) -> None:
    # report.md is reset before Build, so a Build that raises on a corrupt envelope also clears a
    # stale rendered report rather than leaving it beside a now-absent/partial daily-report.json.
    # The error propagates out of run (the pipeline marks the task failed); the stale file is gone.
    workspace = copy_corrupt_daily_workspace(tmp_path)
    stale = _report_md(workspace)
    stale.write_text("# stale report from a previous run\n", encoding="utf-8")
    factory = DailySynthesisAgentSessionFactory()
    runner = DailySynthesisRunner(agent_factory=factory)

    async def run() -> None:
        async with factory:
            await runner.run(workspace_path=workspace, task=_task())

    with pytest.raises(PromptDiaryError):
        asyncio.run(run())

    assert not stale.exists()
    # Build raised before any pass ran, so no synthesize conversation was minted.
    assert factory.runners == []


def test_runner_fails_when_finalize_rejects(tmp_path: Path) -> None:
    # All three passes write valid slots, but the team-learning turn leaves a malformed citation
    # (missing its resolved lines); Finalize rejects, and the runner surfaces that as a failure and
    # does not render report.md.
    workspace = copy_basic_daily_workspace(tmp_path)
    factory = DailySynthesisAgentSessionFactory(tamper_citation=True)

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert result.errors
    assert not _report_md(workspace).exists()


# --- empty report --------------------------------------------------------------------------------


def test_runner_empty_workspace_runs_no_passes_and_renders_fallbacks(tmp_path: Path) -> None:
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
    text = _report_md(workspace).read_text(encoding="utf-8")
    assert text.strip()
    assert "Insufficient supported engagement evidence" in text
    assert "No supported reusable agent-driving pattern" in text


def test_runner_gap_only_project_runs_no_passes_and_renders_fallbacks(tmp_path: Path) -> None:
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
    text = _report_md(workspace).read_text(encoding="utf-8")
    assert text.strip()
    assert "Insufficient supported engagement evidence" in text
    assert "No supported reusable agent-driving pattern" in text
