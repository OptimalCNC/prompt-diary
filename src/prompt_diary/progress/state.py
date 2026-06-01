"""Pure reducer folding progress events into a renderable snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from prompt_diary.progress.events import (
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
    ProgressEvent,
    RunFinished,
    RunStarted,
    TaskFinished,
    TaskStarted,
    TurnAdvanced,
)

_TERMINAL_STATUSES = frozenset({"success", "failed", "blocked"})


@dataclass(frozen=True)
class TaskRow:
    """Current snapshot of one generation task."""

    kind: str
    task_id: str
    project_key: str | None
    session_ref: str | None
    status: str = "running"
    turn_index: int = 0
    total_turns: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    error: str | None = None

    @property
    def elapsed(self) -> float | None:
        """Return finished-task wall time, or ``None`` while still running."""
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at


@dataclass(frozen=True)
class ProgressState:
    """Immutable aggregate of every progress event seen so far."""

    label: str = ""
    kind_totals: tuple[tuple[str, int], ...] = ()
    prepare_sources: tuple[str, ...] = ()
    prepare_step_order: tuple[str, ...] = ()
    prepare_steps: dict[str, tuple[int, int | None]] = field(default_factory=dict)
    prepare_step_scopes: dict[str, dict[str, tuple[int, int | None]]] = field(default_factory=dict)
    prepare_done: bool = False
    prepare_projects: int = 0
    prepare_sessions: int = 0
    tasks: dict[str, TaskRow] = field(default_factory=dict)
    run_done: bool = False

    def kind_total(self, kind: str) -> int:
        """Return the planned task count for a kind."""
        return dict(self.kind_totals).get(kind, 0)

    def running_count(self, kind: str) -> int:
        """Return the number of in-flight tasks for a kind."""
        return sum(1 for row in self.tasks.values() if row.kind == kind and row.status == "running")

    def done_count(self, kind: str) -> int:
        """Return the number of terminal tasks for a kind."""
        return sum(
            1
            for row in self.tasks.values()
            if row.kind == kind and row.status in _TERMINAL_STATUSES
        )


def reduce(state: ProgressState, event: ProgressEvent) -> ProgressState:
    """Fold one event into the state, returning a new immutable state."""
    if isinstance(event, PrepareStarted):
        return replace(state, prepare_sources=event.sources)
    if isinstance(event, PrepareStep):
        step_order = state.prepare_step_order
        if event.name not in step_order:
            step_order = (*step_order, event.name)
        if event.scope is None:
            steps = dict(state.prepare_steps)
            steps[event.name] = (event.done, event.total)
            return replace(state, prepare_step_order=step_order, prepare_steps=steps)
        scopes = {name: dict(values) for name, values in state.prepare_step_scopes.items()}
        scopes.setdefault(event.name, {})[event.scope] = (event.done, event.total)
        return replace(state, prepare_step_order=step_order, prepare_step_scopes=scopes)
    if isinstance(event, PrepareFinished):
        return replace(
            state,
            prepare_done=True,
            prepare_projects=event.projects,
            prepare_sessions=event.sessions,
        )
    if isinstance(event, RunStarted):
        return replace(state, label=event.label, kind_totals=event.kind_totals)
    if isinstance(event, TaskStarted):
        tasks = dict(state.tasks)
        tasks[event.task_id] = TaskRow(
            kind=event.kind,
            task_id=event.task_id,
            project_key=event.project_key,
            session_ref=event.session_ref,
            started_at=event.at,
        )
        return replace(state, tasks=tasks)
    if isinstance(event, TurnAdvanced):
        tasks = dict(state.tasks)
        existing = tasks.get(event.task_id)
        base = (
            existing
            if existing is not None
            else TaskRow(
                kind="evidence_extraction",
                task_id=event.task_id,
                project_key=None,
                session_ref=None,
                started_at=event.at,
            )
        )
        tasks[event.task_id] = replace(
            base, turn_index=event.turn_index, total_turns=event.total_turns
        )
        return replace(state, tasks=tasks)
    if isinstance(event, TaskFinished):
        tasks = dict(state.tasks)
        existing = tasks.get(event.task_id)
        base = (
            existing
            if existing is not None
            else TaskRow(
                kind=event.kind,
                task_id=event.task_id,
                project_key=event.project_key,
                session_ref=event.session_ref,
                started_at=event.at,
            )
        )
        tasks[event.task_id] = replace(
            base, status=event.status, finished_at=event.at, error=event.error
        )
        return replace(state, tasks=tasks)
    assert isinstance(event, RunFinished)  # noqa: S101
    return replace(state, run_done=True)
