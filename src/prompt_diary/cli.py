"""Typer command-line entrypoint for Prompt Diary."""

from __future__ import annotations

from typing import Annotated

import typer

from prompt_diary import __version__

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
    _fail_not_implemented("prepare", date, today, timezone, force)


@app.command()
def generate(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
) -> None:
    """Generate and validate a prompt diary report."""
    _fail_not_implemented("generate", date, today, timezone)


def _fail_not_implemented(command: str, *_options: object) -> None:
    typer.echo(f"Error: {command!r} is not implemented yet", err=True)
    raise typer.Exit(2)


def main() -> None:
    app()
