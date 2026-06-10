"""Tests for the rendering-phase runner against a real daily-report.json model.

The rendering phase is deterministic and agent-free: given a finalized ``daily-report.json`` it
writes the reader-facing views ``report.md`` and ``report.notion.json``, transactionally (a failed
render leaves neither). These tests build a real model via the daily-synthesis support helpers, then
drive :class:`RenderingRunner` directly.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from prompt_diary.generate.pipeline import (
    TaskSpec,
    markdown_report_artifact,
    notion_report_artifact,
    rendering_task_id,
)
from prompt_diary.generate.rendering.runner import RenderingRunner
from tests.support.daily_synthesis import (
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    empty_daily_workspace,
    fill_synthesize_slots,
    finalize_daily_report_via_api,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult

# Raised by the fake renderer to exercise the runner's transactional cleanup; kept as a constant so
# the raise site avoids ruff's long-inline-message rule (TRY003).
_RENDER_FAILED = "notion render failed"


def _task() -> TaskSpec:
    return TaskSpec(
        task_id=rendering_task_id(),
        kind="rendering",
        output_artifacts=(markdown_report_artifact(), notion_report_artifact()),
    )


def _run(workspace: Path) -> TaskResult:
    return asyncio.run(RenderingRunner().run(workspace_path=workspace, task=_task()))


def _report_md(workspace: Path) -> Path:
    return workspace / "report.md"


def _report_notion(workspace: Path) -> Path:
    return workspace / "report.notion.json"


def _complete_workspace(tmp_path: Path) -> Path:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    finalize_daily_report_via_api(workspace)
    return workspace


def _empty_complete_workspace(tmp_path: Path) -> Path:
    workspace = empty_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    finalize_daily_report_via_api(workspace)
    return workspace


def test_runner_renders_both_views(tmp_path: Path) -> None:
    workspace = _complete_workspace(tmp_path)

    result = _run(workspace)

    assert result.status == "success"
    assert _report_md(workspace).read_text(encoding="utf-8").strip()
    payload = json.loads(_report_notion(workspace).read_text(encoding="utf-8"))
    assert payload["title"] == "Evidence Tools and QA Strategy"
    assert payload["children"]


def test_runner_returns_both_view_artifacts(tmp_path: Path) -> None:
    workspace = _complete_workspace(tmp_path)

    result = _run(workspace)

    paths = {str(artifact.path) for artifact in result.output_artifacts}
    assert paths == {"report.md", "report.notion.json"}


def test_runner_clears_both_views_when_a_render_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Markdown render succeeds and writes report.md, then the Notion render fails; the runner's
    # transactional cleanup removes both before the error propagates (the pipeline turns it failed).
    workspace = _complete_workspace(tmp_path)

    def _boom(*, workspace_path: Path) -> Path:
        del workspace_path
        raise RuntimeError(_RENDER_FAILED)

    monkeypatch.setattr("prompt_diary.generate.rendering.runner.render_notion_artifact", _boom)

    with pytest.raises(RuntimeError, match=_RENDER_FAILED):
        _run(workspace)

    assert not _report_md(workspace).exists()
    assert not _report_notion(workspace).exists()


def test_runner_replaces_stale_views_before_rendering(tmp_path: Path) -> None:
    # A previous run left stale views; the runner resets them before rendering, so the new run never
    # leaves stale content behind.
    workspace = _complete_workspace(tmp_path)
    _report_md(workspace).write_text("# stale report from a previous run\n", encoding="utf-8")
    _report_notion(workspace).write_text('{"title": "stale"}\n', encoding="utf-8")

    result = _run(workspace)

    assert result.status == "success"
    assert "stale report from a previous run" not in _report_md(workspace).read_text(
        encoding="utf-8"
    )
    payload = json.loads(_report_notion(workspace).read_text(encoding="utf-8"))
    assert payload["title"] == "Evidence Tools and QA Strategy"


def test_runner_renders_empty_report_fallbacks(tmp_path: Path) -> None:
    workspace = _empty_complete_workspace(tmp_path)

    result = _run(workspace)

    assert result.status == "success"
    text = _report_md(workspace).read_text(encoding="utf-8")
    assert text.strip()
    assert "Insufficient supported engagement evidence" in text
    assert "No supported reusable agent-driving pattern" in text
