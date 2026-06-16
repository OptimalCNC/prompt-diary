"""Local report listing and inspection commands."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, cast

import typer

from prompt_diary.cmds.common import (
    CliWorkspaceTargetOptions,
    ReportsRootOption,
    echo_messages,
    exit_with_error,
    workspace_target_command,
)
from prompt_diary.config import resolve_reports_root
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.prepare.workspace import (
    audit_path_for_target,
    validate_workspace_matches_target,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import PreparedProject

_DATE_WORKSPACE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEFAULT_LIST_LIMIT = 10
_DEFAULT_VERBOSE_LIST_LIMIT = 5

LimitOption = Annotated[
    int | None,
    typer.Option(
        "--limit",
        min=1,
        help="Maximum number of reports to show. Defaults to 10, or 5 with --verbose.",
    ),
]
VerboseOption = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Include inspection details for each listed report."),
]


@dataclass(frozen=True)
class ProjectInspection:
    """Progress summary for one project in a prepared report workspace."""

    key: str
    label: str
    directory: str | None
    sessions: int
    turns: int
    evidence_chains: int
    work_items: int | None


@dataclass(frozen=True)
class ReportInspection:
    """Read-only progress summary for a local report workspace."""

    workspace_path: Path
    report_date: str
    status: str
    timezone: str
    projects: tuple[ProjectInspection, ...]
    daily_model: bool
    markdown_view: bool
    notion_payload: bool

    @property
    def sessions(self) -> int:
        return sum(project.sessions for project in self.projects)

    @property
    def turns(self) -> int:
        return sum(project.turns for project in self.projects)

    @property
    def evidence_chains(self) -> int:
        return sum(project.evidence_chains for project in self.projects)

    @property
    def project_syntheses(self) -> int:
        return sum(project.work_items is not None for project in self.projects)


def register(app: typer.Typer) -> None:
    """Register local report navigation commands."""
    app.command(name="list")(list_reports)
    workspace_target_command(app, inspect_report, name="inspect")


def list_reports(
    *,
    verbose: VerboseOption = False,
    limit: LimitOption = None,
    reports_root: ReportsRootOption = None,
) -> None:
    """List local report workspaces."""
    try:
        root = resolve_reports_root(reports_root)
        work_root = root / "work"
        report_paths = _local_report_paths(work_root)
        selected_limit = limit if limit is not None else _default_list_limit(verbose=verbose)
        selected_paths = report_paths[:selected_limit]
        if not report_paths:
            typer.echo(f"No local reports found in {work_root}.")
            return
        messages = [_list_header(work_root, shown=len(selected_paths), total=len(report_paths))]
        for workspace_path in selected_paths:
            messages.extend(_format_list_entry(workspace_path, verbose=verbose, root=root))
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(messages)


def inspect_report(
    *,
    target_options: CliWorkspaceTargetOptions,
) -> None:
    """Inspect one local report workspace."""
    try:
        workspace_path, audit_path = _resolve_existing_workspace(target_options)
        inspection = inspect_workspace(
            workspace_path,
            audit_path=audit_path,
        )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(_format_inspection(inspection))


def inspect_workspace(workspace_path: Path, *, audit_path: Path | None = None) -> ReportInspection:
    """Inspect an existing workspace path without resolving a CLI target."""
    workspace = load_prepared_workspace(workspace_path)
    directories = _project_directories(audit_path)
    projects = tuple(
        _inspect_project(workspace_path, project, directories) for project in workspace.projects
    )
    return ReportInspection(
        workspace_path=workspace_path,
        report_date=workspace.report_date,
        status=workspace.status,
        timezone=workspace.timezone,
        projects=projects,
        daily_model=(workspace_path / "daily-report.json").exists(),
        markdown_view=(workspace_path / "report.md").exists(),
        notion_payload=(workspace_path / "report.notion.json").exists(),
    )


def _local_report_paths(work_root: Path) -> tuple[Path, ...]:
    if not work_root.exists():
        return ()
    return tuple(
        sorted(
            (
                path
                for path in work_root.iterdir()
                if path.is_dir() and _DATE_WORKSPACE_RE.fullmatch(path.name) is not None
            ),
            key=lambda path: path.name,
            reverse=True,
        )
    )


def _default_list_limit(*, verbose: bool) -> int:
    return _DEFAULT_VERBOSE_LIST_LIMIT if verbose else _DEFAULT_LIST_LIMIT


def _resolve_existing_workspace(
    target_options: CliWorkspaceTargetOptions,
) -> tuple[Path, Path]:
    resolved = target_options.resolve()
    if not resolved.workspace_path.exists():
        raise PromptDiaryError(_missing_workspace_message(resolved.workspace_path))
    validate_workspace_matches_target(resolved.workspace_path, resolved.target)
    return resolved.workspace_path, audit_path_for_target(
        resolved.target,
        reports_root=resolved.reports_root,
    )


def _format_list_entry(workspace_path: Path, *, verbose: bool, root: Path) -> tuple[str, ...]:
    if not verbose:
        return (f"{workspace_path.name}:", f"  Work path: {workspace_path}")
    inspection = inspect_workspace(
        workspace_path,
        audit_path=root / "private" / workspace_path.name / "audit.manifest.json",
    )
    return _format_inspection(inspection)


def _list_header(work_root: Path, *, shown: int, total: int) -> str:
    return f"Local reports in {work_root} (showing {shown} of {total})"


def _inspect_project(
    workspace_path: Path,
    project: PreparedProject,
    directories: dict[str, str],
) -> ProjectInspection:
    project_dir = workspace_path / "projects" / project.project_key
    return ProjectInspection(
        key=project.project_key,
        label=project.project_label,
        directory=directories.get(project.project_key),
        sessions=len(project.sessions),
        turns=sum(len(session.turns) for session in project.sessions),
        evidence_chains=sum(
            _evidence_chain_count(project_dir / "evidence" / f"{session.session_ref}.json")
            for session in project.sessions
        ),
        work_items=_project_work_item_count(project_dir / "project-synthesis.json"),
    )


def _project_directories(audit_path: Path | None) -> dict[str, str]:
    raw = _json_object(audit_path) if audit_path is not None and audit_path.exists() else {}
    sessions = raw.get("sessions")
    session_items = cast("list[object]", sessions) if isinstance(sessions, list) else []
    directories: dict[str, str] = {}
    for item in session_items:
        session = cast("dict[str, object]", item) if isinstance(item, dict) else {}
        key = session.get("workspace_project_key")
        root = session.get("canonical_project_root")
        is_unknown = session.get("project_root_is_unknown")
        if isinstance(key, str) and isinstance(root, str) and key not in directories:
            directories[key] = f"unknown ({root})" if is_unknown is True else root
    return directories


def _evidence_chain_count(path: Path) -> int:
    raw = _json_object(path) if path.exists() else {}
    return _list_field_count(raw, "evidence_chains")


def _project_work_item_count(path: Path) -> int | None:
    raw = _json_object(path) if path.exists() else None
    return None if raw is None else _list_field_count(raw, "work_items")


def _list_field_count(raw: dict[str, object], field: str) -> int:
    value = raw.get(field)
    if not isinstance(value, list):
        return 0
    return len(cast("list[object]", value))


def _json_object(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptDiaryError(_invalid_json_message(path, exc.msg)) from exc
    return cast("dict[str, object]", raw if isinstance(raw, dict) else {})


def _format_inspection(inspection: ReportInspection) -> tuple[str, ...]:
    lines = [
        f"Report {inspection.report_date} ({inspection.status})",
        f"Work path: {inspection.workspace_path}",
        f"Status: {inspection.status}",
        f"Timezone: {inspection.timezone}",
        "Progress:",
        f"  Projects: {len(inspection.projects)}",
        f"  Sessions: {inspection.sessions}",
        f"  Turns: {inspection.turns}",
        f"  Evidence chains: {inspection.evidence_chains}/{inspection.turns}",
        f"  Project synthesis: {inspection.project_syntheses}/{len(inspection.projects)}",
        f"  Daily model: {_present(value=inspection.daily_model)}",
        f"  Markdown view: {_present(value=inspection.markdown_view)}",
        f"  Notion payload: {_present(value=inspection.notion_payload)}",
        "Projects:" if inspection.projects else "Projects: none",
    ]
    for project in inspection.projects:
        lines.extend(_format_project(project))
    return tuple(lines)


def _format_project(project: ProjectInspection) -> tuple[str, ...]:
    work_item_summary = (
        "missing" if project.work_items is None else f"present ({project.work_items} work items)"
    )
    return (
        f"  {project.label} ({project.key})",
        f"    Directory: {project.directory or 'unavailable'}",
        f"    Sessions: {project.sessions}",
        f"    Turns: {project.turns}",
        f"    Evidence chains: {project.evidence_chains}/{project.turns}",
        f"    Project synthesis: {work_item_summary}",
    )


def _present(*, value: bool) -> str:
    return "present" if value else "missing"


def _missing_workspace_message(workspace_path: Path) -> str:
    return f"prepared workspace is missing: {workspace_path}; run prepare first"


def _invalid_json_message(path: Path, message: str) -> str:
    return f"{path} contains invalid JSON: {message}"
