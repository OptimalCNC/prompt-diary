"""Tests for progress event value types."""

from __future__ import annotations

import dataclasses

import pytest

from prompt_diary.progress.events import (
    PhaseFinished,
    PhaseStarted,
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
    RunFinished,
    RunStarted,
    TaskFinished,
    TaskStarted,
    TurnAdvanced,
)


def test_events_are_frozen_and_carry_fields() -> None:
    started = TaskStarted(
        at=1.0,
        kind="evidence_extraction",
        task_id="evidence:p:S1",
        project_key="p",
        session_ref="S1",
    )
    assert started.at == 1.0
    assert started.task_id == "evidence:p:S1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        started.at = 2.0  # type: ignore[misc]


def test_event_construction_covers_all_types() -> None:
    events = [
        PrepareStarted(at=0.0, sources=("codex", "claude-code")),
        PrepareStep(at=0.1, name="assigning_projects", done=2, total=None),
        PrepareFinished(at=0.2, projects=2, sessions=9),
        PhaseStarted(at=0.3, phase_id="prepare", label="prepare"),
        PhaseFinished(at=0.4, phase_id="prepare", status="success"),
        RunStarted(at=0.5, label="2026-05-30", kind_totals=(("evidence_extraction", 9),)),
        TaskStarted(
            at=0.6,
            kind="evidence_extraction",
            task_id="t",
            project_key="p",
            session_ref="S1",
        ),
        TurnAdvanced(at=0.7, task_id="t", turn_index=1, total_turns=5, turn_ref="T0001"),
        TaskFinished(
            at=0.8,
            kind="evidence_extraction",
            task_id="t",
            project_key="p",
            session_ref="S1",
            status="success",
            error=None,
        ),
        RunFinished(at=0.9, succeeded=8, failed=1, blocked=0),
    ]
    assert len(events) == 10
