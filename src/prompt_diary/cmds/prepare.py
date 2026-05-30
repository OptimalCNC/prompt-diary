"""Prepare command registration."""

from __future__ import annotations

from typing import Annotated

import typer

from prompt_diary.cmds.common import (
    DateOption,
    QuietOption,
    TimezoneOption,
    TodayOption,
    build_cli_reporter,
    echo_messages,
    exit_with_error,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

ForceOption = Annotated[bool, typer.Option(help="Recreate an existing workspace.")]


def register(app: typer.Typer) -> None:
    """Register prepare commands."""
    app.command()(prepare)


def prepare(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    force: ForceOption = False,
    quiet: QuietOption = False,
) -> None:
    """Prepare a prompt diary workspace."""
    try:
        target = resolve_report_target(date=date, today=today, timezone_name=timezone)
        with build_cli_reporter(quiet=quiet) as reporter:
            result = prepare_workspace(target, force=force, reporter=reporter)
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)
