"""Tests for log-line formatting and the streaming log reporter."""

from __future__ import annotations

import io

from prompt_diary.progress.events import (
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
    RunFinished,
    RunStarted,
    TaskFinished,
    TaskStarted,
    TurnAdvanced,
)
from prompt_diary.progress.log import LogReporter, format_event


def test_format_event_lines() -> None:
    assert format_event(PrepareStarted(at=0.0, sources=("codex", "claude-code"))) == (
        "prepare: starting (sources: codex, claude-code)"
    )
    assert format_event(PrepareStep(at=0.0, name="copying_transcripts", done=4, total=9)) == (
        "prepare: copying_transcripts 4/9"
    )
    assert format_event(
        PrepareStep(
            at=0.0,
            name="scanning_sessions",
            done=2,
            total=7,
            scope="codex ~/.codex/sessions",
        )
    ) == ("prepare: scanning_sessions codex ~/.codex/sessions 2/7")
    assert format_event(PrepareStep(at=0.0, name="discovering", done=3, total=None)) == (
        "prepare: discovering 3"
    )
    assert format_event(PrepareFinished(at=0.0, projects=2, sessions=9)) == (
        "prepare: ready (2 projects, 9 sessions)"
    )
    assert (
        format_event(
            RunStarted(at=0.0, label="2026-05-30", kind_totals=(("evidence_extraction", 9),))
        )
        == "generate: starting 2026-05-30 (evidence_extraction: 9)"
    )
    assert (
        format_event(
            TaskStarted(
                at=0.0, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1"
            )
        )
        == "evidence_extraction: start p/S1"
    )
    assert (
        format_event(
            TaskStarted(
                at=0.0, kind="daily_synthesis", task_id="daily", project_key=None, session_ref=None
            )
        )
        == "daily_synthesis: start ?"
    )
    assert (
        format_event(
            TurnAdvanced(at=0.0, task_id="t", turn_index=3, total_turns=8, turn_ref="T0003")
        )
        == "evidence_extraction: p-turn T0003 (3/8)"
    )
    assert (
        format_event(
            TaskFinished(
                at=0.0,
                kind="evidence_extraction",
                task_id="t",
                project_key="p",
                session_ref="S1",
                status="success",
                error=None,
            )
        )
        == "evidence_extraction: done p/S1 [success]"
    )
    assert (
        format_event(
            TaskFinished(
                at=0.0,
                kind="project_synthesis",
                task_id="proj",
                project_key="p",
                session_ref=None,
                status="failed",
                error="boom",
            )
        )
        == "project_synthesis: done p [failed] boom"
    )


def test_run_finished_has_no_log_line() -> None:
    assert format_event(RunFinished(at=0.0, succeeded=1, failed=0, blocked=0)) is None


def test_log_reporter_writes_lines_and_skips_none() -> None:
    stream = io.StringIO()
    with LogReporter(stream=stream) as reporter:
        reporter.emit(PrepareFinished(at=0.0, projects=1, sessions=1))
        reporter.emit(
            TaskStarted(
                at=0.0,
                kind="evidence_extraction",
                task_id="t",
                project_key="p",
                session_ref="S1",
            )
        )
        reporter.emit(RunFinished(at=0.0, succeeded=1, failed=0, blocked=0))  # skipped (None)
    assert stream.getvalue() == (
        "prepare: ready (1 projects, 1 sessions)\nevidence_extraction: start p/S1\n"
    )
