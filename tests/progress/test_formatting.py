"""Tests for progress display formatting helpers."""

from __future__ import annotations

from prompt_diary.progress.formatting import format_duration


def test_format_duration_uses_seconds_minutes_and_hours() -> None:
    assert format_duration(2.25) == "2.2s"
    assert format_duration(65.2) == "1m05s"
    assert format_duration(3661.0) == "1h01m01s"
