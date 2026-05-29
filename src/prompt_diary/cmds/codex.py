"""Codex command registration."""

from __future__ import annotations

import typer

from prompt_diary.cmds.common import echo_messages, exit_with_error
from prompt_diary.errors import PromptDiaryError
from prompt_diary.integrations.codex_bootstrap import bootstrap_codex_sdk


def register(app: typer.Typer) -> None:
    """Register Codex commands."""
    codex_app = typer.Typer(help="Manage optional Codex SDK support.")
    codex_app.command(name="bootstrap")(codex_bootstrap)
    app.add_typer(codex_app, name="codex")


def codex_bootstrap() -> None:
    """Install the optional Codex SDK into this runtime environment."""
    try:
        result = bootstrap_codex_sdk()
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)
