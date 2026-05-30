"""Progress event value types emitted by prepare and generate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrepareStarted:
    """Preparation began for a target day."""

    at: float
    sources: tuple[str, ...]


@dataclass(frozen=True)
class PrepareStep:
    """One preparation step advanced. ``total`` is ``None`` when unknown in advance."""

    at: float
    name: str
    done: int
    total: int | None


@dataclass(frozen=True)
class PrepareFinished:
    """Preparation completed."""

    at: float
    projects: int
    sessions: int


@dataclass(frozen=True)
class RunStarted:
    """A generation run began. ``kind_totals`` maps task kind to its task count."""

    at: float
    label: str
    kind_totals: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class TaskStarted:
    """One generation task entered the running state."""

    at: float
    kind: str
    task_id: str
    project_key: str | None
    session_ref: str | None


@dataclass(frozen=True)
class TurnAdvanced:
    """An evidence task committed one indexed turn."""

    at: float
    task_id: str
    turn_index: int
    total_turns: int
    turn_ref: str


@dataclass(frozen=True)
class TaskFinished:
    """One generation task reached a terminal status."""

    at: float
    kind: str
    task_id: str
    project_key: str | None
    session_ref: str | None
    status: str
    error: str | None


@dataclass(frozen=True)
class RunFinished:
    """A generation run completed."""

    at: float
    succeeded: int
    failed: int
    blocked: int


ProgressEvent = (
    PrepareStarted
    | PrepareStep
    | PrepareFinished
    | RunStarted
    | TaskStarted
    | TurnAdvanced
    | TaskFinished
    | RunFinished
)
