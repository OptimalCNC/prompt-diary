"""Date and timezone target resolution."""

from __future__ import annotations

import os
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from prompt_diary.errors import PromptDiaryError
from prompt_diary.models import ReportStatus, ReportTarget, TimeWindow

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_TIMEZONE = "UTC"
TIMEZONE_ENV_VARS = ("PROMPT_DIARY_TIMEZONE", "TZ")


def normalize_timezone_env_value(value: str) -> str | None:
    """Return a timezone env value usable as an IANA name, or ``None`` when it selects none."""
    stripped = value.strip()
    if not stripped or stripped.startswith(":"):
        return None
    return stripped


def resolve_report_target(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> ReportTarget:
    """Resolve CLI date options into an authoritative local-day target."""
    if date is not None and today:
        raise PromptDiaryError(_mutually_exclusive_date_message())

    resolved_timezone_name = timezone_name or _default_timezone_name(env)
    zone = _load_zone_info(resolved_timezone_name)
    local_now = _localize_now(now, zone)
    local_today = local_now.date()
    report_date = _target_date(date_text=date, today=today, local_today=local_today)

    if report_date > local_today:
        raise PromptDiaryError(_future_date_message(report_date))

    status: ReportStatus = "partial" if report_date == local_today else "final"
    local_start = datetime.combine(report_date, time.min, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)

    return ReportTarget(
        report_date=report_date,
        timezone=resolved_timezone_name,
        status=status,
        report_window_local=TimeWindow(start=local_start, end=local_end),
        report_window_utc=TimeWindow(start=utc_start, end=utc_end),
    )


def _default_timezone_name(env: Mapping[str, str] | None) -> str:
    values = os.environ if env is None else env
    for key in TIMEZONE_ENV_VARS:
        raw_value = values.get(key)
        if raw_value is not None and (value := normalize_timezone_env_value(raw_value)) is not None:
            return value
    system_timezone = _system_timezone_name()
    if system_timezone is not None:
        return system_timezone
    return DEFAULT_TIMEZONE


def _load_zone_info(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise PromptDiaryError(_unknown_timezone_message(timezone_name)) from exc


def detect_system_timezone_name() -> str | None:
    """Return the host's IANA timezone name when it can be detected."""
    return _system_timezone_name()


def is_known_timezone_name(value: str) -> bool:
    """Return whether ``value`` is a known IANA timezone name."""
    return _is_known_timezone(value)


def _system_timezone_name() -> str | None:
    timezone_file = Path("/etc/timezone")
    if timezone_file.exists():
        lines = timezone_file.read_text(encoding="utf-8").strip().splitlines()
        if lines and _is_known_timezone(lines[0]):
            return lines[0]

    localtime = Path("/etc/localtime")
    if not localtime.exists():
        return None
    resolved = localtime.resolve()
    parts = resolved.parts
    if "zoneinfo" not in parts:
        return None
    zoneinfo_index = parts.index("zoneinfo")
    value = "/".join(parts[zoneinfo_index + 1 :])
    if _is_known_timezone(value):
        return value
    return None


def _is_known_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return False
    return True


def _localize_now(now: datetime | None, zone: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def _target_date(*, date_text: str | None, today: bool, local_today: date_type) -> date_type:
    if today:
        return local_today
    if date_text is None:
        return local_today - timedelta(days=1)
    try:
        return date_type.fromisoformat(date_text)
    except ValueError as exc:
        raise PromptDiaryError(_invalid_date_message(date_text)) from exc


def _mutually_exclusive_date_message() -> str:
    return "--date and --today are mutually exclusive"


def _future_date_message(value: date_type) -> str:
    return f"Future report dates are not defined: {value.isoformat()}"


def _unknown_timezone_message(timezone_name: str) -> str:
    return f"Unknown IANA timezone: {timezone_name}"


def _invalid_date_message(date_text: str) -> str:
    return f"Invalid --date value {date_text!r}; expected YYYY-MM-DD"
