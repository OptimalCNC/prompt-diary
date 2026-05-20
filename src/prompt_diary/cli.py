"""Typer command-line entrypoint for Prompt Diary."""

from __future__ import annotations

from typing import Annotated, NoReturn

import typer

from prompt_diary import __version__
from prompt_diary.api import generate_prompt_diary, prepare_prompt_diary
from prompt_diary.errors import PromptDiaryError

app = typer.Typer(
    add_completion=False,
    help="Prepare and generate evidence-backed prompt diary reports.",
    invoke_without_command=True,
    no_args_is_help=True,
)

DateOption = Annotated[str | None, typer.Option(help="Target local date in YYYY-MM-DD format.")]
TodayOption = Annotated[bool, typer.Option(help="Target the current local day.")]
TimezoneOption = Annotated[
    str | None,
    typer.Option(help="IANA timezone name, e.g. Asia/Shanghai."),
]
ForceOption = Annotated[bool, typer.Option(help="Recreate an existing workspace.")]
VersionOption = Annotated[
    bool,
    typer.Option("--version", help="Show the version and exit.", is_eager=True),
]


@app.callback()
def app_callback(*, version: VersionOption = False) -> None:
    """Prompt Diary command group."""
    if version:
        typer.echo(__version__)
        raise typer.Exit


@app.command()
def prepare(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    force: ForceOption = False,
) -> None:
    """Prepare a prompt diary workspace."""
    try:
        result = prepare_prompt_diary(
            date=date,
            today=today,
            timezone_name=timezone,
            force=force,
        )
    except PromptDiaryError as exc:
        _exit_with_error(exc)
    for message in result.messages:
        typer.echo(message)


@app.command()
def generate(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
) -> None:
    """Generate and validate a prompt diary report."""
    try:
        result = generate_prompt_diary(date=date, today=today, timezone_name=timezone)
    except PromptDiaryError as exc:
        _exit_with_error(exc)
    for message in result.messages:
        typer.echo(message)


def _exit_with_error(exc: PromptDiaryError) -> NoReturn:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(2) from exc


def main() -> None:
    app()
