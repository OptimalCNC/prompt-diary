"""Shared typed models for Prompt Diary workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from pathlib import Path

ReportStatus: TypeAlias = Literal["final", "partial"]
SourceName: TypeAlias = Literal["codex", "claude-code"]
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True)
class TimeWindow:
    """Absolute half-open time window."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class ReportTarget:
    """Resolved local-day report target."""

    report_date: date
    timezone: str
    status: ReportStatus
    report_window_local: TimeWindow
    report_window_utc: TimeWindow

    @property
    def workspace_name(self) -> str:
        """Return the date folder used for this target."""
        return self.report_date.isoformat()


@dataclass(frozen=True)
class SourceSpec:
    """Configured source transcript location."""

    source: SourceName
    root: Path
    fallback_project_root: Path | None = None


@dataclass(frozen=True)
class PrepareResult:
    """Result from workspace preparation."""

    target: ReportTarget
    workspace_path: Path
    audit_path: Path
    created: bool
    project_count: int
    session_count: int
    messages: tuple[str, ...]


@dataclass(frozen=True)
class ValidationResult:
    """Structured report validation result."""

    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Return whether validation succeeded."""
        return len(self.errors) == 0


@dataclass(frozen=True)
class GenerateResult:
    """Result from report generation."""

    target: ReportTarget
    workspace_path: Path
    report_path: Path
    validation: ValidationResult
    messages: tuple[str, ...]


def serialize_datetime(value: datetime) -> str:
    """Serialize an aware datetime with stable seconds precision."""
    text = value.replace(microsecond=0).isoformat()
    if value.utcoffset() == timezone.utc.utcoffset(value):
        return text.replace("+00:00", "Z")
    return text
