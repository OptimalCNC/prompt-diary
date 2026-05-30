"""Tests for the recording reporter test double."""

from __future__ import annotations

from prompt_diary.progress.events import PrepareFinished, TaskStarted
from tests.support.progress import RecordingReporter


def test_recording_reporter_captures_events_in_order() -> None:
    with RecordingReporter() as reporter:
        reporter.emit(PrepareFinished(at=0.0, projects=1, sessions=1))
        reporter.emit(
            TaskStarted(
                at=0.0, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1"
            )
        )
    assert [type(event).__name__ for event in reporter.events] == [
        "PrepareFinished",
        "TaskStarted",
    ]
    assert reporter.entered == 1
    assert reporter.exited == 1
