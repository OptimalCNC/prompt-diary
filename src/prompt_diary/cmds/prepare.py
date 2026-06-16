"""Prepare command registration."""

from __future__ import annotations

from typing import Annotated

import typer

from prompt_diary.cmds.common import (
    CliWorkspaceTargetOptions,
    QuietOption,
    build_cli_reporter,
    echo_messages,
    exit_with_error,
    workspace_target_command,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.prepare.workspace import prepare_workspace

ForceOption = Annotated[bool, typer.Option(help="Recreate an existing workspace.")]


def register(app: typer.Typer) -> None:
    """Register prepare commands."""
    workspace_target_command(app, prepare)


def prepare(
    *,
    target_options: CliWorkspaceTargetOptions,
    force: ForceOption = False,
    quiet: QuietOption = False,
) -> None:
    """Prepare a prompt diary workspace."""
    try:
        resolved = target_options.resolve()
        with build_cli_reporter(quiet=quiet) as reporter:
            result = prepare_workspace(
                resolved.target,
                reports_root=resolved.reports_root,
                force=force,
                reporter=reporter,
            )
            timing = reporter.timing_summary_message()
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages((*result.messages, *((timing,) if timing is not None else ())))
