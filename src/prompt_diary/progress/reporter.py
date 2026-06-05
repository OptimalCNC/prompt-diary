"""Progress reporter protocol, no-op reporter, and mode selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from prompt_diary.progress.formatting import format_duration
from prompt_diary.progress.state import ProgressState, reduce

if TYPE_CHECKING:
    from types import TracebackType

    from prompt_diary.progress.events import ProgressEvent

ReporterMode = Literal["live", "log", "quiet"]


@runtime_checkable
class ProgressReporter(Protocol):
    """Sink for progress events; also a synchronous context manager for its lifecycle."""

    def emit(self, event: ProgressEvent) -> None:
        """Receive one progress event."""
        ...

    def __enter__(self) -> ProgressReporter:
        """Start the reporter."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the reporter."""
        ...


class NullProgressReporter:
    """A reporter that discards every event."""

    def emit(self, event: ProgressEvent) -> None:
        """Discard the event."""
        del event

    def __enter__(self) -> NullProgressReporter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback


NULL_REPORTER = NullProgressReporter()


@dataclass
class RecordingProgressReporter:
    """Forward events to another reporter while retaining reduced progress state."""

    inner: ProgressReporter
    _state: ProgressState = field(default_factory=ProgressState)

    def emit(self, event: ProgressEvent) -> None:
        """Record and forward the event."""
        self._state = reduce(self._state, event)
        self.inner.emit(event)

    def __enter__(self) -> RecordingProgressReporter:
        self.inner.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.inner.__exit__(exc_type, exc, traceback)

    @property
    def state(self) -> ProgressState:
        """Return the latest reduced state."""
        return self._state

    def timing_summary_message(self) -> str | None:
        """Return a final one-line timing summary, if any phases ran."""
        parts: list[str] = []
        for phase_id, label in _SUMMARY_PHASES:
            phase = self._state.phases.get(phase_id)
            if phase is None:
                continue
            parts.append(f"{format_duration(phase.elapsed_at(phase.finished_at or 0.0))} {label}")
        if not parts:
            return None
        return f"Spent {'; '.join(parts)}."


def select_reporter_mode(*, quiet: bool, isatty: bool) -> ReporterMode:
    """Choose the reporter mode from the quiet flag and terminal detection."""
    if quiet:
        return "quiet"
    return "live" if isatty else "log"


_SUMMARY_PHASES = (
    ("prepare", "preparing workspace"),
    ("evidence", "evidence"),
    ("project", "project"),
    ("daily", "daily"),
    ("rendering", "rendering"),
)
