"""Rendering phase runner.

The rendering phase is the deterministic, agent-free tail of generation: it reads the daily report
model (``daily-report.json``) and writes two outputs — ``report.md``, the reader-facing Markdown
view, and ``report.notion.json``, the Notion page *payload* that the publish step uploads to create
the Notion page. It owns those output artifacts — it resets them before rendering and renders both
transactionally, so a failed run leaves neither stale output behind, and a successful run leaves
both. It runs no agent passes and needs no MCP tools or prompts; the pipeline emits its phase
lifecycle like the other kinds, so the runner only renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.generate.pipeline import TaskResult
from prompt_diary.generate.rendering.render_markdown import render_report
from prompt_diary.generate.rendering.render_notion import render_notion_artifact
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskSpec
    from prompt_diary.progress.reporter import ProgressReporter

_REPORT_MD_NAME = "report.md"
_REPORT_NOTION_NAME = "report.notion.json"


@dataclass(frozen=True)
class RenderingRunner:
    """Render the daily report model into ``report.md`` + ``report.notion.json`` (no agent)."""

    async def run(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> TaskResult:
        """Render both outputs (the Markdown view and the Notion page payload) transactionally."""
        # The pipeline guarantees daily-report.json exists (the task's prerequisite check) and emits
        # this phase's lifecycle, so the runner only renders; the reporter is unused.
        del reporter
        # Render both views transactionally: clear any stale views first, then render Markdown and
        # Notion. If either renderer raises, leave neither view behind (the pipeline turns the
        # propagated error into a failed task), so a failed run never leaves report.md without its
        # report.notion.json, or vice versa.
        _reset_rendered_report(workspace_path)
        rendered = False
        try:
            render_report(workspace_path=workspace_path)
            render_notion_artifact(workspace_path=workspace_path)
            rendered = True
        finally:
            if not rendered:
                _reset_rendered_report(workspace_path)
        return TaskResult(
            task_id=task.task_id, status="success", output_artifacts=task.output_artifacts
        )


def _reset_rendered_report(workspace_path: Path) -> None:
    # Clear both rendered views (Markdown and Notion) so a run that fails before rendering leaves no
    # stale view beside the daily-report.json model it no longer matches.
    (workspace_path / _REPORT_MD_NAME).unlink(missing_ok=True)
    (workspace_path / _REPORT_NOTION_NAME).unlink(missing_ok=True)
