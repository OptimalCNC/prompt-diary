"""Generate command registration."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from prompt_diary.cmds.common import (
    DateOption,
    QuietOption,
    ReportsRootOption,
    TimezoneOption,
    TodayOption,
    build_cli_reporter,
    echo_messages,
    exit_with_error,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis import DailySynthesisRunner
from prompt_diary.generate.daily_synthesis.notion_client_adapter import build_notion_client
from prompt_diary.generate.daily_synthesis.notion_publish import publish_workspace_report
from prompt_diary.generate.evidence_extraction import EvidenceExtractionRunner
from prompt_diary.generate.project_synthesis import ProjectSynthesisRunner
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow, PhaseName
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig
from prompt_diary.mcp.codex_config import (
    codex_clean_startup_overrides,
    default_codex_home,
    prompt_diary_mcp_overrides,
)
from prompt_diary.paths import resolve_reports_root
from prompt_diary.prepare.workspace import (
    prepare_workspace,
    validate_workspace_matches_target,
    workspace_path_for_target,
)
from prompt_diary.progress.reporter import NULL_REPORTER
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.daily_synthesis.notion_publish import NotionClientProtocol
    from prompt_diary.generate.pipeline import PhaseRunner, TaskKind
    from prompt_diary.models import SourceSpec
    from prompt_diary.progress.reporter import ProgressReporter

# Environment variables that configure the optional Notion publish (`generate --notion`). These are
# the variable *names* to read from the environment, not secrets — the token value never lives here.
_NOTION_TOKEN_ENV = "NOTION_API_KEY"  # noqa: S105 - env var name to read, not a credential
_NOTION_DATABASE_ENV = "NOTION_PAGE_ID"

NotionOption = Annotated[
    bool,
    typer.Option(
        help=(
            "After generating, publish the report to the Notion database in "
            f"${_NOTION_DATABASE_ENV} (authenticating with ${_NOTION_TOKEN_ENV})."
        ),
    ),
]

GenerateProjectKeyOption = Annotated[
    str,
    typer.Option(help="Prepared project key for the phase task."),
]
GenerateSessionRefOption = Annotated[
    str,
    typer.Option(help="Prepared session reference for the evidence task."),
]


def build_generation_workflow() -> GenerateWorkspaceWorkflow:
    """Build the default generation workflow with a workspace-aware Codex backend per run."""

    def build_agent_factory(workspace_path: Path) -> AgentSessionFactory:
        codex_path = shutil.which("codex")
        return CodexAgentSessionFactory(
            CodexBackendConfig(
                codex_bin=Path(codex_path) if codex_path is not None else None,
                mcp_config_overrides=(
                    *prompt_diary_mcp_overrides(workspace_path),
                    *codex_clean_startup_overrides(default_codex_home()),
                ),
            )
        )

    def build_phase_runners(factory: AgentSessionFactory) -> dict[TaskKind, PhaseRunner]:
        return {
            "evidence_extraction": EvidenceExtractionRunner(agent_factory=factory),
            "project_synthesis": ProjectSynthesisRunner(agent_factory=factory),
            "daily_synthesis": DailySynthesisRunner(agent_factory=factory),
        }

    return GenerateWorkspaceWorkflow(
        build_agent_factory=build_agent_factory,
        build_phase_runners=build_phase_runners,
    )


def register(app: typer.Typer) -> None:
    """Register generate commands."""
    generate_app = typer.Typer(
        help="Run report generation or a standalone generation phase.",
        invoke_without_command=True,
    )
    generate_app.callback()(generate)
    generate_app.command(name="evidence")(generate_evidence)
    generate_app.command(name="project")(generate_project)
    generate_app.command(name="daily")(generate_daily)
    app.add_typer(generate_app, name="generate")


def generate(
    ctx: typer.Context,
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    quiet: QuietOption = False,
    notion: NotionOption = False,
    reports_root: ReportsRootOption = None,
) -> None:
    """Run the full generation pipeline."""
    # Phase subcommands inherit this group-level --reports-root via the context (see
    # _group_reports_root), so it is honored whether it precedes or follows the subcommand.
    ctx.obj = reports_root
    if ctx.invoked_subcommand is not None:
        return
    try:
        # With --notion, fail fast on missing configuration before the expensive pipeline runs.
        if notion:
            _notion_credentials()
        root = resolve_reports_root(reports_root)
        with build_cli_reporter(quiet=quiet) as reporter:
            workspace_path, messages = workspace_for_generate_target(
                date=date,
                today=today,
                timezone_name=timezone,
                reports_root=root,
                reporter=reporter,
            )
            workflow = build_generation_workflow()
            result = workflow.run_pipeline(
                workspace_path=workspace_path, messages=messages, reporter=reporter
            )
        # Publishing is an explicit, outward-facing step after a successful pipeline, so it runs
        # outside the reporter context and only when --notion is set.
        published = publish_report_to_notion(workspace_path) if notion else ()
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages((*result.messages, *published))


def publish_report_to_notion(
    workspace_path: Path,
    *,
    client_factory: Callable[..., NotionClientProtocol] = build_notion_client,
) -> tuple[str, ...]:
    """Publish the workspace's rendered Notion payload to the configured database.

    Reads the integration token and target database id from the environment so credentials never
    pass through the command line. ``client_factory`` is injected in tests; by default it builds the
    real ``notion_client`` SDK adapter. Any non-:class:`PromptDiaryError` failure (a malformed
    artifact, client construction, an unforeseen SDK/HTTP error) is converted into a structured,
    token-free error so the publish path never crashes the CLI with a traceback.
    """
    token, database_id = _notion_credentials()
    try:
        client = client_factory(token=token)
        result = publish_workspace_report(
            workspace_path=workspace_path, client=client, database_id=database_id
        )
    except PromptDiaryError:
        raise
    except Exception as exc:
        raise PromptDiaryError(_notion_publish_failed_message(exc)) from exc
    return (f"Published report to Notion: {result.url or result.page_id}",)


def _notion_credentials() -> tuple[str, str]:
    """Return the Notion (token, database_id) from the environment, raising if either is missing."""
    token = os.environ.get(_NOTION_TOKEN_ENV)
    database_id = os.environ.get(_NOTION_DATABASE_ENV)
    if not token or not database_id:
        raise PromptDiaryError(_missing_notion_env_message())
    return token, database_id


def generate_evidence(
    ctx: typer.Context,
    *,
    project_key: GenerateProjectKeyOption,
    session_ref: GenerateSessionRefOption,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    quiet: QuietOption = False,
    reports_root: ReportsRootOption = None,
) -> None:
    """Run one evidence extraction task."""
    _run_phase_command(
        phase="evidence",
        date=date,
        today=today,
        timezone_name=timezone,
        project_key=project_key,
        session_ref=session_ref,
        quiet=quiet,
        reports_root=_group_reports_root(ctx, reports_root),
    )


def generate_project(
    ctx: typer.Context,
    *,
    project_key: GenerateProjectKeyOption,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    quiet: QuietOption = False,
    reports_root: ReportsRootOption = None,
) -> None:
    """Run one project synthesis task."""
    _run_phase_command(
        phase="project",
        date=date,
        today=today,
        timezone_name=timezone,
        project_key=project_key,
        quiet=quiet,
        reports_root=_group_reports_root(ctx, reports_root),
    )


def generate_daily(
    ctx: typer.Context,
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    quiet: QuietOption = False,
    reports_root: ReportsRootOption = None,
) -> None:
    """Run daily report synthesis."""
    _run_phase_command(
        phase="daily",
        date=date,
        today=today,
        timezone_name=timezone,
        quiet=quiet,
        reports_root=_group_reports_root(ctx, reports_root),
    )


def workspace_for_generate_target(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    reports_root: Path,
    source_specs: tuple[SourceSpec, ...] | None = None,
    now: datetime | None = None,
    reporter: ProgressReporter = NULL_REPORTER,
) -> tuple[Path, tuple[str, ...]]:
    """Resolve a CLI target and ensure its prepared workspace exists."""
    target = resolve_report_target(date=date, today=today, timezone_name=timezone_name, now=now)
    workspace_path = workspace_path_for_target(target, reports_root=reports_root)
    if workspace_path.exists():
        validate_workspace_matches_target(workspace_path, target)
        return (
            workspace_path,
            (
                f"Reusing existing workspace {workspace_path}; "
                "run prepare --force to refresh it after session updates.",
            ),
        )

    prepare_result = prepare_workspace(
        target,
        reports_root=reports_root,
        source_specs=source_specs,
        force=False,
        prepared_at=now,
        reporter=reporter,
    )
    return prepare_result.workspace_path, prepare_result.messages


def workspace_for_existing_target(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    reports_root: Path,
    now: datetime | None = None,
) -> Path:
    """Resolve a CLI target and return its existing prepared workspace."""
    target = resolve_report_target(date=date, today=today, timezone_name=timezone_name, now=now)
    workspace_path = workspace_path_for_target(target, reports_root=reports_root)
    if not workspace_path.exists():
        raise PromptDiaryError(_missing_workspace_message(workspace_path))
    validate_workspace_matches_target(workspace_path, target)
    return workspace_path


def _group_reports_root(ctx: typer.Context, reports_root: Path | None) -> Path | None:
    """Return the phase command's own --reports-root, else the generate group's value.

    The ``generate`` group callback stores its --reports-root in ``ctx.obj``, so the flag is
    honored whether it precedes or follows the phase subcommand; a value passed on the subcommand
    itself takes precedence over the group's.
    """
    if reports_root is not None:
        return reports_root
    group_value = ctx.obj
    return group_value if isinstance(group_value, Path) else None


def _run_phase_command(
    *,
    phase: PhaseName,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    project_key: str | None = None,
    session_ref: str | None = None,
    quiet: bool = False,
    reports_root: Path | None = None,
) -> None:
    try:
        root = resolve_reports_root(reports_root)
        workspace_path = workspace_for_existing_target(
            date=date,
            today=today,
            timezone_name=timezone_name,
            reports_root=root,
        )
        workflow = build_generation_workflow()
        with build_cli_reporter(quiet=quiet) as reporter:
            result = workflow.run_phase(
                workspace_path=workspace_path,
                phase=phase,
                project_key=project_key,
                session_ref=session_ref,
                reporter=reporter,
            )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)


def _missing_workspace_message(workspace_path: Path) -> str:
    return f"prepared workspace is missing: {workspace_path}; run prepare first"


def _missing_notion_env_message() -> str:
    return (
        f"set {_NOTION_TOKEN_ENV} (integration token) and {_NOTION_DATABASE_ENV} (database id) "
        "to publish to Notion"
    )


def _notion_publish_failed_message(cause: object) -> str:
    return f"failed to publish the report to Notion: {cause}"
