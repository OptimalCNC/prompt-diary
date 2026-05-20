"""Public library workflow functions for Prompt Diary."""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from pathlib import Path

from prompt_diary.errors import ReportValidationError
from prompt_diary.models import (
    GenerateResult,
    PrepareResult,
    ReportTarget,
    SourceSpec,
    serialize_datetime,
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

    generated_at = serialize_datetime(_timestamp_for_target(target, now))
    prompt = build_report_prompt(workspace_path, generated_at=generated_at)
    writer = CommandReportWriter.from_environment() if report_writer is None else report_writer
    returned_report_path = writer.write_report(
        workspace_path=workspace_path,
        prompt=prompt,
        generated_at=generated_at,
    )
    expected_report_path = workspace_path / "report.md"
    if returned_report_path.resolve() != expected_report_path.resolve():
        raise ReportValidationError(
            _report_writer_returned_wrong_path_message(returned_report_path, expected_report_path)
        )
    report_path = expected_report_path
    validation = validate_report(workspace_path, generated_at=generated_at)
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


def _timestamp_for_target(target: ReportTarget, timestamp: datetime | None) -> datetime:
    target_tzinfo = _target_tzinfo(target)
    if timestamp is None:
        return datetime.now(target_tzinfo)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=target_tzinfo)
    return timestamp.astimezone(target_tzinfo)


def _target_tzinfo(target: ReportTarget) -> tzinfo:
    return target.report_window_local.start.tzinfo or timezone.utc


def _validation_failed_message(errors: tuple[str, ...]) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"Report validation failed:\n{details}"


def _report_writer_returned_wrong_path_message(report_path: Path, expected_path: Path) -> str:
    return f"Report writer returned {report_path}, but it must create {expected_path}"
