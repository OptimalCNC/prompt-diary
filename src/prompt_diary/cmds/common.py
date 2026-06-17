"""Shared CLI helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import wraps
from inspect import Parameter, Signature, signature
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn, TypeAlias, cast, get_type_hints

import click
import typer
from typer.core import TyperCommand, TyperGroup

import prompt_diary.targeting.resolve as target_resolution
from prompt_diary.config import (
    NOTION_DATABASE_ENV,
    NOTION_TOKEN_ENV,
    load_config,
    resolve_reports_root,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.paths import REPORTS_HOME_ENV
from prompt_diary.prepare.workspace import workspace_path_for_target
from prompt_diary.progress.console import build_reporter
from prompt_diary.progress.reporter import RecordingProgressReporter, select_reporter_mode

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime
    from typing import Protocol

    from prompt_diary.models import ReportTarget

    class _SignatureAwareCallable(Protocol):
        __signature__: Signature

        def __call__(self, *args: object, **kwargs: object) -> object: ...


DateOption: TypeAlias = Annotated[
    str | None, typer.Option(help="Target local date in YYYY-MM-DD format.")
]
TodayOption: TypeAlias = Annotated[bool, typer.Option(help="Target the current local day.")]
TimezoneOption: TypeAlias = Annotated[
    str | None,
    typer.Option(help="IANA timezone name, e.g. Asia/Shanghai."),
]
QuietOption: TypeAlias = Annotated[
    bool, typer.Option(help="Suppress progress; print only the final summary.")
]
ReportsRootOption: TypeAlias = Annotated[
    Path | None,
    typer.Option(help="Reports root containing work/<YYYY-MM-DD> workspaces."),
]
WorkspaceOption: TypeAlias = Annotated[
    Path | None,
    typer.Option("--workspace", help="Existing prepared workspace path to generate from."),
]

_BASE_HELP_ATTRIBUTE = "_prompt_diary_base_help"


@dataclass(frozen=True)
class _TimezoneEnvState:
    name: str
    value: str | None
    unusable_help: str | None
    blocking_help: str | None


@dataclass(frozen=True)
class _NotionSettingState:
    label: str
    env_var: str
    source: str | None
    blank_env: bool


@dataclass(frozen=True)
class CliReportTargetOptions:
    """Raw CLI date targeting options."""

    date: str | None
    today: bool
    timezone: str | None

    def resolve(self, *, now: datetime | None = None) -> ReportTarget:
        """Resolve raw CLI date options into an authoritative report target."""
        return target_resolution.resolve_report_target(
            date=self.date,
            today=self.today,
            timezone_name=self.timezone,
            now=now,
        )


@dataclass(frozen=True)
class CliWorkspaceTargetOptions:
    """Raw CLI options that identify one report workspace."""

    report_target: CliReportTargetOptions
    reports_root: Path | None
    workspace: Path | None = None

    def resolve(self, *, now: datetime | None = None) -> ResolvedCliWorkspaceTarget:
        """Resolve CLI workspace target options into typed target and path values."""
        if self.workspace is not None:
            raise PromptDiaryError(_workspace_unsupported_resolution_message())
        target = self.report_target.resolve(now=now)
        reports_root = resolve_reports_root(self.reports_root)
        return ResolvedCliWorkspaceTarget(
            target=target,
            reports_root=reports_root,
            workspace_path=workspace_path_for_target(target, reports_root=reports_root),
        )

    def resolve_generation_target(
        self, *, now: datetime | None = None
    ) -> ResolvedCliGenerationTarget:
        """Resolve generation target options into either date/root or direct workspace mode."""
        if self.workspace is not None:
            _reject_direct_workspace_conflicts(self)
            return ResolvedCliDirectWorkspaceTarget(workspace_path=self.workspace)
        return self.resolve(now=now)

    def with_reports_root(self, reports_root: Path | None) -> CliWorkspaceTargetOptions:
        """Return the same date target with a different raw reports-root option."""
        return CliWorkspaceTargetOptions(
            report_target=self.report_target,
            reports_root=reports_root,
            workspace=self.workspace,
        )

    def with_workspace(self, workspace: Path | None) -> CliWorkspaceTargetOptions:
        """Return the same date/root target with a different direct workspace option."""
        return CliWorkspaceTargetOptions(
            report_target=self.report_target,
            reports_root=self.reports_root,
            workspace=workspace,
        )


@dataclass(frozen=True)
class ResolvedCliWorkspaceTarget:
    """Resolved report workspace target values for command handlers."""

    target: ReportTarget
    reports_root: Path
    workspace_path: Path


@dataclass(frozen=True)
class ResolvedCliDirectWorkspaceTarget:
    """Resolved direct prepared workspace target for generation commands."""

    workspace_path: Path


ResolvedCliGenerationTarget: TypeAlias = (
    ResolvedCliWorkspaceTarget | ResolvedCliDirectWorkspaceTarget
)


class DynamicDefaultsTyperGroup(TyperGroup):
    """Typer group that refreshes selected option help at render time."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        refresh_dynamic_default_help(self.params)
        super().format_help(ctx, formatter)


