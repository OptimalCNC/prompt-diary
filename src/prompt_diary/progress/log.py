"""Append-only log-line progress reporter for non-TTY output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.progress.events import (
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
    RunStarted,
    TaskFinished,
    TaskStarted,
    TurnAdvanced,
)

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
    if isinstance(event, PrepareStarted):
        return f"prepare: starting (sources: {', '.join(event.sources)})"
    if isinstance(event, PrepareStep):
        if event.total is None:
            return f"prepare: {event.name} {event.done}"
        return f"prepare: {event.name} {event.done}/{event.total}"
    if isinstance(event, PrepareFinished):
        return f"prepare: ready ({event.projects} projects, {event.sessions} sessions)"
    if isinstance(event, RunStarted):
        totals = ", ".join(f"{kind}: {count}" for kind, count in event.kind_totals)
        return f"generate: starting {event.label} ({totals})"
    if isinstance(event, TaskStarted):
        return f"{event.kind}: start {_label(event.project_key, event.session_ref)}"
    if isinstance(event, TurnAdvanced):
        return (
            f"evidence_extraction: p-turn {event.turn_ref} ({event.turn_index}/{event.total_turns})"
        )
    if isinstance(event, TaskFinished):
        label = _label(event.project_key, event.session_ref)
        suffix = f" {event.error}" if event.error else ""
        return f"{event.kind}: done {label} [{event.status}]{suffix}"
    return None  # RunFinished: summary printed separately by the CLI


class LogReporter:
    """Write one log line per event to a text stream."""

    def __init__(self, *, stream: TextIO) -> None:
        self._stream = stream

    def emit(self, event: ProgressEvent) -> None:
        """Format and write the event, if it has a log line."""
        line = format_event(event)
        if line is not None:
            self._stream.write(line + "\n")

    def __enter__(self) -> LogReporter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
