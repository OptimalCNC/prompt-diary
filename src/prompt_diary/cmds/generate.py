"""Generate command registration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from prompt_diary.cmds.common import (
    DateOption,
    TimezoneOption,
    TodayOption,
    echo_messages,
    exit_with_error,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis import DailySynthesisRunner
from prompt_diary.generate.evidence_extraction import EvidenceExtractionRunner
from prompt_diary.generate.project_synthesis import ProjectSynthesisRunner
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow, PhaseName
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig
from prompt_diary.mcp.codex_config import prompt_diary_mcp_overrides
from prompt_diary.prepare.workspace import (
    prepare_workspace,
    validate_workspace_matches_target,
    workspace_path_for_target,
)
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from datetime import datetime

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import PhaseRunner, TaskKind
    from prompt_diary.models import SourceSpec

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
        return CodexAgentSessionFactory(
            CodexBackendConfig(mcp_config_overrides=prompt_diary_mcp_overrides(workspace_path))
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
) -> None:
    """Run the full generation pipeline."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        workspace_path, messages = workspace_for_generate_target(
            date=date,
            today=today,
            timezone_name=timezone,
        )
        workflow = build_generation_workflow()
        result = workflow.run_pipeline(workspace_path=workspace_path, messages=messages)
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)


def generate_evidence(
    *,
    project_key: GenerateProjectKeyOption,
    session_ref: GenerateSessionRefOption,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
) -> None:
    """Run one evidence extraction task."""
    _run_phase_command(
        phase="evidence",
        date=date,
        today=today,
        timezone_name=timezone,
        project_key=project_key,
        session_ref=session_ref,
    )


def generate_project(
    *,
    project_key: GenerateProjectKeyOption,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
) -> None:
    """Run one project synthesis task."""
    _run_phase_command(
        phase="project",
        date=date,
        today=today,
        timezone_name=timezone,
        project_key=project_key,
    )


def generate_daily(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
) -> None:
    """Run daily report synthesis."""
    _run_phase_command(
        phase="daily",
        date=date,
        today=today,
        timezone_name=timezone,
    )


def workspace_for_generate_target(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    reports_root: Path = Path(".reports"),
    source_specs: tuple[SourceSpec, ...] | None = None,
    now: datetime | None = None,
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
    )
    return prepare_result.workspace_path, prepare_result.messages


def workspace_for_existing_target(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    reports_root: Path = Path(".reports"),
    now: datetime | None = None,
) -> Path:
    """Resolve a CLI target and return its existing prepared workspace."""
    target = resolve_report_target(date=date, today=today, timezone_name=timezone_name, now=now)
    workspace_path = workspace_path_for_target(target, reports_root=reports_root)
    if not workspace_path.exists():
        raise PromptDiaryError(_missing_workspace_message(workspace_path))
    validate_workspace_matches_target(workspace_path, target)
    return workspace_path


def _run_phase_command(
    *,
    phase: PhaseName,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    project_key: str | None = None,
    session_ref: str | None = None,
) -> None:
    try:
        workspace_path = workspace_for_existing_target(
            date=date,
            today=today,
            timezone_name=timezone_name,
        )
        workflow = build_generation_workflow()
        result = workflow.run_phase(
            workspace_path=workspace_path,
            phase=phase,
            project_key=project_key,
            session_ref=session_ref,
        )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)


def _missing_workspace_message(workspace_path: Path) -> str:
    return f"prepared workspace is missing: {workspace_path}; run prepare first"
