"""Generate command registration."""

from __future__ import annotations

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
from prompt_diary.config import (
    NOTION_DATABASE_ENV,
    NOTION_TOKEN_ENV,
    notion_is_configured,
    resolve_notion_credentials,
    resolve_reports_root,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis import DailySynthesisRunner
from prompt_diary.generate.evidence_extraction import EvidenceExtractionRunner
from prompt_diary.generate.project_synthesis import ProjectSynthesisRunner
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow, PhaseName
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig
from prompt_diary.mcp.codex_config import (
    codex_clean_startup_overrides,
    default_codex_home,
    prompt_diary_mcp_overrides,
)
from prompt_diary.prepare.workspace import (
    prepare_workspace,
    validate_workspace_matches_target,
    workspace_path_for_target,
)
from prompt_diary.progress.reporter import NULL_REPORTER
from prompt_diary.render.notion import NotionRenderResult, render_workspace_report_to_notion
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from datetime import datetime

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import PhaseRunner, TaskKind
    from prompt_diary.models import SourceSpec
    from prompt_diary.progress.reporter import ProgressReporter
    from prompt_diary.secret import Secret

NotionOption = Annotated[
    bool | None,
    typer.Option(
        "--notion/--no-notion",
        help=(
            "Publish the finished report as a new row in the configured Notion database. "
            "Defaults to publishing when Notion is configured and skipping otherwise; pass "
            "--notion to require it (errors if unconfigured) or --no-notion to skip. "
            f"Configure with `prompt-diary config init`, or ${NOTION_DATABASE_ENV} / "
            f"${NOTION_TOKEN_ENV}."
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
    notion: NotionOption = None,
    reports_root: ReportsRootOption = None,
) -> None:
    """Run the full generation pipeline."""
    # Phase subcommands inherit this group-level --reports-root via the context (see
    # _group_reports_root), so it is honored whether it precedes or follows the subcommand.
    ctx.obj = reports_root
    if ctx.invoked_subcommand is not None:
        return
    try:
        # Freeze the Notion publish target before the expensive pipeline: the default (unset)
        # publishes only when configured, --notion requires configuration (fail-fast), --no-notion
        # never publishes. Resolving the credentials here means the target cannot drift mid-run.
        notion_target = resolve_notion_publish(notion=notion)
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
        # Publishing is an outward-facing step after a successful pipeline, so it runs outside the
        # reporter context, with the target frozen before the run.
        published = (
            render_report_to_notion_messages(workspace_path, credentials=notion_target)
            if notion_target is not None
            else ()
        )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages((*result.messages, *published))


def resolve_notion_publish(*, notion: bool | None) -> tuple[Secret, str] | None:
    """Resolve the frozen Notion ``(token, database_id)`` to publish with, or ``None`` to skip.

    Resolving the credentials here, before the expensive pipeline, both fails fast and freezes the
    publish target so it cannot drift if the stored config changes mid-run. ``None`` (flag unset)
    publishes only when Notion is configured; ``True`` (``--notion``) requires configuration and
    raises otherwise; ``False`` (``--no-notion``) never publishes.
    """
    if notion is False:
        return None
    configured = notion_is_configured()
    if notion is None and not configured:
        return None
    if notion is True and not configured:
        raise PromptDiaryError(_notion_unconfigured_message())
    return resolve_notion_credentials()


def render_report_to_notion_messages(
    workspace_path: Path,
    *,
    credentials: tuple[Secret, str],
) -> tuple[str, ...]:
    """Render and publish the workspace report to Notion using frozen credentials."""
    result = render_workspace_report_to_notion(workspace_path, credentials=credentials)
    return (_notion_render_message(result),)


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


def _notion_render_message(result: NotionRenderResult) -> str:
    return f"Published report to Notion: {result.url or result.page_id}"


def _notion_unconfigured_message() -> str:
    return (
        "--notion was given but no Notion credentials are configured; run "
        f"`prompt-diary config init` (or set ${NOTION_TOKEN_ENV} and ${NOTION_DATABASE_ENV})."
    )
