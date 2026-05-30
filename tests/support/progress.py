"""Recording reporter test double for emit-site assertions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

    from prompt_diary.progress.events import ProgressEvent


@dataclass
class RecordingReporter:
    """Capture every emitted event for assertions; lifecycle is a no-op."""

    events: list[ProgressEvent] = field(default_factory=list)
    entered: int = 0
    exited: int = 0

    def emit(self, event: ProgressEvent) -> None:
        """Record the event."""
        self.events.append(event)

    def __enter__(self) -> RecordingReporter:
        self.entered += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.exited += 1
