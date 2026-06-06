"""Tests for the reporter protocol, null reporter, and mode selection."""

from __future__ import annotations

from prompt_diary.progress.events import PhaseFinished, PhaseStarted, PrepareFinished
from prompt_diary.progress.reporter import (
    NULL_REPORTER,
    NullProgressReporter,
    RecordingProgressReporter,
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


def test_recording_reporter_formats_timing_summary() -> None:
    reporter = RecordingProgressReporter(inner=NULL_REPORTER)

    reporter.emit(PhaseStarted(at=0.0, phase_id="prepare", label="prepare"))
    reporter.emit(PhaseFinished(at=2.25, phase_id="prepare", status="success"))
    reporter.emit(PhaseStarted(at=3.0, phase_id="evidence", label="evidence"))
    reporter.emit(PhaseFinished(at=68.0, phase_id="evidence", status="success"))
    reporter.emit(PhaseStarted(at=68.0, phase_id="rendering", label="rendering"))
    reporter.emit(PhaseFinished(at=68.5, phase_id="rendering", status="success"))
    reporter.emit(PhaseStarted(at=69.0, phase_id="publish", label="publish"))
    reporter.emit(PhaseFinished(at=70.5, phase_id="publish", status="success"))

    assert reporter.state.phases["prepare"].status == "success"
    # The summary lists every phase that ran, in pipeline order, including rendering and publish.
    assert reporter.timing_summary_message() == (
        "Spent 2.2s preparing workspace; 1m05s evidence; 0.5s rendering; 1.5s publish."
    )
