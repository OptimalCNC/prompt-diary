"""Shared CLI helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import typer

from prompt_diary.progress.console import build_reporter
from prompt_diary.progress.reporter import RecordingProgressReporter, select_reporter_mode

if TYPE_CHECKING:
    from collections.abc import Iterable

    from prompt_diary.errors import PromptDiaryError

DateOption = Annotated[str | None, typer.Option(help="Target local date in YYYY-MM-DD format.")]
TodayOption = Annotated[bool, typer.Option(help="Target the current local day.")]
TimezoneOption = Annotated[
    str | None,
    typer.Option(help="IANA timezone name, e.g. Asia/Shanghai."),
]
QuietOption = Annotated[bool, typer.Option(help="Suppress progress; print only the final summary.")]
ReportsRootOption = Annotated[
    Path | None,
    typer.Option(
        help=(
            "Directory for report workspaces. Defaults to the per-user data directory "
            "(override with $PROMPT_DIARY_HOME)."
        ),
    ),
]


def build_cli_reporter(*, quiet: bool) -> RecordingProgressReporter:
    """Build the progress reporter for a CLI invocation."""
    mode = select_reporter_mode(quiet=quiet, isatty=sys.stderr.isatty())
    return RecordingProgressReporter(inner=build_reporter(mode))


def echo_messages(messages: Iterable[str]) -> None:
    """Print workflow messages."""
    for message in messages:
        typer.echo(message)


def exit_with_error(exc: PromptDiaryError) -> NoReturn:
    """Print an actionable error and exit with the workflow error code."""
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(2) from exc