class DynamicDefaultsTyperCommand(TyperCommand):
    """Typer command that refreshes selected option help at render time."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        refresh_dynamic_default_help(self.params)
        super().format_help(ctx, formatter)


def workspace_target_command(
    app: typer.Typer,
    callback: Callable[..., object],
    *,
    name: str | None = None,
    cls: type[TyperCommand] | None = None,
    include_workspace: bool = False,
) -> Callable[..., object]:
    """Register a command that receives one typed workspace target option object."""
    wrapper = _workspace_target_wrapper(callback, include_workspace=include_workspace)
    app.command(name=name, cls=cls)(wrapper)
    return wrapper


def workspace_target_callback(
    app: typer.Typer,
    callback: Callable[..., object],
    *,
    include_workspace: bool = False,
) -> Callable[..., object]:
    """Register a group callback that receives one typed workspace target option object."""
    wrapper = _workspace_target_wrapper(callback, include_workspace=include_workspace)
    app.callback()(wrapper)
    return wrapper


def _workspace_target_wrapper(
    callback: Callable[..., object],
    *,
    include_workspace: bool,
) -> Callable[..., object]:
    callback_signature = signature(callback)
    callback_type_hints = get_type_hints(callback, include_extras=True)
    wrapper_signature = _workspace_target_wrapper_signature(
        callback_signature,
        callback_type_hints=callback_type_hints,
        include_workspace=include_workspace,
    )

    @wraps(callback)
    def wrapper(*args: object, **kwargs: object) -> object:
        bound = wrapper_signature.bind(*args, **kwargs)
        bound.apply_defaults()
        date = cast("str | None", bound.arguments.pop("date"))
        today = cast("bool", bound.arguments.pop("today"))
        timezone = cast("str | None", bound.arguments.pop("timezone"))
        reports_root = cast("Path | None", bound.arguments.pop("reports_root"))
        workspace = (
            cast("Path | None", bound.arguments.pop("workspace")) if include_workspace else None
        )
        target_options = CliWorkspaceTargetOptions(
            report_target=CliReportTargetOptions(
                date=date,
                today=today,
                timezone=timezone,
            ),
            reports_root=reports_root,
            workspace=workspace,
        )
        callback_kwargs = dict(bound.arguments)
        callback_kwargs["target_options"] = target_options
        return callback(**callback_kwargs)

    signature_wrapper = cast("_SignatureAwareCallable", wrapper)
    signature_wrapper.__signature__ = wrapper_signature
    return wrapper


def _workspace_target_wrapper_signature(
    callback_signature: Signature,
    *,
    callback_type_hints: dict[str, object],
    include_workspace: bool,
) -> Signature:
    parameters: list[Parameter] = []
    for parameter in callback_signature.parameters.values():
        if parameter.name == "target_options":
            parameters.extend(
                _workspace_target_option_parameters(include_workspace=include_workspace)
            )
        else:
            parameters.append(
                parameter.replace(
                    annotation=callback_type_hints.get(parameter.name, parameter.annotation)
                )
            )
    if len(parameters) == len(callback_signature.parameters):
        raise ValueError(_missing_target_options_parameter_message())
    return Signature(
        parameters=parameters,
        return_annotation=callback_type_hints.get("return", callback_signature.return_annotation),
    )


def _missing_target_options_parameter_message() -> str:
    return "workspace target callbacks must accept a target_options parameter"


def _workspace_target_option_parameters(*, include_workspace: bool) -> tuple[Parameter, ...]:
    parameters = (
        Parameter(
            "date",
            kind=Parameter.KEYWORD_ONLY,
            default=None,
            annotation=DateOption,
        ),
        Parameter(
            "today",
            kind=Parameter.KEYWORD_ONLY,
            default=False,
            annotation=TodayOption,
        ),
        Parameter(
            "timezone",
            kind=Parameter.KEYWORD_ONLY,
            default=None,
            annotation=TimezoneOption,
        ),
        Parameter(
            "reports_root",
            kind=Parameter.KEYWORD_ONLY,
            default=None,
            annotation=ReportsRootOption,
        ),
    )
    if not include_workspace:
        return parameters
    return (
        *parameters,
        Parameter(
            "workspace",
            kind=Parameter.KEYWORD_ONLY,
            default=None,
            annotation=WorkspaceOption,
        ),
    )


def _reject_direct_workspace_conflicts(target_options: CliWorkspaceTargetOptions) -> None:
    conflicts: list[str] = []
    if target_options.report_target.date is not None:
        conflicts.append("--date")
    if target_options.report_target.today:
        conflicts.append("--today")
    if target_options.report_target.timezone is not None:
        conflicts.append("--timezone")
    if target_options.reports_root is not None:
        conflicts.append("--reports-root")
    if conflicts:
        raise PromptDiaryError(_direct_workspace_conflict_message(tuple(conflicts)))


def _direct_workspace_conflict_message(conflicts: tuple[str, ...]) -> str:
    joined = ", ".join(conflicts)
    return f"--workspace cannot be combined with {joined}."


def _workspace_unsupported_resolution_message() -> str:
    return "--workspace can only be resolved by generation commands"


def refresh_dynamic_default_help(params: list[click.Parameter]) -> None:
    """Append effective runtime defaults to shared targeting option help."""
    defaults = _dynamic_default_help_by_option()
    for param in params:
        if not isinstance(param, click.Option):
            continue
        if param.name == "notion":
            default_help = _notion_default_help(param)
        elif param.name in defaults:
            default_help = defaults[param.name]
        else:
            continue
        if default_help:
            base_help = getattr(param, _BASE_HELP_ATTRIBUTE, param.help or "")
            setattr(param, _BASE_HELP_ATTRIBUTE, base_help)
            param.help = f"{base_help} {default_help}".strip()


def _dynamic_default_help_by_option() -> dict[str, str]:
    timezone_name, timezone_source = _default_timezone_name_and_source()
    if timezone_name is None:
        date_help = (
            "If neither --date nor --today is passed, the default date cannot be computed until "
            "a valid timezone default is available."
        )
        timezone_help = _ensure_sentence(f"No default timezone is available {timezone_source}")
    else:
        target = target_resolution.resolve_report_target(
            date=None,
            today=False,
            timezone_name=timezone_name,
        )
        date_help = (
            f"Defaults to {target.report_date.isoformat()} (yesterday in {timezone_name}) when "
            "neither --date nor --today is passed."
        )
        timezone_help = _ensure_sentence(f"Defaults to {timezone_name} {timezone_source}")
    reports_root = resolve_reports_root(None)
    reports_root_source = _reports_root_source(reports_root)
    return {
        "date": date_help,
        "timezone": timezone_help,
        "reports_root": _ensure_sentence(f"Defaults to {reports_root} {reports_root_source}"),
    }


def _default_timezone_name_and_source() -> tuple[str | None, str]:
    env_states = tuple(
        _timezone_env_state(env_var) for env_var in target_resolution.TIMEZONE_ENV_VARS
    )
    for index, state in enumerate(env_states):
        if state.value is not None:
            target = target_resolution.resolve_report_target(
                date=None,
                today=True,
                timezone_name=state.value,
            )
            details = _timezone_env_status_before_selected(
                env_states[:index]
            ) + _timezone_env_status_after_selected(env_states[index + 1 :], state.name)
            return target.timezone, _join_sentences(f"from ${state.name}", *details)
        if state.blocking_help is not None:
            details = _timezone_env_status_before_selected(
                env_states[:index]
            ) + _timezone_env_status_after_selected(env_states[index + 1 :], state.name)
            return None, _join_sentences(
                f"because {state.blocking_help}",
                *details,
                "Running without --timezone will error.",
                f"Pass --timezone or fix ${state.name}.",
            )

    system_timezone = target_resolution.detect_system_timezone_name()
    if system_timezone is not None:
        return system_timezone, _join_sentences(
            "from the system timezone", *_timezone_env_status_before_selected(env_states)
        )

    return target_resolution.DEFAULT_TIMEZONE, _join_sentences(
        "because no valid timezone override is set and no system timezone was detected",
        *_timezone_env_status_before_selected(env_states),
    )


def _reports_root_source(reports_root: Path) -> str:
    if _stripped_env_value(REPORTS_HOME_ENV) is not None:
        return f"from ${REPORTS_HOME_ENV}{_relative_path_help(reports_root)}"
    env_status = _env_status(REPORTS_HOME_ENV)
    if load_config().reports_root:
        return _join_sentences(f"from stored config{_relative_path_help(reports_root)}", env_status)
    return _join_sentences(
        "from the per-user data directory", f"{env_status} and no reports_root is stored in config"
    )


def _notion_default_help(param: click.Option) -> str:
    del param
    configured, reason = _notion_configuration_reason()
    if configured:
        return _join_sentences(f"Default now: publish because {reason}", "Pass --no-notion to skip")
    return _join_sentences(
        f"Default now: do not publish because {reason}",
        "If --notion is passed now, it will error",
    )


def _notion_configuration_reason() -> tuple[bool, str]:
    config = load_config()
    token = _notion_setting_state(
        label="Notion token",
        env_var=NOTION_TOKEN_ENV,
        stored_value=config.notion_api_key,
    )
    database = _notion_setting_state(
        label="database id",
        env_var=NOTION_DATABASE_ENV,
        stored_value=config.notion_page_id,
    )
    blank_env_details = _notion_blank_env_details(token, database)
    if token.source is not None and database.source is not None:
        return True, _join_sentences(_notion_present_reason(token, database), *blank_env_details)
    return False, _join_sentences(_notion_missing_reason(token, database), *blank_env_details)


def _notion_setting_state(
    *, label: str, env_var: str, stored_value: str | None
) -> _NotionSettingState:
    raw_env_value = os.environ.get(env_var)
    if raw_env_value is not None and raw_env_value.strip():
        return _NotionSettingState(
            label=label,
            env_var=env_var,
            source="env",
            blank_env=False,
        )
    if stored_value:
        return _NotionSettingState(
            label=label,
            env_var=env_var,
            source="config",
            blank_env=raw_env_value is not None,
        )
    return _NotionSettingState(
        label=label,
        env_var=env_var,
        source=None,
        blank_env=raw_env_value is not None,
    )


def _notion_present_reason(token: _NotionSettingState, database: _NotionSettingState) -> str:
    if token.source == "env" and database.source == "env":
        return f"${NOTION_TOKEN_ENV} and ${NOTION_DATABASE_ENV} are set"
    if token.source == "config" and database.source == "config":
        return "the Notion token and database id are stored in config"
    return f"{_notion_setting_source_phrase(token)} and {_notion_setting_source_phrase(database)}"


def _notion_setting_source_phrase(state: _NotionSettingState) -> str:
    if state.source == "env":
        return f"the {state.label} is from ${state.env_var}"
    return f"the {state.label} is stored in config"


def _notion_missing_reason(token: _NotionSettingState, database: _NotionSettingState) -> str:
    missing = tuple(state for state in (token, database) if state.source is None)
    if len(missing) == 2:
        return "no Notion token or database id resolves"
    state = missing[0]
    return f"no {state.label} resolves"


def _notion_blank_env_details(*states: _NotionSettingState) -> tuple[str, ...]:
    return tuple(f"${state.env_var} is set but blank" for state in states if state.blank_env)


def _timezone_env_state(name: str) -> _TimezoneEnvState:
    value = os.environ.get(name)
    if value is None:
        return _TimezoneEnvState(name=name, value=None, unusable_help=None, blocking_help=None)
    normalized = target_resolution.normalize_timezone_env_value(value)
    if normalized is not None:
        if not target_resolution.is_known_timezone_name(normalized):
            return _TimezoneEnvState(
                name=name,
                value=None,
                unusable_help=None,
                blocking_help=f"${name}={normalized} is not a known IANA timezone name",
            )
        return _TimezoneEnvState(
            name=name, value=normalized, unusable_help=None, blocking_help=None
        )

    stripped = value.strip()
    if not stripped:
        return _TimezoneEnvState(
            name=name,
            value=None,
            unusable_help=f"${name} is set but blank.",
            blocking_help=None,
        )
    return _TimezoneEnvState(
        name=name,
        value=None,
        unusable_help=(
            f"${name} is not used because it is POSIX TZ syntax ({stripped}), not an "
            "IANA timezone name."
        ),
        blocking_help=None,
    )


def _timezone_env_status_before_selected(
    states: tuple[_TimezoneEnvState, ...],
) -> tuple[str, ...]:
    return tuple(
        state.unusable_help if state.unusable_help is not None else f"${state.name} is unset."
        for state in states
    )


def _timezone_env_status_after_selected(
    states: tuple[_TimezoneEnvState, ...], selected_name: str
) -> tuple[str, ...]:
    statuses: list[str] = []
    for state in states:
        raw_value = os.environ.get(state.name)
        if raw_value is None:
            statuses.append(f"${state.name} is unset.")
        elif raw_value.strip():
            statuses.append(
                f"${state.name} is set but not used because ${selected_name} takes precedence."
            )
        else:
            statuses.append(
                f"${state.name} is set but blank and is not used because ${selected_name} "
                "takes precedence."
            )
    return tuple(statuses)


def _stripped_env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _env_status(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return f"${name} is unset"
    return f"${name} is set but blank"


def _relative_path_help(path: Path) -> str:
    return " (relative to the current working directory)" if not path.is_absolute() else ""


def _join_sentences(first: str, *rest: str) -> str:
    return " ".join(_ensure_sentence(part) for part in (first, *rest) if part)


def _ensure_sentence(text: str) -> str:
    return text if text.endswith(".") else f"{text}."


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
