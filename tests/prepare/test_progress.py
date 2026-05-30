"""Prepare emits progress events for each stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.progress.events import PrepareStep
from prompt_diary.targeting.resolve import resolve_report_target
from tests.support.progress import RecordingReporter

if TYPE_CHECKING:
    from pathlib import Path


def test_prepare_emits_full_event_sequence_with_zero_sessions(tmp_path: Path) -> None:
    target = resolve_report_target(date="2026-05-30", today=False, timezone_name="UTC")
    reporter = RecordingReporter()
    prepare_workspace(
        target,
        reports_root=tmp_path / ".reports",
        source_specs=(),
        reporter=reporter,
    )
    names = [type(event).__name__ for event in reporter.events]
    assert names == ["PrepareStarted", "PrepareStep", "PrepareStep", "PrepareFinished"]
    steps = [event for event in reporter.events if isinstance(event, PrepareStep)]
    assert [(step.name, step.done, step.total) for step in steps] == [
        ("discovering", 0, None),
        ("assigning_projects", 0, None),
    ]
