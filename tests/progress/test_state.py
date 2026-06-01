"""Tests for the pure progress state reducer."""

from __future__ import annotations

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
from prompt_diary.progress.state import ProgressState, reduce


def _reduce_all(*events: object) -> ProgressState:
    state = ProgressState()
    for event in events:
        state = reduce(state, event)  # type: ignore[arg-type]
    return state


def test_prepare_steps_track_counts() -> None:
    state = _reduce_all(
        PrepareStarted(at=0.0, sources=("codex", "claude-code")),
        PrepareStep(at=0.1, name="copying_transcripts", done=2, total=9),
        PrepareStep(at=0.2, name="copying_transcripts", done=5, total=9),
        PrepareStep(
            at=0.3,
            name="scanning_sessions",
            done=7,
            total=12,
            scope="codex ~/.codex/sessions",
        ),
    )
    assert state.prepare_sources == ("codex", "claude-code")
    assert state.prepare_steps["copying_transcripts"] == (5, 9)
    assert state.prepare_step_scopes["scanning_sessions"]["codex ~/.codex/sessions"] == (7, 12)
    assert state.prepare_done is False


def test_prepare_step_order_uses_first_seen_step() -> None:
    state = _reduce_all(
        PrepareStep(
            at=0.1,
            name="scanning_sessions",
            done=12,
            total=20,
            scope="codex ~/.codex/sessions",
        ),
        PrepareStep(
            at=0.2,
            name="discovering",
            done=7,
            total=None,
            scope="codex ~/.codex/sessions",
        ),
        PrepareStep(at=0.3, name="discovering", done=16, total=None),
    )
    assert state.prepare_step_order == ("scanning_sessions", "discovering")


def test_prepare_finished_records_totals() -> None:
    state = _reduce_all(PrepareFinished(at=0.3, projects=2, sessions=9))
    assert state.prepare_done is True
    assert state.prepare_projects == 2
    assert state.prepare_sessions == 9


def test_run_tracks_kind_totals_and_running_count() -> None:
    state = _reduce_all(
        RunStarted(at=0.0, label="2026-05-30", kind_totals=(("evidence_extraction", 2),)),
        TaskStarted(
            at=0.1, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"
        ),
        TaskStarted(
            at=0.1, kind="evidence_extraction", task_id="b", project_key="p", session_ref="S2"
        ),
    )
    assert state.label == "2026-05-30"
    assert state.kind_total("evidence_extraction") == 2
    assert state.running_count("evidence_extraction") == 2
    assert state.done_count("evidence_extraction") == 0


def test_turn_advanced_sets_turn_counter() -> None:
    state = _reduce_all(
        TaskStarted(
            at=0.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"
        ),
        TurnAdvanced(at=0.1, task_id="a", turn_index=3, total_turns=8, turn_ref="T0003"),
    )
    row = state.tasks["a"]
    assert row.turn_index == 3
    assert row.total_turns == 8
    assert row.status == "running"


def test_task_finished_marks_status_elapsed_and_done_count() -> None:
    state = _reduce_all(
        RunStarted(at=0.0, label="d", kind_totals=(("evidence_extraction", 1),)),
        TaskStarted(
            at=1.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"
        ),
        TurnAdvanced(at=1.5, task_id="a", turn_index=5, total_turns=5, turn_ref="T0005"),
        TaskFinished(
            at=4.0,
            kind="evidence_extraction",
            task_id="a",
            project_key="p",
            session_ref="S1",
            status="success",
            error=None,
        ),
    )
    row = state.tasks["a"]
    assert row.status == "success"
    assert row.elapsed == 3.0
    assert row.total_turns == 5
    assert state.done_count("evidence_extraction") == 1
    assert state.running_count("evidence_extraction") == 0


def test_failed_task_keeps_error_and_running_continues() -> None:
    state = _reduce_all(
        TaskStarted(
            at=0.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"
        ),
        TaskStarted(
            at=0.0, kind="evidence_extraction", task_id="b", project_key="p", session_ref="S2"
        ),
        TaskFinished(
            at=1.0,
            kind="evidence_extraction",
            task_id="a",
            project_key="p",
            session_ref="S1",
            status="failed",
            error="boom",
        ),
    )
    assert state.tasks["a"].status == "failed"
    assert state.tasks["a"].error == "boom"
    assert state.running_count("evidence_extraction") == 1


def test_blocked_status_is_counted_done_not_running() -> None:
    state = _reduce_all(
        TaskFinished(
            at=1.0,
            kind="project_synthesis",
            task_id="proj",
            project_key="p",
            session_ref=None,
            status="blocked",
            error="dep failed",
        ),
    )
    assert state.tasks["proj"].status == "blocked"
    assert state.running_count("project_synthesis") == 0
    assert state.done_count("project_synthesis") == 1


def test_turn_advanced_without_prior_start_creates_row() -> None:
    # reduce is a total fold over the event type; a TurnAdvanced with no prior
    # TaskStarted must still produce a usable row (covers the fallback branch).
    state = _reduce_all(
        TurnAdvanced(at=0.0, task_id="a", turn_index=1, total_turns=4, turn_ref="T0001"),
    )
    assert state.tasks["a"].total_turns == 4
    assert state.tasks["a"].kind == "evidence_extraction"


def test_run_finished_sets_run_done() -> None:
    state = _reduce_all(RunFinished(at=0.0, succeeded=1, failed=0, blocked=0))
    assert state.run_done is True


def test_elapsed_is_none_while_running() -> None:
    # Covers the ``elapsed`` property's None branch for an in-flight task.
    state = _reduce_all(
        TaskStarted(
            at=0.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"
        ),
    )
    assert state.tasks["a"].elapsed is None
