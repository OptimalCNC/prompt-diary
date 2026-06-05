"""Render command registration."""

from __future__ import annotations

import typer

from prompt_diary.cmds.common import (
    DateOption,
    ReportsRootOption,
    TimezoneOption,
    exit_with_error,
)
from prompt_diary.cmds.generate import workspace_for_existing_target
from prompt_diary.config import resolve_reports_root
from prompt_diary.errors import PromptDiaryError
from prompt_diary.render.notion import NotionRenderResult, render_workspace_report_to_notion


def register(app: typer.Typer) -> None:
    """Register render commands."""
    render_app = typer.Typer(help="Render generated report artifacts.")
    render_app.command(name="notion")(render_notion)
    app.add_typer(render_app, name="render")


def render_notion(
    *,
    date: DateOption = None,
    timezone: TimezoneOption = None,
    reports_root: ReportsRootOption = None,
) -> None:
    """Render and publish the generated report to Notion."""
    try:
        root = resolve_reports_root(reports_root)
        workspace_path = workspace_for_existing_target(
            date=date,
            today=False,
            timezone_name=timezone,
            reports_root=root,
        )
        result = render_workspace_report_to_notion(workspace_path)
    except PromptDiaryError as exc:
        exit_with_error(exc)
    typer.echo(_notion_render_message(result))


def _notion_render_message(result: NotionRenderResult) -> str:
    return f"Published report to Notion: {result.url or result.page_id}"
