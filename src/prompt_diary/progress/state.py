"""Pure reducer folding progress events into a renderable snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from prompt_diary.progress.events import (
    PhaseFinished,
    PhaseStarted,
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
class PhaseRow:
    """Current timing snapshot for one named progress phase."""

    phase_id: str
    label: str
    status: str = "running"
    started_at: float | None = None
    active_started_at: float | None = None
    finished_at: float | None = None
    elapsed: float = 0.0

    @property
    def is_running(self) -> bool:
        """Return whether this phase currently has an active segment."""
        return self.active_started_at is not None

    def elapsed_at(self, at: float) -> float:
        """Return elapsed wall time including the active segment, if any."""
        if self.active_started_at is None:
            return self.elapsed
        return self.elapsed + max(0.0, at - self.active_started_at)


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
    phases: dict[str, PhaseRow] = field(default_factory=dict)
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
    if isinstance(event, PhaseStarted | PhaseFinished):
        return _reduce_phase(state, event)
    if isinstance(event, PrepareStarted | PrepareStep | PrepareFinished):
        return _reduce_prepare(state, event)
    if isinstance(event, RunStarted | RunFinished):
        return _reduce_run(state, event)
    return _reduce_task(state, event)


def _reduce_phase(
    state: ProgressState,
    event: PhaseStarted | PhaseFinished,
) -> ProgressState:
    if isinstance(event, PhaseStarted):
        phases = dict(state.phases)
        existing = phases.get(event.phase_id)
        phases[event.phase_id] = PhaseRow(
            phase_id=event.phase_id,
            label=event.label,
            status="running",
            started_at=existing.started_at if existing is not None else event.at,
            active_started_at=event.at,
            finished_at=existing.finished_at if existing is not None else None,
            elapsed=existing.elapsed if existing is not None else 0.0,
        )
        return replace(state, phases=phases)
    return replace(state, phases=_finish_phase(state, event))


def _finish_phase(state: ProgressState, event: PhaseFinished) -> dict[str, PhaseRow]:
    phases = dict(state.phases)
    existing = phases.get(event.phase_id)
    if existing is None:
        phases[event.phase_id] = PhaseRow(
            phase_id=event.phase_id,
            label=event.phase_id,
            status=event.status,
            started_at=event.at,
            active_started_at=None,
            finished_at=event.at,
            elapsed=0.0,
        )
        return phases
    elapsed = existing.elapsed
    if existing.active_started_at is not None:
        elapsed += max(0.0, event.at - existing.active_started_at)
    phases[event.phase_id] = replace(
        existing,
        status=event.status,
        active_started_at=None,
        finished_at=event.at,
        elapsed=elapsed,
    )
    return phases


def _reduce_prepare(
    state: ProgressState,
    event: PrepareStarted | PrepareStep | PrepareFinished,
) -> ProgressState:
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
    return replace(
        state,
        prepare_done=True,
        prepare_projects=event.projects,
        prepare_sessions=event.sessions,
    )


def _reduce_run(state: ProgressState, event: RunStarted | RunFinished) -> ProgressState:
    if isinstance(event, RunStarted):
        return replace(state, label=event.label, kind_totals=event.kind_totals)
    return replace(state, run_done=True)


def _reduce_task(
    state: ProgressState,
    event: TaskStarted | TurnAdvanced | TaskFinished,
) -> ProgressState:
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
