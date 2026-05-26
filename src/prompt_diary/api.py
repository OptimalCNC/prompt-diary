"""Public library workflow functions for Prompt Diary."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from prompt_diary.errors import ReportValidationError
from prompt_diary.models import (
    GenerateResult,
    PrepareResult,
    SourceSpec,
)
from prompt_diary.report import (
    CommandReportWriter,
    ReportWriter,
    build_report_prompt,
    validate_report,
)
from prompt_diary.targets import resolve_report_target
from prompt_diary.workspace import (
    prepare_workspace,
    validate_workspace_matches_target,
    workspace_path_for_target,
)

if TYPE_CHECKING:
    from datetime import datetime


def prepare_prompt_diary(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    force: bool,
    reports_root: Path = Path(".reports"),
    source_specs: tuple[SourceSpec, ...] | None = None,
    now: datetime | None = None,
) -> PrepareResult:
    """Resolve a report target and prepare its workspace."""
    target = resolve_report_target(date=date, today=today, timezone_name=timezone_name, now=now)
    return prepare_workspace(
        target,
        reports_root=reports_root,
        source_specs=source_specs,
        force=force,
        prepared_at=now,
    )


def generate_prompt_diary(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    reports_root: Path = Path(".reports"),
    source_specs: tuple[SourceSpec, ...] | None = None,
    now: datetime | None = None,
    report_writer: ReportWriter | None = None,
) -> GenerateResult:
    """Resolve a target, ensure a workspace exists, write, and validate report.md."""
    target = resolve_report_target(date=date, today=today, timezone_name=timezone_name, now=now)
    workspace_path = workspace_path_for_target(target, reports_root=reports_root)
    messages: list[str] = []

    if workspace_path.exists():
        validate_workspace_matches_target(workspace_path, target)
        messages.append(
            f"Reusing existing workspace {workspace_path}; "
            "run prepare --force to refresh it after session updates."
        )
    else:
        prepare_result = prepare_workspace(
            target,
            reports_root=reports_root,
            source_specs=source_specs,
            force=False,
            prepared_at=now,
        )
        messages.extend(prepare_result.messages)

    prompt = build_report_prompt(workspace_path)
    writer = CommandReportWriter.from_environment() if report_writer is None else report_writer
    returned_report_path = writer.write_report(
        workspace_path=workspace_path,
        prompt=prompt,
    )
    expected_report_path = workspace_path / "report.md"
    if returned_report_path.resolve() != expected_report_path.resolve():
        raise ReportValidationError(
            _report_writer_returned_wrong_path_message(returned_report_path, expected_report_path)
        )
    report_path = expected_report_path
    validation = validate_report(workspace_path)
    if not validation.ok:
        raise ReportValidationError(_validation_failed_message(validation.errors))

    messages.append(f"Wrote validated report {report_path}.")
    return GenerateResult(
        target=target,
        workspace_path=workspace_path,
        report_path=report_path,
        validation=validation,
        messages=tuple(messages),
    )


def _validation_failed_message(errors: tuple[str, ...]) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"Report validation failed:\n{details}"


def _report_writer_returned_wrong_path_message(report_path: Path, expected_path: Path) -> str:
    return f"Report writer returned {report_path}, but it must create {expected_path}"
