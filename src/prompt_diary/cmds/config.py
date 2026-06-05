"""Config command registration: the `config` bootstrap wizard and inspection commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import typer
from msgspec import structs

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
from prompt_diary.secret import Secret

if TYPE_CHECKING:
    from collections.abc import Callable

    from prompt_diary.generate.daily_synthesis.notion_validate import (
        NotionDatabaseInfo,
        NotionIdentity,
    )

_T = TypeVar("_T")


def register(app: typer.Typer) -> None:
    """Register configuration commands."""
    config_app = typer.Typer(help="Configure Prompt Diary credentials and settings.")
    config_app.command(name="init")(config_init)
    config_app.command(name="show")(config_show)
    config_app.command(name="path")(config_path_command)
    app.add_typer(config_app, name="config")


def config_init() -> None:
    """Configure credentials and settings interactively, validating each credential live.

    Each setting is persisted as soon as it is accepted, not in one final write at the end, so
    abandoning the wizard part way keeps what was already verified — a verified token survives even
    if the database step is interrupted.
    """
    try:
        config = load_config()

        token, identity = _prompt_token(config.notion_api_key)
        typer.echo(_describe_identity(identity))
        # A changed token invalidates any database id verified against the previous token: drop it
        # so an interrupted wizard cannot leave the new token paired with a stale, un-re-verified
        # (possibly unintended) database. It is restored only after verify_database passes below.
        carried_page_id = config.notion_page_id if token.reveal() == config.notion_api_key else None
        config = structs.replace(
            config, notion_api_key=token.reveal(), notion_page_id=carried_page_id
        )
        save_config(config)

        reports_root = _prompt_reports_root(config)
        config = structs.replace(config, reports_root=reports_root)
        save_config(config)

        # Rebuild the validator with the accepted token so the database check authenticates with it.
        validator = build_notion_validator(token=token.reveal())
        page_id, database = _prompt_until_valid(
            "Notion database id (NOTION_PAGE_ID)",
            hide_input=False,
            current=config.notion_page_id,
            validate=validator.verify_database,
        )
        typer.echo(_describe_database(database))
        config = structs.replace(config, notion_page_id=page_id)
        save_config(config)

        config = structs.replace(config, notion_reporter=_prompt_reporter(config))
        path = save_config(config)
    except PromptDiaryError as exc:
        exit_with_error(exc)
    typer.echo(f"Saved configuration to {path}")


def config_show() -> None:
    """Print the stored configuration (the Notion token is masked) and the config file path."""
    try:
        config = load_config()
        # Show the resolved default folder, not an opaque label, so the user sees the real path.
        data_folder = config.reports_root or f"{platform_data_dir()} (default; not configured)"
    except PromptDiaryError as exc:
        exit_with_error(exc)
    typer.echo(f"Config file: {config_path()}")
    typer.echo(f"Notion integration token (NOTION_API_KEY): {_mask(config.notion_api_key)}")
    typer.echo(f"Notion database id (NOTION_PAGE_ID): {config.notion_page_id or '(unset)'}")
    typer.echo(f"Reporter name (汇报人 column): {config.notion_reporter or '(unset)'}")
    typer.echo(f"Data folder: {data_folder}")
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


def _prompt_token(current: str | None) -> tuple[Secret, NotionIdentity]:
    """Prompt for and verify the Notion token, returning it wrapped so no bare token outlives this.

    The raw string is bound only inside this helper and ``_prompt_until_valid`` (where it is typed
    and verified); wrapping it before returning keeps ``config_init`` — whose frame lives across the
    later, fallible database and reporter prompts — free of a bare-token local that a
    locals-capturing traceback could surface. Reveal it only at the transient points of use there.
    """

    def _verify(token: str) -> NotionIdentity:
        return build_notion_validator(token=token).verify_token()

    raw, identity = _prompt_until_valid(
        "Notion integration token (NOTION_API_KEY)",
        hide_input=True,
        current=current,
        validate=_verify,
    )
    return Secret(raw), identity


def _prompt_until_valid(
    text: str, *, hide_input: bool, current: str | None, validate: Callable[[str], _T]
) -> tuple[str, _T]:
    """Prompt until a non-empty value passes ``validate``; return it with its result."""
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
            result = validate(value)
        except PromptDiaryError as exc:
            typer.echo(str(exc), err=True)
            continue
        return value, result


def _prompt_reports_root(current: StoredConfig) -> str | None:
    """Prompt for the data folder; return ``None`` when the user keeps the per-user data dir."""
    default_dir = platform_data_dir()
    entered = typer.prompt("Data folder", default=current.reports_root or str(default_dir)).strip()
    if Path(entered).expanduser() == default_dir:
        return None
    return entered


def _prompt_reporter(current: StoredConfig) -> str | None:
    """Prompt for the free-form reporter name written into the 汇报人 column; optional.

    The name is a personal label (like ``git config user.name``), not a credential, so it is not
    validated and not hidden. It is written into the 汇报人 text column at publish time. Pressing
    enter keeps the current value, or skips when none is stored.
    """
    hint = f" [keep {current.notion_reporter}]" if current.notion_reporter else " (enter to skip)"
    entered = typer.prompt(
        f"Reporter name for the 汇报人 column{hint}",
        default=current.notion_reporter or "",
        show_default=False,
    ).strip()
    return entered or None


def _describe_identity(identity: NotionIdentity) -> str:
    """Summarize who an accepted token authenticates as (carries no secret material)."""
    name = identity.integration_name or "(unnamed integration)"
    details: list[str] = []
    if identity.workspace_name:
        details.append(f'workspace "{identity.workspace_name}"')
    if identity.owner_type:
        details.append(f"owner type {identity.owner_type}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f'Token verified. Authenticated as Notion integration "{name}"{suffix}.'


def _describe_database(database: NotionDatabaseInfo) -> str:
    """Summarize the reachable target database behind an accepted page id."""
    title = database.title or "(untitled)"
    return f'Database verified. Connected to "{title}".'


def _mask(token: str | None) -> str:
    return f"set ({len(token)} chars)" if token else "(unset)"
