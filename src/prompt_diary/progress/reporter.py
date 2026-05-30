"""Progress reporter protocol, no-op reporter, and mode selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

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


def select_reporter_mode(*, quiet: bool, isatty: bool) -> ReporterMode:
    """Choose the reporter mode from the quiet flag and terminal detection."""
    if quiet:
        return "quiet"
    return "live" if isatty else "log"
