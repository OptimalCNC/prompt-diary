"""Config command registration: the `config` bootstrap wizard and inspection commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from prompt_diary.cmds.common import exit_with_error
from prompt_diary.config import (
    NOTION_DATABASE_ENV,
    NOTION_TOKEN_ENV,
    StoredConfig,
    config_path,
    load_config,
    save_config,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.notion_client_adapter import build_notion_validator
from prompt_diary.paths import REPORTS_HOME_ENV, platform_data_dir

if TYPE_CHECKING:
    from collections.abc import Callable


def register(app: typer.Typer) -> None:
    """Register configuration commands."""
    config_app = typer.Typer(help="Configure Prompt Diary credentials and settings.")
    config_app.command(name="init")(config_init)
    config_app.command(name="show")(config_show)
    config_app.command(name="path")(config_path_command)
    app.add_typer(config_app, name="config")


def config_init() -> None:
    """Configure credentials and settings interactively, validating the Notion token live."""
    try:
        current = load_config()

        def _verify_token(token: str) -> None:
            build_notion_validator(token=token).verify_token()

        token = _prompt_until_valid(
            "Notion integration token (NOTION_API_KEY)",
            hide_input=True,
            current=current.notion_api_key,
            validate=_verify_token,
        )
        reports_root = _prompt_reports_root(current)
        # Rebuild the validator with the accepted token so the database check authenticates with it.
        validator = build_notion_validator(token=token)
        page_id = _prompt_until_valid(
            "Notion database id (NOTION_PAGE_ID)",
            hide_input=False,
            current=current.notion_page_id,
            validate=validator.verify_database,
        )
        path = save_config(
            StoredConfig(reports_root=reports_root, notion_api_key=token, notion_page_id=page_id)
        )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    typer.echo(f"Saved configuration to {path}")


def config_show() -> None:
    """Print the stored configuration (the Notion token is masked) and the config file path."""
    try:
        config = load_config()
    except PromptDiaryError as exc:
        exit_with_error(exc)
    typer.echo(f"Config file: {config_path()}")
    typer.echo(f"Notion integration token (NOTION_API_KEY): {_mask(config.notion_api_key)}")
    typer.echo(f"Notion database id (NOTION_PAGE_ID): {config.notion_page_id or '(unset)'}")
    typer.echo(f"Data folder: {config.reports_root or '(default: per-user data dir)'}")
    overrides = [
        env
        for env in (NOTION_TOKEN_ENV, NOTION_DATABASE_ENV, REPORTS_HOME_ENV)
        if os.environ.get(env)
    ]
    if overrides:
        typer.echo(
            f"Note: these environment variables override the stored config: {', '.join(overrides)}"
        )


def config_path_command() -> None:
    """Print the config file path."""
    typer.echo(str(config_path()))


def _prompt_until_valid(
    text: str, *, hide_input: bool, current: str | None, validate: Callable[[str], None]
) -> str:
    """Prompt until a non-empty value passes ``validate``; re-prompt otherwise."""
    # show_default=False is essential: Typer/Click would otherwise render the default in the visible
    # prompt text, which for the hidden token prompt would print the stored secret. The hint conveys
    # the keep-current affordance instead; an empty entry still returns the default.
    hint = " [enter to keep current]" if current else ""
    while True:
        value = typer.prompt(
            f"{text}{hint}", hide_input=hide_input, default=current or "", show_default=False
        ).strip()
        if not value:
            typer.echo("A value is required.", err=True)
            continue
        try:
            validate(value)
        except PromptDiaryError as exc:
            typer.echo(str(exc), err=True)
            continue
        return value


def _prompt_reports_root(current: StoredConfig) -> str | None:
    """Prompt for the data folder; return ``None`` when the user keeps the per-user data dir."""
    default_dir = platform_data_dir()
    entered = typer.prompt("Data folder", default=current.reports_root or str(default_dir)).strip()
    if Path(entered).expanduser() == default_dir:
        return None
    return entered


def _mask(token: str | None) -> str:
    return f"set ({len(token)} chars)" if token else "(unset)"
