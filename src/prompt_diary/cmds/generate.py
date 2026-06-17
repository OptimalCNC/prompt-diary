"""Generate command registration."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from prompt_diary.cmds.common import (
    CliWorkspaceTargetOptions,
    DynamicDefaultsTyperCommand,
    DynamicDefaultsTyperGroup,
    QuietOption,
    ResolvedCliDirectWorkspaceTarget,
    build_cli_reporter,
    echo_messages,
    exit_with_error,
    workspace_target_callback,
    workspace_target_command,
)
from prompt_diary.config import (
    NOTION_DATABASE_ENV,
    NOTION_TOKEN_ENV,
    notion_is_configured,
    resolve_content_language,
    resolve_notion_credentials,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.agent_language import LanguageNormAgentSessionFactory
from prompt_diary.generate.daily_synthesis import DailySynthesisRunner
from prompt_diary.generate.evidence_extraction import EvidenceExtractionRunner
from prompt_diary.generate.project_synthesis import ProjectSynthesisRunner
from prompt_diary.generate.rendering import (
    NotionRenderResult,
    RenderingRunner,
    render_workspace_report_to_notion,
)
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow, PhaseName
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig
from prompt_diary.mcp.codex_config import (
    codex_clean_startup_overrides,
    default_codex_home,
    prompt_diary_mcp_overrides,
)
from prompt_diary.prepare.workspace import (
    prepare_workspace,
    validate_workspace_matches_target,
)
from prompt_diary.progress.reporter import NULL_REPORTER

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
            "Pass --notion to require publishing (errors if unconfigured) or --no-notion to skip. "
            f"Configure with `prompt-diary config init`, or ${NOTION_DATABASE_ENV} / "
            f"${NOTION_TOKEN_ENV}."
        ),
    ),
]

RenderNotionOption = Annotated[
    bool | None,
    typer.Option(
        "--notion/--no-notion",
        help=(
            "After rendering, publish the report as a new row in the configured Notion database. "
            "Pass --notion to require publishing (errors if unconfigured) or --no-notion to skip. "
            f"Configure with `prompt-diary config init`, or ${NOTION_TOKEN_ENV} / "
            f"${NOTION_DATABASE_ENV}."
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
        inner = CodexAgentSessionFactory(
            backend_config=CodexBackendConfig(
                codex_bin=Path(codex_path) if codex_path is not None else None,
                mcp_config_overrides=(
                    *prompt_diary_mcp_overrides(workspace_path),
                    *codex_clean_startup_overrides(default_codex_home()),
                ),
            ),
        )
        return LanguageNormAgentSessionFactory(
            inner=inner,
            workspace_path=workspace_path,
            language=resolve_content_language(),
        )

    def build_phase_runners(factory: AgentSessionFactory) -> dict[TaskKind, PhaseRunner]:
        return {
            "evidence_extraction": EvidenceExtractionRunner(agent_factory=factory),
            "project_synthesis": ProjectSynthesisRunner(agent_factory=factory),
            "daily_synthesis": DailySynthesisRunner(agent_factory=factory),
            "rendering": RenderingRunner(),
        }

    return GenerateWorkspaceWorkflow(
        build_agent_factory=build_agent_factory,
        build_phase_runners=build_phase_runners,
    )


def register(app: typer.Typer) -> None:
    """Register generate commands."""
    generate_app = typer.Typer(
        cls=DynamicDefaultsTyperGroup,
        help="Run report generation or a standalone generation phase.",
        invoke_without_command=True,
        subcommand_metavar="[COMMAND] [ARGS]...",
    )
    workspace_target_callback(generate_app, generate, include_workspace=True)
    workspace_target_command(
        generate_app,
        generate_evidence,
        name="evidence",
        cls=DynamicDefaultsTyperCommand,
        include_workspace=True,
    )
    workspace_target_command(
        generate_app,
        generate_project,
        name="project",
        cls=DynamicDefaultsTyperCommand,
        include_workspace=True,
    )
    workspace_target_command(
        generate_app,
        generate_daily,
        name="daily",
        cls=DynamicDefaultsTyperCommand,
        include_workspace=True,
    )
    workspace_target_command(
        generate_app,
        generate_render,
        name="render",
        cls=DynamicDefaultsTyperCommand,
        include_workspace=True,
    )
    app.add_typer(generate_app, name="generate")


def generate(
    ctx: typer.Context,
    *,
    target_options: CliWorkspaceTargetOptions,
    quiet: QuietOption = False,
    notion: NotionOption = None,
) -> None:
    """Run the full generation pipeline."""
    # Phase subcommands inherit selected group-level target flags via the context (see
    # _phase_target_options), so --reports-root and --workspace are honored whether they precede or
    # follow the subcommand.
    ctx.obj = target_options
    if ctx.invoked_subcommand is not None:
        return
    try:
        # Freeze the Notion publish target before the expensive pipeline: the default (unset)
        # publishes only when configured, --notion requires configuration (fail-fast), and
        # --no-notion is an explicit skip. Resolving the credentials here means the target cannot
        # drift mid-run.
        notion_target = resolve_notion_publish(notion=notion)
        with build_cli_reporter(quiet=quiet) as reporter:
            workspace_path, messages = workspace_for_generate_target(
                target_options=target_options,
                reporter=reporter,
            )
            workflow = build_generation_workflow()
            result = workflow.run_pipeline(
                workspace_path=workspace_path, messages=messages, reporter=reporter
            )
            # Publishing is an outward-facing step after a successful pipeline, with the target
            # frozen before the run. It stays inside the reporter context so the rendering phase
            # includes optional Notion publishing time.
            published = (
                render_report_to_notion_messages(
                    workspace_path,
                    credentials=notion_target,
                    progress_reporter=reporter,
                )
                if notion_target is not None
                else ()
            )
            timing = reporter.timing_summary_message()
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages((*result.messages, *published, *((timing,) if timing is not None else ())))


def resolve_notion_publish(*, notion: bool | None) -> tuple[Secret, str] | None:
    """Resolve the frozen Notion ``(token, database_id)`` to publish with, or ``None`` to skip.

    Resolving the credentials here, before the expensive pipeline, both fails fast and freezes the
    publish target so it cannot drift if the stored config changes mid-run. ``True`` (``--notion``)
    requires configuration and raises otherwise; ``False`` (``--no-notion``) skips publishing.
    ``None`` (flag unset) publishes when configuration is complete and otherwise skips.
    """
    if notion is False:
        return None
    configured = notion_is_configured()
    if not configured:
        if notion is None:
            return None
        raise PromptDiaryError(_notion_unconfigured_message())
    return resolve_notion_credentials()


def render_report_to_notion_messages(
    workspace_path: Path,
    *,
    credentials: tuple[Secret, str],
    progress_reporter: ProgressReporter = NULL_REPORTER,
) -> tuple[str, ...]:
    """Render and publish the workspace report to Notion using frozen credentials."""
    result = render_workspace_report_to_notion(
        workspace_path,
        credentials=credentials,
        progress_reporter=progress_reporter,
    )
    for warning in result.warnings:
        typer.echo(f"Warning: {warning}", err=True)
    return (_notion_render_message(result),)


def generate_evidence(
    ctx: typer.Context,
    *,
    project_key: GenerateProjectKeyOption,
    session_ref: GenerateSessionRefOption,
    target_options: CliWorkspaceTargetOptions,
    quiet: QuietOption = False,
) -> None:
    """Run one evidence extraction task."""
    _run_phase_command(
        phase="evidence",
        target_options=_phase_target_options(ctx, target_options),
        project_key=project_key,
        session_ref=session_ref,
        quiet=quiet,
    )


def generate_project(
    ctx: typer.Context,
    *,
    project_key: GenerateProjectKeyOption,
    target_options: CliWorkspaceTargetOptions,
    quiet: QuietOption = False,
) -> None:
    """Run one project synthesis task."""
    _run_phase_command(
        phase="project",
        target_options=_phase_target_options(ctx, target_options),
        project_key=project_key,
        quiet=quiet,
    )


def generate_daily(
    ctx: typer.Context,
    *,
    target_options: CliWorkspaceTargetOptions,
    quiet: QuietOption = False,
) -> None:
    """Run daily report synthesis."""
    _run_phase_command(
        phase="daily",
        target_options=_phase_target_options(ctx, target_options),
        quiet=quiet,
    )


def generate_render(
    ctx: typer.Context,
    *,
    target_options: CliWorkspaceTargetOptions,
    quiet: QuietOption = False,
    notion: RenderNotionOption = None,
) -> None:
    """Render the report views from daily-report.json, optionally publishing to Notion."""
    try:
        # Resolve the publish target before rendering so --notion fails fast on an unconfigured
        # machine and the default publishes only when configuration is complete.
        notion_target = resolve_notion_publish(notion=notion)
        workspace_path = workspace_for_existing_target(
            target_options=_phase_target_options(ctx, target_options),
        )
        workflow = build_generation_workflow()
        with build_cli_reporter(quiet=quiet) as reporter:
            result = workflow.run_phase(
                workspace_path=workspace_path,
                phase="render",
                reporter=reporter,
            )
            # Publishing is an outward-facing step after a successful render; it stays inside the
            # reporter context so its timing is included.
            published = (
                render_report_to_notion_messages(
                    workspace_path,
                    credentials=notion_target,
                    progress_reporter=reporter,
                )
                if notion_target is not None
                else ()
            )
            timing = reporter.timing_summary_message()
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages((*result.messages, *published, *((timing,) if timing is not None else ())))


def workspace_for_generate_target(
    *,
    target_options: CliWorkspaceTargetOptions,
    source_specs: tuple[SourceSpec, ...] | None = None,
    now: datetime | None = None,
    reporter: ProgressReporter = NULL_REPORTER,
) -> tuple[Path, tuple[str, ...]]:
    """Resolve a CLI target and ensure its prepared workspace exists."""
    resolved = target_options.resolve_generation_target(now=now)
    if isinstance(resolved, ResolvedCliDirectWorkspaceTarget):
        _validate_direct_workspace(resolved.workspace_path)
        return resolved.workspace_path, (f"Using prepared workspace {resolved.workspace_path}.",)

    if resolved.workspace_path.exists():
        validate_workspace_matches_target(resolved.workspace_path, resolved.target)
        return (
            resolved.workspace_path,
            (
                f"Reusing existing workspace {resolved.workspace_path}; "
                "run prepare --force to refresh it after session updates.",
            ),
        )

    prepare_result = prepare_workspace(
        resolved.target,
        reports_root=resolved.reports_root,
        source_specs=source_specs,
        force=False,
        prepared_at=now,
        reporter=reporter,
    )
    return prepare_result.workspace_path, prepare_result.messages


def workspace_for_existing_target(
    *,
    target_options: CliWorkspaceTargetOptions,
    now: datetime | None = None,
) -> Path:
    """Resolve a CLI target and return its existing prepared workspace."""
    resolved = target_options.resolve_generation_target(now=now)
    if isinstance(resolved, ResolvedCliDirectWorkspaceTarget):
        _validate_direct_workspace(resolved.workspace_path)
        return resolved.workspace_path

    if not resolved.workspace_path.exists():
        raise PromptDiaryError(_missing_workspace_message(resolved.workspace_path))
    validate_workspace_matches_target(resolved.workspace_path, resolved.target)
    return resolved.workspace_path


def _validate_direct_workspace(workspace_path: Path) -> None:
    """Verify a direct generation workspace exists and has prepared-workspace structure."""
    if not workspace_path.exists():
        raise PromptDiaryError(_missing_workspace_message(workspace_path))
    load_prepared_workspace(workspace_path)


def _group_target_options(ctx: typer.Context) -> CliWorkspaceTargetOptions | None:
    group_value = ctx.obj
    return group_value if isinstance(group_value, CliWorkspaceTargetOptions) else None


def _phase_target_options(
    ctx: typer.Context, target_options: CliWorkspaceTargetOptions
) -> CliWorkspaceTargetOptions:
    group_options = _group_target_options(ctx)
    if group_options is None:
        return target_options

    reports_root = (
        target_options.reports_root
        if target_options.reports_root is not None
        else group_options.reports_root
    )
    workspace = (
        target_options.workspace
        if target_options.workspace is not None
        else group_options.workspace
    )
    return target_options.with_reports_root(reports_root).with_workspace(workspace)


def _run_phase_command(
    *,
    phase: PhaseName,
    target_options: CliWorkspaceTargetOptions,
    project_key: str | None = None,
    session_ref: str | None = None,
    quiet: bool = False,
) -> None:
    try:
        workspace_path = workspace_for_existing_target(
            target_options=target_options,
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
            timing = reporter.timing_summary_message()
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages((*result.messages, *((timing,) if timing is not None else ())))


def _missing_workspace_message(workspace_path: Path) -> str:
    return f"prepared workspace is missing: {workspace_path}; run prepare first"


def _notion_render_message(result: NotionRenderResult) -> str:
    return f"Published report to Notion: {result.url or result.page_id}"


def _notion_unconfigured_message() -> str:
    return (
        "--notion was given but no Notion credentials are configured; run "
        f"`prompt-diary config init` (or set ${NOTION_TOKEN_ENV} and ${NOTION_DATABASE_ENV})."
    )
