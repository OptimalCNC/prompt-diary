from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from prompt_diary.cmds.generate import build_generation_workflow
from prompt_diary.progress.reporter import NULL_REPORTER
from tests.support.daily_synthesis import (
    PROJECT_KEY,
    copy_basic_daily_workspace,
    load_daily_report,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.codex_mcp


def test_real_agent_synthesizes_daily_report_for_fixture(tmp_path: Path) -> None:
    pytest.importorskip("openai_codex")
    workspace = copy_basic_daily_workspace(tmp_path)
    workflow = build_generation_workflow()

    result = workflow.run_phase(
        workspace_path=workspace,
        phase="daily",
        reporter=NULL_REPORTER,
    )

    assert result.task_result.ok
    # Daily synthesis produces only the model; the Rendering phase produces the views.
    assert (workspace / "daily-report.json").exists()

    # The three synthesize slots were filled and Finalize rolled up a confidence band.
    report = load_daily_report(workspace)
    project = next(entry for entry in report["projects"] if entry["project_key"] == PROJECT_KEY)
    assert project["summary"] is not None
    assert report["engagement_assessment"] is not None
    assert report["team_learning"] is not None
    assert report["overall_confidence"] in {"high", "medium", "low"}

    # The deterministic Rendering phase projects the synthesized model into report.md.
    render_result = workflow.run_phase(
        workspace_path=workspace,
        phase="render",
        reporter=NULL_REPORTER,
    )
    assert render_result.task_result.ok
    report_md = workspace / "report.md"
    assert report_md.exists()
    assert report_md.read_text(encoding="utf-8").strip()
