"""Append-only log-line progress reporter for non-TTY output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.progress.events import (
    PhaseFinished,
    PhaseStarted,
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
    RunStarted,
    TaskFinished,
    TaskStarted,
    TurnAdvanced,
)
from prompt_diary.progress.formatting import format_duration
from prompt_diary.progress.state import ProgressState, reduce

if TYPE_CHECKING:
    from types import TracebackType
    from typing import TextIO

    from prompt_diary.progress.events import ProgressEvent


def _label(project_key: str | None, session_ref: str | None) -> str:
    if project_key is None:
        return "?"
    if session_ref is None:
        return project_key
    return f"{project_key}/{session_ref}"


def format_event(event: ProgressEvent) -> str | None:
    """Render one event as a single log line, or ``None`` to skip it."""
    if isinstance(event, PhaseStarted):
        return f"phase: start {event.label}"
    if isinstance(event, PhaseFinished):
        return f"phase: done {event.phase_id} [{event.status}]"
    if isinstance(event, PrepareStarted | PrepareStep | PrepareFinished):
        return _format_prepare_event(event)
    if isinstance(event, RunStarted | TaskStarted | TurnAdvanced | TaskFinished):
        return _format_generate_event(event)
    return None  # RunFinished: summary printed separately by the CLI


def _format_prepare_event(event: PrepareStarted | PrepareStep | PrepareFinished) -> str:
    if isinstance(event, PrepareStarted):
        return f"prepare: starting (sources: {', '.join(event.sources)})"
    if isinstance(event, PrepareFinished):
        return f"prepare: ready ({event.projects} projects, {event.sessions} sessions)"
    label = f"{event.name} {event.scope}" if event.scope is not None else event.name
    if event.total is None:
        return f"prepare: {label} {event.done}"
    return f"prepare: {label} {event.done}/{event.total}"


def _format_generate_event(
    event: RunStarted | TaskStarted | TurnAdvanced | TaskFinished,
) -> str:
    if isinstance(event, RunStarted):
        totals = ", ".join(f"{kind}: {count}" for kind, count in event.kind_totals)
        return f"generate: starting {event.label} ({totals})"
    if isinstance(event, TaskStarted):
        return f"{event.kind}: start {_label(event.project_key, event.session_ref)}"
    if isinstance(event, TurnAdvanced):
        return (
            f"evidence_extraction: p-turn {event.turn_ref} ({event.turn_index}/{event.total_turns})"
        )
    label = _label(event.project_key, event.session_ref)
    suffix = f" {event.error}" if event.error else ""
    return f"{event.kind}: done {label} [{event.status}]{suffix}"


class LogReporter:
    """Write one log line per event to a text stream."""

    def __init__(self, *, stream: TextIO) -> None:
        self._stream = stream
        self._state = ProgressState()

    def emit(self, event: ProgressEvent) -> None:
        """Format and write the event, if it has a log line."""
        line = _format_event_with_state(self._state, event)
        self._state = reduce(self._state, event)
        if line is not None:
            self._stream.write(line + "\n")
            self._stream.flush()

    def __enter__(self) -> LogReporter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback


def _format_event_with_state(state: ProgressState, event: ProgressEvent) -> str | None:
    if isinstance(event, PhaseFinished):
        next_state = reduce(state, event)
        phase = next_state.phases[event.phase_id]
        return f"{format_event(event)} {format_duration(phase.elapsed_at(event.at))}"
    return format_event(event)
