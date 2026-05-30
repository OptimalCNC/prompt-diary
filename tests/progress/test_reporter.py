"""Tests for the reporter protocol, null reporter, and mode selection."""

from __future__ import annotations

from prompt_diary.progress.events import PrepareFinished
from prompt_diary.progress.reporter import (
    NULL_REPORTER,
    NullProgressReporter,
    select_reporter_mode,
)


def test_null_reporter_is_a_noop_context_manager() -> None:
    reporter = NullProgressReporter()
    with reporter as entered:
        entered.emit(PrepareFinished(at=0.0, projects=1, sessions=1))
    assert NULL_REPORTER.emit(PrepareFinished(at=0.0, projects=1, sessions=1)) is None


def test_select_reporter_mode() -> None:
    assert select_reporter_mode(quiet=True, isatty=True) == "quiet"
    assert select_reporter_mode(quiet=True, isatty=False) == "quiet"
    assert select_reporter_mode(quiet=False, isatty=True) == "live"
    assert select_reporter_mode(quiet=False, isatty=False) == "log"
