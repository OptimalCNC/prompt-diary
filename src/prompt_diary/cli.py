"""Typer command-line entrypoint for Prompt Diary."""

from __future__ import annotations

from typing import Annotated

import typer

from prompt_diary import __version__
from prompt_diary.cmds import config, generate, mcp, prepare, prompts

app = typer.Typer(
    add_completion=False,
    help="Prepare and generate evidence-backed prompt diary reports.",
    invoke_without_command=True,
    no_args_is_help=True,
)

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


prepare.register(app)
generate.register(app)
config.register(app)
prompts.register(app)
mcp.register(app)


def main() -> None:
    app()
