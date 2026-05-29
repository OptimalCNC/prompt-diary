"""Shared CLI helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, NoReturn

import typer

if TYPE_CHECKING:
    from collections.abc import Iterable

    from prompt_diary.errors import PromptDiaryError

DateOption = Annotated[str | None, typer.Option(help="Target local date in YYYY-MM-DD format.")]
TodayOption = Annotated[bool, typer.Option(help="Target the current local day.")]
TimezoneOption = Annotated[
    str | None,
    typer.Option(help="IANA timezone name, e.g. Asia/Shanghai."),
]


def echo_messages(messages: Iterable[str]) -> None:
    """Print workflow messages."""
    for message in messages:
        typer.echo(message)


def exit_with_error(exc: PromptDiaryError) -> NoReturn:
    """Print an actionable error and exit with the workflow error code."""
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(2) from exc
