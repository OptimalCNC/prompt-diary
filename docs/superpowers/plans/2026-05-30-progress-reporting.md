# Progress Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the user continuous, legible terminal feedback while `prepare` and `generate` run, via a live Rich dashboard on a TTY and tested log lines elsewhere.

**Architecture:** A pure `progress/` package — frozen event dataclasses → a pure `reduce(state, event)` reducer → a narrow `ProgressReporter` protocol with a no-op default. Two real reporters (`LogReporter`, `LiveConsoleReporter`) are selected by `isatty()`/`--quiet`. The pipeline, evidence runner, and `prepare_workspace` emit events into the threaded-through reporter; only the Rich console module is coverage-omitted.

**Tech Stack:** Python 3.10+, `rich` (already transitive via typer), `typer`, `pytest`, `coverage` (100% gate), `basedpyright` (strict), `ruff`.

---

## Spec

`docs/superpowers/specs/2026-05-30-progress-reporting-design.md`.

## Conventions for every task

- Run the full gate before each commit: `uv run coverage run -m pytest && uv run coverage report && uv run basedpyright && uv run ruff check && uv run ruff format --check`.
- 100% coverage is enforced. Only `src/prompt_diary/progress/console.py` is coverage-omitted (added in Task 6).
- All new modules start with `"""docstring."""` then `from __future__ import annotations`.

## File structure

Create:
- `src/prompt_diary/progress/__init__.py` — package exports.
- `src/prompt_diary/progress/events.py` — frozen event dataclasses + `ProgressEvent` union.
- `src/prompt_diary/progress/state.py` — `ProgressState`, `TaskRow`, `reduce`.
- `src/prompt_diary/progress/reporter.py` — `ProgressReporter` protocol, `NullProgressReporter`, `NULL_REPORTER`, `select_reporter_mode`.
- `src/prompt_diary/progress/log.py` — `format_event`, `LogReporter`.
- `src/prompt_diary/progress/console.py` — `LiveConsoleReporter`, `build_reporter` (COVERAGE-OMITTED).
- `tests/support/progress.py` — `RecordingReporter` test double.
- `tests/progress/__init__.py`, `tests/progress/test_events.py`, `test_state.py`, `test_reporter.py`, `test_log.py`.

Modify:
- `pyproject.toml` — add `rich` dependency; add `progress/console.py` to coverage omit.
- `src/prompt_diary/generate/pipeline.py` — `PhaseRunner.run` gains `reporter`; `GeneratePipelineRunner` holds a reporter and emits task events; `run_generation_task*` gain `reporter`.
- `src/prompt_diary/generate/evidence_extraction/runner.py` — emit `TurnAdvanced`.
- `src/prompt_diary/generate/project_synthesis/runner.py`, `daily_synthesis/runner.py` — accept `reporter` in `run`.
- `src/prompt_diary/generate/workflow.py` — thread `reporter`; emit `RunStarted`/`RunFinished`.
- `src/prompt_diary/prepare/workspace.py` — accept `reporter`; emit prepare events.
- `src/prompt_diary/cmds/common.py` — `QuietOption`, `build_cli_reporter`.
- `src/prompt_diary/cmds/prepare.py`, `cmds/generate.py` — build + thread reporter; always echo final summary.
- Tests touching the above signatures: `tests/generate/test_pipeline.py`, `test_workflow.py`, `tests/generate/evidence_extraction/test_runner.py`, `tests/prepare/*`, `tests/cmds/*`, `tests/test_cli.py`.
- Docs (Task 12).

---

## Task 1: Event types (`events.py`)

**Files:**
- Create: `src/prompt_diary/progress/events.py`
- Create: `tests/progress/__init__.py`, `tests/progress/test_events.py`

- [ ] **Step 1: Write the failing test**

`tests/progress/__init__.py`: empty file.

`tests/progress/test_events.py`:

```python
"""Tests for progress event value types."""

from __future__ import annotations

import dataclasses

import pytest

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


def test_events_are_frozen_and_carry_fields() -> None:
    started = TaskStarted(
        at=1.0, kind="evidence_extraction", task_id="evidence:p:S1",
        project_key="p", session_ref="S1",
    )
    assert started.at == 1.0
    assert started.task_id == "evidence:p:S1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        started.at = 2.0  # type: ignore[misc]


def test_event_construction_covers_all_types() -> None:
    events = [
        PrepareStarted(at=0.0, sources=("codex", "claude-code")),
        PrepareStep(at=0.1, name="copying_transcripts", done=2, total=9),
        PrepareFinished(at=0.2, projects=2, sessions=9),
        RunStarted(at=0.3, label="2026-05-30", kind_totals=(("evidence_extraction", 9),)),
        TaskStarted(at=0.4, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1"),
        TurnAdvanced(at=0.5, task_id="t", turn_index=1, total_turns=5, turn_ref="T0001"),
        TaskFinished(at=0.6, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1", status="success", error=None),
        RunFinished(at=0.7, succeeded=8, failed=1, blocked=0),
    ]
    assert len(events) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/progress/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: prompt_diary.progress.events`.

- [ ] **Step 3: Write minimal implementation**

`src/prompt_diary/progress/events.py`:

```python
"""Progress event value types emitted by prepare and generate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


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


ProgressEvent = Union[
    PrepareStarted,
    PrepareStep,
    PrepareFinished,
    RunStarted,
    TaskStarted,
    TurnAdvanced,
    TaskFinished,
    RunFinished,
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/progress/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/prompt_diary/progress/events.py tests/progress/__init__.py tests/progress/test_events.py
git commit -m "feat(progress): add progress event value types"
```

---

## Task 2: State reducer (`state.py`)

**Files:**
- Create: `src/prompt_diary/progress/state.py`
- Create: `tests/progress/test_state.py`

- [ ] **Step 1: Write the failing test**

`tests/progress/test_state.py`:

```python
"""Tests for the pure progress state reducer."""

from __future__ import annotations

from prompt_diary.progress.events import (
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
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
    )
    assert state.prepare_sources == ("codex", "claude-code")
    assert state.prepare_steps["copying_transcripts"] == (5, 9)
    assert state.prepare_done is False


def test_prepare_finished_records_totals() -> None:
    state = _reduce_all(PrepareFinished(at=0.3, projects=2, sessions=9))
    assert state.prepare_done is True
    assert state.prepare_projects == 2
    assert state.prepare_sessions == 9


def test_run_tracks_kind_totals_and_running_count() -> None:
    state = _reduce_all(
        RunStarted(at=0.0, label="2026-05-30", kind_totals=(("evidence_extraction", 2),)),
        TaskStarted(at=0.1, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"),
        TaskStarted(at=0.1, kind="evidence_extraction", task_id="b", project_key="p", session_ref="S2"),
    )
    assert state.label == "2026-05-30"
    assert state.kind_total("evidence_extraction") == 2
    assert state.running_count("evidence_extraction") == 2
    assert state.done_count("evidence_extraction") == 0


def test_turn_advanced_sets_turn_counter() -> None:
    state = _reduce_all(
        TaskStarted(at=0.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"),
        TurnAdvanced(at=0.1, task_id="a", turn_index=3, total_turns=8, turn_ref="T0003"),
    )
    row = state.tasks["a"]
    assert row.turn_index == 3
    assert row.total_turns == 8
    assert row.status == "running"


def test_task_finished_marks_status_elapsed_and_done_count() -> None:
    state = _reduce_all(
        RunStarted(at=0.0, label="d", kind_totals=(("evidence_extraction", 1),)),
        TaskStarted(at=1.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"),
        TurnAdvanced(at=1.5, task_id="a", turn_index=5, total_turns=5, turn_ref="T0005"),
        TaskFinished(at=4.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1", status="success", error=None),
    )
    row = state.tasks["a"]
    assert row.status == "success"
    assert row.elapsed == 3.0
    assert row.total_turns == 5
    assert state.done_count("evidence_extraction") == 1
    assert state.running_count("evidence_extraction") == 0


def test_failed_task_keeps_error_and_running_continues() -> None:
    state = _reduce_all(
        TaskStarted(at=0.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1"),
        TaskStarted(at=0.0, kind="evidence_extraction", task_id="b", project_key="p", session_ref="S2"),
        TaskFinished(at=1.0, kind="evidence_extraction", task_id="a", project_key="p", session_ref="S1", status="failed", error="boom"),
    )
    assert state.tasks["a"].status == "failed"
    assert state.tasks["a"].error == "boom"
    assert state.running_count("evidence_extraction") == 1


def test_blocked_status_is_counted_done_not_running() -> None:
    state = _reduce_all(
        TaskFinished(at=1.0, kind="project_synthesis", task_id="proj", project_key="p", session_ref=None, status="blocked", error="dep failed"),
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
    from prompt_diary.progress.events import RunFinished

    state = _reduce_all(RunFinished(at=0.0, succeeded=1, failed=0, blocked=0))
    assert state.run_done is True
```

Add `RunFinished` to the imports at the top of `test_state.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/progress/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: prompt_diary.progress.state`.

- [ ] **Step 3: Write minimal implementation**

`src/prompt_diary/progress/state.py`:

```python
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
    prepare_steps: dict[str, tuple[int, int | None]] = field(default_factory=dict)
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
        return sum(
            1 for row in self.tasks.values() if row.kind == kind and row.status == "running"
        )

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
        steps = dict(state.prepare_steps)
        steps[event.name] = (event.done, event.total)
        return replace(state, prepare_steps=steps)
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
        base = existing if existing is not None else TaskRow(
            kind="evidence_extraction",
            task_id=event.task_id,
            project_key=None,
            session_ref=None,
            started_at=event.at,
        )
        tasks[event.task_id] = replace(
            base, turn_index=event.turn_index, total_turns=event.total_turns
        )
        return replace(state, tasks=tasks)
    if isinstance(event, TaskFinished):
        tasks = dict(state.tasks)
        existing = tasks.get(event.task_id)
        base = existing if existing is not None else TaskRow(
            kind=event.kind,
            task_id=event.task_id,
            project_key=event.project_key,
            session_ref=event.session_ref,
            started_at=event.at,
        )
        tasks[event.task_id] = replace(base, status=event.status, finished_at=event.at)
        return replace(state, tasks=tasks)
    return replace(state, run_done=True)  # RunFinished
```

Note on the final branch: `basedpyright` strict treats the `RunFinished` branch as the exhaustive fallback. If it reports `event` as not narrowed, add `assert isinstance(event, RunFinished)` before the final `return`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/progress/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/prompt_diary/progress/state.py tests/progress/test_state.py
git commit -m "feat(progress): add pure state reducer"
```

---

## Task 3: Reporter protocol, null reporter, mode selection (`reporter.py`)

**Files:**
- Create: `src/prompt_diary/progress/reporter.py`
- Create: `tests/progress/test_reporter.py`

- [ ] **Step 1: Write the failing test**

`tests/progress/test_reporter.py`:

```python
"""Tests for the reporter protocol, null reporter, and mode selection."""

from __future__ import annotations

from prompt_diary.progress.events import PrepareFinished
from prompt_diary.progress.reporter import (
    NULL_REPORTER,
    NullProgressReporter,
    select_reporter_mode,
)


def test_null_reporter_is_a_noop_context_manager() -> None:
    reporter = NullProgressReporter()
    with reporter as entered:
        entered.emit(PrepareFinished(at=0.0, projects=1, sessions=1))
    assert NULL_REPORTER.emit(PrepareFinished(at=0.0, projects=1, sessions=1)) is None


def test_select_reporter_mode() -> None:
    assert select_reporter_mode(quiet=True, isatty=True) == "quiet"
    assert select_reporter_mode(quiet=True, isatty=False) == "quiet"
    assert select_reporter_mode(quiet=False, isatty=True) == "live"
    assert select_reporter_mode(quiet=False, isatty=False) == "log"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/progress/test_reporter.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/prompt_diary/progress/reporter.py`:

```python
"""Progress reporter protocol, no-op reporter, and mode selection."""

from __future__ import annotations

from types import TracebackType
from typing import Literal, Protocol, runtime_checkable

from prompt_diary.progress.events import ProgressEvent

ReporterMode = Literal["live", "log", "quiet"]


@runtime_checkable
class ProgressReporter(Protocol):
    """Sink for progress events; also a synchronous context manager for its lifecycle."""

    def emit(self, event: ProgressEvent) -> None:
        """Receive one progress event."""
        ...

    def __enter__(self) -> "ProgressReporter":
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

    def __enter__(self) -> "NullProgressReporter":
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/progress/test_reporter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/prompt_diary/progress/reporter.py tests/progress/test_reporter.py
git commit -m "feat(progress): add reporter protocol, null reporter, mode selection"
```

---

## Task 4: Log reporter (`log.py`)

**Files:**
- Create: `src/prompt_diary/progress/log.py`
- Create: `tests/progress/test_log.py`

- [ ] **Step 1: Write the failing test**

`tests/progress/test_log.py`:

```python
"""Tests for log-line formatting and the streaming log reporter."""

from __future__ import annotations

import io

from prompt_diary.progress.events import (
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
    RunStarted,
    TaskFinished,
    TaskStarted,
    TurnAdvanced,
)
from prompt_diary.progress.log import LogReporter, format_event


def test_format_event_lines() -> None:
    assert format_event(PrepareStarted(at=0.0, sources=("codex", "claude-code"))) == (
        "prepare: starting (sources: codex, claude-code)"
    )
    assert format_event(PrepareStep(at=0.0, name="copying_transcripts", done=4, total=9)) == (
        "prepare: copying_transcripts 4/9"
    )
    assert format_event(PrepareStep(at=0.0, name="discovering", done=3, total=None)) == (
        "prepare: discovering 3"
    )
    assert format_event(PrepareFinished(at=0.0, projects=2, sessions=9)) == (
        "prepare: ready (2 projects, 9 sessions)"
    )
    assert format_event(
        RunStarted(at=0.0, label="2026-05-30", kind_totals=(("evidence_extraction", 9),))
    ) == "generate: starting 2026-05-30 (evidence_extraction: 9)"
    assert format_event(
        TaskStarted(at=0.0, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1")
    ) == "evidence_extraction: start p/S1"
    assert format_event(
        TurnAdvanced(at=0.0, task_id="t", turn_index=3, total_turns=8, turn_ref="T0003")
    ) == "evidence_extraction: p-turn T0003 (3/8)"
    assert format_event(
        TaskFinished(at=0.0, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1", status="success", error=None)
    ) == "evidence_extraction: done p/S1 [success]"
    assert format_event(
        TaskFinished(at=0.0, kind="project_synthesis", task_id="proj", project_key="p", session_ref=None, status="failed", error="boom")
    ) == "project_synthesis: done p [failed] boom"


def test_run_finished_has_no_log_line() -> None:
    from prompt_diary.progress.events import RunFinished

    assert format_event(RunFinished(at=0.0, succeeded=1, failed=0, blocked=0)) is None


def test_log_reporter_writes_lines_and_skips_none() -> None:
    from prompt_diary.progress.events import RunFinished

    stream = io.StringIO()
    with LogReporter(stream=stream) as reporter:
        reporter.emit(PrepareFinished(at=0.0, projects=1, sessions=1))
        reporter.emit(TaskStarted(at=0.0, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1"))
        reporter.emit(RunFinished(at=0.0, succeeded=1, failed=0, blocked=0))  # skipped (None)
    assert stream.getvalue() == (
        "prepare: ready (1 projects, 1 sessions)\n"
        "evidence_extraction: start p/S1\n"
    )
```

Note: `TurnAdvanced` carries only `task_id`, so its log label uses the task's kind prefix and `turn_ref`; project/session context is not on the event. Use the format shown (`"<kind-unknown>"` is avoided by labeling turns under `evidence_extraction`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/progress/test_log.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/prompt_diary/progress/log.py`:

```python
"""Append-only log-line progress reporter for non-TTY output."""

from __future__ import annotations

from types import TracebackType
from typing import TextIO

from prompt_diary.progress.events import (
    PrepareFinished,
    PrepareStarted,
    PrepareStep,
    ProgressEvent,
    RunStarted,
    TaskFinished,
    TaskStarted,
    TurnAdvanced,
)


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
            f"evidence_extraction: p-turn {event.turn_ref} "
            f"({event.turn_index}/{event.total_turns})"
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

    def __enter__(self) -> "LogReporter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
```

If `basedpyright` flags the `TurnAdvanced` branch's unused binding or the final `RunFinished` narrowing, add `assert isinstance(event, RunFinished)` before `return None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/progress/test_log.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/prompt_diary/progress/log.py tests/progress/test_log.py
git commit -m "feat(progress): add log-line reporter for non-tty output"
```

---

## Task 5: Recording test double (`tests/support/progress.py`)

**Files:**
- Create: `tests/support/progress.py`
- Create: `tests/progress/test_recording.py`

- [ ] **Step 1: Write the failing test**

`tests/progress/test_recording.py`:

```python
"""Tests for the recording reporter test double."""

from __future__ import annotations

from prompt_diary.progress.events import PrepareFinished, TaskStarted
from tests.support.progress import RecordingReporter


def test_recording_reporter_captures_events_in_order() -> None:
    with RecordingReporter() as reporter:
        reporter.emit(PrepareFinished(at=0.0, projects=1, sessions=1))
        reporter.emit(TaskStarted(at=0.0, kind="evidence_extraction", task_id="t", project_key="p", session_ref="S1"))
    assert [type(event).__name__ for event in reporter.events] == [
        "PrepareFinished",
        "TaskStarted",
    ]
    assert reporter.entered == 1
    assert reporter.exited == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/progress/test_recording.py -v`
Expected: FAIL with `ModuleNotFoundError: tests.support.progress`.

- [ ] **Step 3: Write minimal implementation**

`tests/support/progress.py`:

```python
"""Recording reporter test double for emit-site assertions."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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

    def __enter__(self) -> "RecordingReporter":
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/progress/test_recording.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/support/progress.py tests/progress/test_recording.py
git commit -m "test(progress): add recording reporter double"
```

---

## Task 6: Rich console reporter + factory (`console.py`, coverage-omitted)

**Files:**
- Create: `src/prompt_diary/progress/console.py`
- Modify: `pyproject.toml` (add `rich` dependency; add `console.py` to coverage omit)
- Create: `src/prompt_diary/progress/__init__.py`

No unit tests: this module is coverage-omitted (Rich `Live` rendering is "believed", tuned during daily use). It MUST be excluded before the gate runs or coverage drops below 100%.

- [ ] **Step 1: Add `rich` dependency and coverage omit**

In `pyproject.toml`, change the `dependencies` list to include rich:

```toml
dependencies = [
    "jinja2>=3.1",
    "mcp>=1.2",
    "rich>=13",
    "typer>=0.25.1",
]
```

And extend the coverage omit list:

```toml
[tool.coverage.run]
source = ["prompt_diary"]
omit = [
    "src/prompt_diary/integrations/codex_runner.py",
    "src/prompt_diary/progress/console.py",
]
```

Run: `uv sync` (refreshes the lock with rich as a direct dependency).

- [ ] **Step 2: Write the console reporter**

`src/prompt_diary/progress/console.py`:

```python
"""Live Rich dashboard reporter and reporter factory (coverage-omitted)."""

from __future__ import annotations

import sys
import threading
import time
from types import TracebackType
from typing import TextIO

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from prompt_diary.progress.events import ProgressEvent
from prompt_diary.progress.log import LogReporter
from prompt_diary.progress.reporter import (
    NULL_REPORTER,
    ProgressReporter,
    ReporterMode,
)
from prompt_diary.progress.state import ProgressState, reduce

_KIND_LABELS = (
    ("evidence_extraction", "evidence"),
    ("project_synthesis", "project"),
    ("daily_synthesis", "daily"),
)


class LiveConsoleReporter:
    """Render progress state as an in-place Rich dashboard."""

    def __init__(self, *, console: Console) -> None:
        self._console = console
        self._state = ProgressState()
        self._lock = threading.Lock()
        self._spinner = Spinner("dots")
        self._live = Live(
            self._render(),
            console=console,
            auto_refresh=True,
            refresh_per_second=12,
            transient=False,
        )

    def emit(self, event: ProgressEvent) -> None:
        with self._lock:
            self._state = reduce(self._state, event)
            self._live.update(self._render())

    def __enter__(self) -> "LiveConsoleReporter":
        self._live.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._live.update(self._render())
        self._live.__exit__(exc_type, exc, traceback)

    def _render(self) -> RenderableType:
        state = self._state
        rows: list[RenderableType] = []
        if state.prepare_sources or state.prepare_steps or state.prepare_done:
            rows.append(self._render_prepare(state))
        if state.kind_totals:
            rows.append(self._render_generate(state))
        return Group(*rows) if rows else Text("")

    def _render_prepare(self, state: ProgressState) -> RenderableType:
        table = Table.grid(padding=(0, 1))
        table.add_row(Text("prepare", style="bold"))
        for name, (done, total) in state.prepare_steps.items():
            counter = f"{done}/{total}" if total is not None else str(done)
            table.add_row(Text(f"  {name}"), Text(counter))
        if state.prepare_done:
            table.add_row(
                Text(
                    f"  ready ({state.prepare_projects} projects, "
                    f"{state.prepare_sessions} sessions)",
                    style="green",
                )
            )
        return table

    def _render_generate(self, state: ProgressState) -> RenderableType:
        table = Table.grid(padding=(0, 1))
        header = f"Generate · {state.label}" if state.label else "Generate"
        table.add_row(Text(header, style="bold"))
        for kind, short in _KIND_LABELS:
            total = state.kind_total(kind)
            if total == 0:
                continue
            done = state.done_count(kind)
            running = state.running_count(kind)
            table.add_row(Text(f"  {short}"), Text(f"{done}/{total}  ({running} running)"))
            for row in state.tasks.values():
                if row.kind != kind or row.status != "running":
                    continue
                if kind == "evidence_extraction" and row.total_turns:
                    detail = f"turn {row.turn_index}/{row.total_turns}"
                else:
                    detail = "working"
                label = row.session_ref or row.project_key or row.task_id
                table.add_row(Text(f"    {label}"), Text(detail, style="cyan"))
        return table


def build_reporter(
    mode: ReporterMode,
    *,
    stream: TextIO | None = None,
) -> ProgressReporter:
    """Construct the reporter for a selected mode."""
    out = stream if stream is not None else sys.stderr
    if mode == "quiet":
        return NULL_REPORTER
    if mode == "log":
        return LogReporter(stream=out)
    return LiveConsoleReporter(console=Console(file=out, stderr=True))


def _now() -> float:
    return time.monotonic()
```

Note: `_now` is provided for emit sites that import it from a single place if convenient; emit sites may also call `time.monotonic()` directly. Keep whichever the implementer wires in Tasks 7–11 consistent. The dashboard renders to `stderr` so report output paths printed to `stdout` stay clean and pipeable.

- [ ] **Step 3: Write package exports**

`src/prompt_diary/progress/__init__.py`:

```python
"""Progress reporting: events, state, and reporters for prepare and generate."""

from __future__ import annotations

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
from prompt_diary.progress.reporter import (
    NULL_REPORTER,
    NullProgressReporter,
    ProgressReporter,
    ReporterMode,
    select_reporter_mode,
)

__all__ = [
    "NULL_REPORTER",
    "NullProgressReporter",
    "PrepareFinished",
    "PrepareStarted",
    "PrepareStep",
    "ProgressEvent",
    "ProgressReporter",
    "ReporterMode",
    "RunFinished",
    "RunStarted",
    "TaskFinished",
    "TaskStarted",
    "TurnAdvanced",
    "select_reporter_mode",
]
```

- [ ] **Step 4: Run the full gate**

Run: `uv run coverage run -m pytest && uv run coverage report && uv run basedpyright && uv run ruff check && uv run ruff format --check`
Expected: PASS, 100% coverage (console.py omitted), types and lint clean.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/prompt_diary/progress/console.py src/prompt_diary/progress/__init__.py
git commit -m "feat(progress): add rich dashboard reporter, factory, and package exports"
```

---

## Task 7: Instrument `prepare_workspace`

**Files:**
- Modify: `src/prompt_diary/prepare/workspace.py`
- Test: `tests/prepare/test_progress.py` (create)

The reporter is threaded into `prepare_workspace`, then into `_write_prepared_workspace` → `_copy_project_sessions` for the per-session copy counter. Discovery/parsing happens inside `_selected_sessions`; its total is unknown until done, so it emits a single completion step (`discovering` with the final count, `total=None`), then `assigning_projects`, then `copying_transcripts done/total` per session, then `PrepareFinished`.

- [ ] **Step 1: Write the failing test**

`tests/prepare/test_progress.py`:

```python
"""Prepare emits progress events for each stage."""

from __future__ import annotations

from pathlib import Path

from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target
from tests.support.progress import RecordingReporter


def test_prepare_emits_started_steps_and_finished(tmp_path: Path) -> None:
    target = resolve_report_target(date="2026-05-30", today=False, timezone_name="UTC")
    reporter = RecordingReporter()
    prepare_workspace(
        target,
        reports_root=tmp_path / ".reports",
        source_specs=(),
        reporter=reporter,
    )
    names = [type(event).__name__ for event in reporter.events]
    assert names[0] == "PrepareStarted"
    assert names[-1] == "PrepareFinished"
```

(With `source_specs=()` there are zero sessions, so the run is fast and deterministic; the assertion only checks the bookend events, which always fire.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/prepare/test_progress.py -v`
Expected: FAIL with `TypeError: prepare_workspace() got an unexpected keyword argument 'reporter'`.

- [ ] **Step 3: Write minimal implementation**

In `src/prompt_diary/prepare/workspace.py`:

Add imports near the top (after existing imports):

```python
from prompt_diary.progress.events import PrepareFinished, PrepareStarted, PrepareStep
from prompt_diary.progress.reporter import NULL_REPORTER, ProgressReporter
```

(If `ProgressReporter` is only used in annotations, place it under `TYPE_CHECKING`; `NULL_REPORTER`, `PrepareStarted` etc. are used at runtime.)

Change the `prepare_workspace` signature and body. Replace the current creation block (lines ~191–235) so it accepts and uses the reporter:

```python
def prepare_workspace(
    target: ReportTarget,
    *,
    reports_root: Path = Path(REPORTS_DIRNAME),
    source_specs: tuple[SourceSpec, ...] | None = None,
    force: bool = False,
    prepared_at: datetime | None = None,
    reporter: ProgressReporter = NULL_REPORTER,
) -> PrepareResult:
    """Prepare the deterministic report workspace for a target day."""
    workspace_path = reports_root / "work" / target.workspace_name
    audit_dir = reports_root / "private" / target.workspace_name
    audit_path = audit_dir / "audit.manifest.json"

    if workspace_path.exists() and not force:
        return _existing_prepare_result(target, workspace_path, audit_path)

    if force:
        _remove_existing_workspace(workspace_path, audit_dir)

    specs = default_source_specs() if source_specs is None else source_specs
    reporter.emit(PrepareStarted(at=time.monotonic(), sources=tuple(spec.source for spec in specs)))
    prepared_at_local = _timestamp_for_target(target, prepared_at)
    parsed_sessions = tuple(_selected_sessions(specs, target))
    reporter.emit(
        PrepareStep(at=time.monotonic(), name="discovering", done=len(parsed_sessions), total=None)
    )
    project_count = len({session.project.key for session in parsed_sessions})
    reporter.emit(
        PrepareStep(at=time.monotonic(), name="assigning_projects", done=project_count, total=None)
    )
    _write_prepared_workspace(
        target=target,
        workspace_path=workspace_path,
        audit_path=audit_path,
        source_specs=specs,
        sessions=parsed_sessions,
        prepared_at=prepared_at_local,
        reporter=reporter,
    )

    message = (
        f"Prepared workspace {workspace_path} "
        f"with {project_count} project(s) and {len(parsed_sessions)} session(s)."
    )
    reporter.emit(
        PrepareFinished(at=time.monotonic(), projects=project_count, sessions=len(parsed_sessions))
    )
    return PrepareResult(
        target=target,
        workspace_path=workspace_path,
        audit_path=audit_path,
        created=True,
        project_count=project_count,
        session_count=len(parsed_sessions),
        messages=(message,),
    )
```

Add `import time` to the top-level imports if not present.

Thread the reporter into the copy step. Update `_write_prepared_workspace` (line ~1257) to accept `reporter: ProgressReporter` and forward it to `_write_project_workspaces`, which forwards to `_copy_project_sessions`. In `_copy_project_sessions` (line ~1333), the existing loop is `for position, session in enumerate(sessions, start=1):` — emit after each copy:

```python
    total = len(sessions)
    for position, session in enumerate(sessions, start=1):
        # ... existing copy body ...
        reporter.emit(
            PrepareStep(at=time.monotonic(), name="copying_transcripts", done=position, total=total)
        )
```

If `_copy_project_sessions` is called per-project, accumulate a workspace-wide counter instead by passing a running `done`/`total` derived from all sessions; the simplest correct form is to compute `total = len(all sessions)` in `_write_prepared_workspace` and thread a shared mutable counter, or emit per-project counters. Per-project counters are acceptable for the display. Choose per-project counters to keep the change local: `total` is that project's session count.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/prepare/ -v`
Expected: PASS (new test plus existing prepare tests unchanged — the reporter defaults to `NULL_REPORTER`).

- [ ] **Step 5: Run the full gate, then commit**

```bash
uv run coverage run -m pytest && uv run coverage report && uv run basedpyright && uv run ruff check && uv run ruff format --check
git add src/prompt_diary/prepare/workspace.py tests/prepare/test_progress.py
git commit -m "feat(progress): emit prepare stage events"
```

---

## Task 8: Thread reporter through the pipeline and emit task events

**Files:**
- Modify: `src/prompt_diary/generate/pipeline.py`
- Modify: `src/prompt_diary/generate/project_synthesis/runner.py`, `daily_synthesis/runner.py`
- Test: `tests/generate/test_pipeline.py` (extend)

This task changes the `PhaseRunner.run` signature to take `reporter`. Update every implementer and call site.

- [ ] **Step 1: Write the failing test**

Add to `tests/generate/test_pipeline.py` (reuse the file's existing fakes/fixtures; this test asserts task events are emitted). If the file defines a fake phase runner, update it in Step 3 to accept `reporter`:

```python
def test_pipeline_emits_task_started_and_finished(tmp_path: Path) -> None:
    # Build a one-task plan with a trivially-succeeding phase runner that writes its output
    # artifact, run GeneratePipelineRunner with a RecordingReporter, and assert the event types.
    from tests.support.progress import RecordingReporter

    reporter = RecordingReporter()
    runner = GeneratePipelineRunner(phase_runners=phase_runners, reporter=reporter)
    asyncio.run(runner.run(workspace_path=workspace_path, plan=plan))
    kinds = [type(event).__name__ for event in reporter.events]
    assert "TaskStarted" in kinds
    assert "TaskFinished" in kinds
```

(Adapt `phase_runners`, `workspace_path`, and `plan` to the construction already used by the existing tests in this file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generate/test_pipeline.py -v`
Expected: FAIL (`GeneratePipelineRunner` has no `reporter` parameter).

- [ ] **Step 3: Write minimal implementation**

In `src/prompt_diary/generate/pipeline.py`:

Add imports:

```python
import time
from prompt_diary.progress.events import TaskFinished, TaskStarted
from prompt_diary.progress.reporter import NULL_REPORTER, ProgressReporter
```

Change the `PhaseRunner` protocol:

```python
class PhaseRunner(Protocol):
    """Protocol implemented by phase-specific task runners."""

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter
    ) -> TaskResult:
        """Run one phase invocation and return its result."""
        ...
```

Add `reporter` to `run_generation_task`:

```python
async def run_generation_task(
    *,
    workspace_path: Path,
    task: TaskSpec,
    phase_runner: PhaseRunner,
    reporter: ProgressReporter = NULL_REPORTER,
) -> TaskResult:
    ...
        result = await phase_runner.run(
            workspace_path=workspace_path, task=task, reporter=reporter
        )
    ...
```

Add `reporter` to `run_generation_task_with_lifecycle` and forward it:

```python
async def run_generation_task_with_lifecycle(
    *,
    workspace_path: Path,
    task: TaskSpec,
    phase_runner: PhaseRunner,
    reporter: ProgressReporter = NULL_REPORTER,
) -> TaskResult:
    async with _phase_runner_lifecycle((phase_runner,)):
        return await run_generation_task(
            workspace_path=workspace_path,
            task=task,
            phase_runner=phase_runner,
            reporter=reporter,
        )
```

Add a `reporter` field to `GeneratePipelineRunner`:

```python
@dataclass(frozen=True)
class GeneratePipelineRunner:
    phase_runners: Mapping[TaskKind, PhaseRunner]
    concurrency_limits: Mapping[TaskKind, int] = field(
        default_factory=lambda: DEFAULT_CONCURRENCY_LIMITS
    )
    reporter: ProgressReporter = NULL_REPORTER
```

Emit `TaskStarted`/`TaskFinished` in `_run_limited` (so "running" reflects the concurrency gate):

```python
    async def _run_limited(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        semaphore: asyncio.Semaphore,
    ) -> TaskResult:
        async with semaphore:
            self.reporter.emit(
                TaskStarted(
                    at=time.monotonic(),
                    kind=task.kind,
                    task_id=task.task_id,
                    project_key=task.project_key,
                    session_ref=task.session_ref,
                )
            )
            result = await run_generation_task(
                workspace_path=workspace_path,
                task=task,
                phase_runner=self.phase_runners[task.kind],
                reporter=self.reporter,
            )
            self.reporter.emit(
                TaskFinished(
                    at=time.monotonic(),
                    kind=task.kind,
                    task_id=task.task_id,
                    project_key=task.project_key,
                    session_ref=task.session_ref,
                    status=result.status,
                    error=result.errors[0] if result.errors else None,
                )
            )
            return result
```

Emit blocked-task `TaskFinished` in `_block_tasks_with_failed_dependencies`, right after building the blocked `result`:

```python
                self.reporter.emit(
                    TaskFinished(
                        at=time.monotonic(),
                        kind=task.kind,
                        task_id=task.task_id,
                        project_key=task.project_key,
                        session_ref=task.session_ref,
                        status="blocked",
                        error=result.errors[0] if result.errors else None,
                    )
                )
```

Update the two placeholder runners to accept `reporter` (and ignore it). In `project_synthesis/runner.py` and `daily_synthesis/runner.py`:

```python
    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter
    ) -> TaskResult:
        del workspace_path, task, reporter
        raise PromptDiaryError(_not_implemented_message())
```

Add the import to each placeholder (under `TYPE_CHECKING` since it is annotation-only):

```python
if TYPE_CHECKING:
    ...
    from prompt_diary.progress.reporter import ProgressReporter
```

**Coverage note:** the new `blocked` emit in `_block_tasks_with_failed_dependencies` only runs when a
task is blocked by a failed dependency. `tests/generate/test_pipeline.py` already exercises a blocked
task (dependency failure) — confirm it does and that it runs with a reporter present (default
`NULL_REPORTER` still executes the emit line). If no blocked-task test exists, add one that fails an
evidence task and asserts the dependent `project_synthesis` task emits a `TaskFinished` with
`status="blocked"`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/generate/test_pipeline.py -v`
Expected: PASS. (Other suites may now fail to compile against the new signature; they are fixed in Tasks 9–10.)

- [ ] **Step 5: Commit**

```bash
git add src/prompt_diary/generate/pipeline.py src/prompt_diary/generate/project_synthesis/runner.py src/prompt_diary/generate/daily_synthesis/runner.py tests/generate/test_pipeline.py
git commit -m "feat(progress): emit pipeline task events and thread reporter to phase runners"
```

---

## Task 9: Emit `TurnAdvanced` from the evidence runner

**Files:**
- Modify: `src/prompt_diary/generate/evidence_extraction/runner.py`
- Test: `tests/generate/evidence_extraction/test_runner.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/generate/evidence_extraction/test_runner.py` (reuse the existing `EvidenceWritingAgentSessionFactory` setup and a prepared two-turn workspace fixture used elsewhere in this file):

```python
def test_runner_emits_turn_advanced_per_committed_turn(...) -> None:
    from tests.support.progress import RecordingReporter
    from prompt_diary.progress.events import TurnAdvanced

    reporter = RecordingReporter()
    runner = EvidenceExtractionRunner(agent_factory=factory)
    asyncio.run(runner.run(workspace_path=workspace_path, task=task, reporter=reporter))
    turns = [event for event in reporter.events if isinstance(event, TurnAdvanced)]
    assert [(event.turn_index, event.total_turns) for event in turns] == [(1, 2), (2, 2)]
    assert [event.turn_ref for event in turns] == ["T0001", "T0002"]
```

(Use the same fixture/factory the other tests in this file already construct; pass `reporter=reporter` into `run`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generate/evidence_extraction/test_runner.py -v`
Expected: FAIL (`run()` has no `reporter` parameter).

- [ ] **Step 3: Write minimal implementation**

In `src/prompt_diary/generate/evidence_extraction/runner.py`:

Add imports:

```python
import time
from prompt_diary.progress.events import TurnAdvanced
from prompt_diary.progress.reporter import NULL_REPORTER, ProgressReporter
```

Change `run` to accept `reporter` and emit one `TurnAdvanced` after each committed turn:

```python
    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        """Run one session evidence extraction task."""
        project_key, session_ref = _require_scope(task)
        inputs = build_session_extraction_inputs(
            workspace_path=workspace_path,
            project_key=project_key,
            session_ref=session_ref,
        )
        card_path = workspace_path / evidence_card_artifact(project_key, session_ref).path
        if card_path.exists():
            card_path.unlink()

        if not inputs.turns:
            _write_empty_card(card_path, project_key, session_ref)
            return TaskResult(task_id=task.task_id, status="success")

        runner = await self.agent_factory.runner(
            AgentConfig(
                working_directory=workspace_path,
                approval_mode="auto_review",
                sandbox="workspace-write",
            )
        )
        total_turns = len(inputs.turns)
        previous_result_json: str | None = None
        for index, turn in enumerate(inputs.turns):
            await runner.turn(_prompt_for_turn(inputs, turn, index, previous_result_json))
            if turn.turn_ref not in _committed_turn_refs(card_path):
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    errors=(_uncommitted_turn_message(session_ref, turn.turn_ref),),
                )
            reporter.emit(
                TurnAdvanced(
                    at=time.monotonic(),
                    task_id=task.task_id,
                    turn_index=index + 1,
                    total_turns=total_turns,
                    turn_ref=turn.turn_ref,
                )
            )
            previous_result_json = _committed_result_json(project_key, session_ref, turn.turn_ref)
        return TaskResult(task_id=task.task_id, status="success")
```

`ProgressReporter` is used in the signature at runtime via the default `NULL_REPORTER`, so import it normally (not under `TYPE_CHECKING`).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/generate/evidence_extraction/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/prompt_diary/generate/evidence_extraction/runner.py tests/generate/evidence_extraction/test_runner.py
git commit -m "feat(progress): emit per-turn progress from the evidence runner"
```

---

## Task 10: Thread reporter through the workflow; emit run events

**Files:**
- Modify: `src/prompt_diary/generate/workflow.py`
- Test: `tests/generate/test_workflow.py` (update existing + add an emit test)

- [ ] **Step 1: Write/adjust the failing test**

Existing `test_workflow.py` constructs `GenerateWorkspaceWorkflow` and calls `run_pipeline`/`run_phase`. Add a `reporter` argument and assert run events. Add:

```python
def test_run_pipeline_emits_run_started_and_finished(...) -> None:
    from tests.support.progress import RecordingReporter

    reporter = RecordingReporter()
    workflow.run_pipeline(workspace_path=workspace_path, reporter=reporter)
    kinds = [type(event).__name__ for event in reporter.events]
    assert kinds[0] == "RunStarted"
    assert kinds[-1] == "RunFinished"
```

(Build `workflow` with the fake builders the file already uses.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generate/test_workflow.py -v`
Expected: FAIL (`run_pipeline` has no `reporter` parameter).

- [ ] **Step 3: Write minimal implementation**

In `src/prompt_diary/generate/workflow.py`:

Add imports:

```python
import time
from prompt_diary.generate.pipeline import GeneratePipelineRunner  # already imported
from prompt_diary.progress.events import RunFinished, RunStarted
from prompt_diary.progress.reporter import NULL_REPORTER, ProgressReporter
```

Add a helper to compute kind totals from a plan:

```python
def _kind_totals(plan: GenerationPlan) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for task in plan.tasks:
        counts[task.kind] = counts.get(task.kind, 0) + 1
    return tuple(sorted(counts.items()))
```

Change `run_pipeline` to accept and thread `reporter`, emitting run bookends:

```python
    def run_pipeline(
        self,
        *,
        workspace_path: Path,
        messages: tuple[str, ...] = (),
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> GeneratePipelineWorkflowResult:
        """Run the full generation pipeline from a prepared workspace."""
        _require_workspace(workspace_path)
        factory = self.build_agent_factory(workspace_path)
        phase_runners = self.build_phase_runners(factory)
        plan = build_generation_plan(workspace_path)
        reporter.emit(
            RunStarted(
                at=time.monotonic(),
                label=workspace_path.name,
                kind_totals=_kind_totals(plan),
            )
        )
        pipeline_result = asyncio.run(
            self._run_plan(
                workspace_path=workspace_path,
                plan=plan,
                factory=factory,
                phase_runners=phase_runners,
                reporter=reporter,
            )
        )
        reporter.emit(_run_finished(pipeline_result))
        if not pipeline_result.ok:
            raise PromptDiaryError(_pipeline_failed_message(pipeline_result))
        ...
```

Add the `_run_finished` helper:

```python
def _run_finished(result: PipelineRunResult) -> RunFinished:
    succeeded = sum(1 for item in result.results if item.status == "success")
    failed = sum(1 for item in result.results if item.status == "failed")
    blocked = sum(1 for item in result.results if item.status == "blocked")
    return RunFinished(at=time.monotonic(), succeeded=succeeded, failed=failed, blocked=blocked)
```

Pass `reporter` into the pipeline runner in `_run_plan`:

```python
    async def _run_plan(
        self,
        *,
        workspace_path: Path,
        plan: GenerationPlan,
        factory: AgentSessionFactory,
        phase_runners: Mapping[TaskKind, PhaseRunner],
        reporter: ProgressReporter,
    ) -> PipelineRunResult:
        runner = GeneratePipelineRunner(phase_runners=phase_runners, reporter=reporter)
        async with factory:
            return await runner.run(workspace_path=workspace_path, plan=plan)
```

Change `run_phase` to accept `reporter` and forward it:

```python
    def run_phase(
        self,
        *,
        workspace_path: Path,
        phase: PhaseName,
        project_key: str | None = None,
        session_ref: str | None = None,
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> GeneratePhaseWorkflowResult:
        ...
        task_result = asyncio.run(
            self._run_task(
                workspace_path=workspace_path,
                task=task,
                factory=factory,
                phase_runners=phase_runners,
                reporter=reporter,
            )
        )
        ...
```

And `_run_task`:

```python
    async def _run_task(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        factory: AgentSessionFactory,
        phase_runners: Mapping[TaskKind, PhaseRunner],
        reporter: ProgressReporter,
    ) -> TaskResult:
        phase_runner = phase_runners[task.kind]
        async with factory:
            return await run_generation_task_with_lifecycle(
                workspace_path=workspace_path,
                task=task,
                phase_runner=phase_runner,
                reporter=reporter,
            )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/generate/ -v`
Expected: PASS.

- [ ] **Step 5: Run the full gate, then commit**

```bash
uv run coverage run -m pytest && uv run coverage report && uv run basedpyright && uv run ruff check && uv run ruff format --check
git add src/prompt_diary/generate/workflow.py tests/generate/test_workflow.py
git commit -m "feat(progress): emit run bookends and thread reporter through the workflow"
```

---

## Task 11: CLI wiring — `--quiet`, reporter construction, final summary

**Files:**
- Modify: `src/prompt_diary/cmds/common.py`
- Modify: `src/prompt_diary/cmds/prepare.py`, `src/prompt_diary/cmds/generate.py`
- Test: `tests/cmds/test_generate.py`, `tests/cmds/test_prepare.py` (create if absent), `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/cmds/test_prepare.py` (or extend existing): assert `--quiet` is accepted and the final summary still prints. Use Typer's `CliRunner`:

```python
"""prepare command progress wiring."""

from __future__ import annotations

from typer.testing import CliRunner

from prompt_diary.cli import app


def test_prepare_accepts_quiet_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROMPT_DIARY_CODEX_SESSIONS", "")
    monkeypatch.setenv("PROMPT_DIARY_CLAUDE_PROJECTS", "")
    result = CliRunner().invoke(app, ["prepare", "--date", "2026-05-30", "--timezone", "UTC", "--quiet"])
    assert result.exit_code == 0
    assert "Prepared workspace" in result.stdout
```

(Empty source env vars yield zero sources, so prepare runs without touching real session files.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmds/test_prepare.py -v`
Expected: FAIL with a Typer "no such option: --quiet" error (exit code 2).

- [ ] **Step 3: Write minimal implementation**

In `src/prompt_diary/cmds/common.py`, add the option type and a reporter builder:

```python
import sys
from prompt_diary.progress.console import build_reporter
from prompt_diary.progress.reporter import ProgressReporter, select_reporter_mode

QuietOption = Annotated[bool, typer.Option(help="Suppress progress; print only the final summary.")]


def build_cli_reporter(*, quiet: bool) -> ProgressReporter:
    """Build the progress reporter for a CLI invocation."""
    mode = select_reporter_mode(quiet=quiet, isatty=sys.stderr.isatty())
    return build_reporter(mode)
```

(Place `build_reporter` import at module top; it pulls in Rich, which is now a dependency. `build_cli_reporter` is covered by the CLI tests below. `build_reporter` itself lives in the coverage-omitted `console.py`.)

In `src/prompt_diary/cmds/prepare.py`, add `--quiet` and wrap the call:

```python
def prepare(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    force: ForceOption = False,
    quiet: QuietOption = False,
) -> None:
    """Prepare a prompt diary workspace."""
    try:
        target = resolve_report_target(date=date, today=today, timezone_name=timezone)
        with build_cli_reporter(quiet=quiet) as reporter:
            result = prepare_workspace(target, force=force, reporter=reporter)
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)
```

In `src/prompt_diary/cmds/generate.py`, add `--quiet` to `generate`, `generate_evidence`, `generate_project`, `generate_daily`, thread it through `workspace_for_generate_target`/`_run_phase_command`, and pass a reporter into `run_pipeline`/`run_phase`. For the full pipeline:

```python
def generate(
    ctx: typer.Context,
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    quiet: QuietOption = False,
) -> None:
    """Run the full generation pipeline."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        with build_cli_reporter(quiet=quiet) as reporter:
            workspace_path, messages = workspace_for_generate_target(
                date=date, today=today, timezone_name=timezone, reporter=reporter
            )
            workflow = build_generation_workflow()
            result = workflow.run_pipeline(
                workspace_path=workspace_path, messages=messages, reporter=reporter
            )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)
```

Add `reporter: ProgressReporter = NULL_REPORTER` to `workspace_for_generate_target` and pass it into its internal `prepare_workspace(...)` call. Add `quiet` + reporter to `_run_phase_command` and pass the reporter into `workflow.run_phase(...)`. Each phase subcommand (`generate_evidence`, `generate_project`, `generate_daily`) gains `quiet: QuietOption = False` and forwards it to `_run_phase_command`.

`_run_phase_command`:

```python
def _run_phase_command(
    *,
    phase: PhaseName,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    project_key: str | None = None,
    session_ref: str | None = None,
    quiet: bool = False,
) -> None:
    try:
        workspace_path = workspace_for_existing_target(
            date=date, today=today, timezone_name=timezone_name
        )
        workflow = build_generation_workflow()
        with build_cli_reporter(quiet=quiet) as reporter:
            result = workflow.run_phase(
                workspace_path=workspace_path,
                phase=phase,
                project_key=project_key,
                session_ref=session_ref,
                reporter=reporter,
            )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    echo_messages(result.messages)
```

Import `build_cli_reporter` and (for `workspace_for_generate_target`) `NULL_REPORTER`/`ProgressReporter` in `generate.py`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cmds/ tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full gate, then commit**

```bash
uv run coverage run -m pytest && uv run coverage report && uv run basedpyright && uv run ruff check && uv run ruff format --check
git add src/prompt_diary/cmds/ tests/cmds/ tests/test_cli.py
git commit -m "feat(progress): wire reporter and --quiet into prepare and generate commands"
```

---

## Task 12: Documentation

**Files:**
- Create: `docs/src/dev/progress-reporting.md`
- Modify: `docs/src/dev/index.md`, `docs/src/dev/generation-pipeline.md`, `docs/src/product.md`, `README.md` / `docs/src/dev/guide.md`

- [ ] **Step 1: Write the dev page**

`docs/src/dev/progress-reporting.md`, following the existing dev-doc style (one-line "This page covers… for developers…" intro, then `## Role`, the seam, emit sites, mode selection, `## Coverage`):

```markdown
# Progress Reporting

This page covers the progress reporting seam (`prompt_diary/progress/`) that surfaces what
`prepare` and `generate` are doing. It is for developers changing the CLI feedback or adding
progress to a new phase.

## Role

The pipeline emits structured **progress events** into a narrow `ProgressReporter`; the reporter
folds them through a pure reducer into a `ProgressState` and renders it. The pipeline depends only
on the reporter protocol, never on Rich.

## Seam: events -> state -> reporter

- `events.py` — frozen event types (`PrepareStep`, `TaskStarted`, `TurnAdvanced`, ...). Each carries
  only deterministic identifiers and counts; never transcript or agent text.
- `state.py` — `reduce(state, event) -> ProgressState`, a pure fold (counts, per-task rows,
  `turn x/y`, finished-task elapsed). All display logic lives here and is unit-tested.
- `reporter.py` — the `ProgressReporter` protocol, `NullProgressReporter` (default), and
  `select_reporter_mode`.
- `log.py` — `LogReporter` for non-TTY/CI: one tested log line per event.
- `console.py` — `LiveConsoleReporter` (Rich `Live` dashboard) and `build_reporter`.

## Emit sites

- `prepare/workspace.py` — prepare stage steps.
- `generate/pipeline.py` — `TaskStarted`/`TaskFinished` (incl. `blocked`), threading the reporter to
  each phase runner's `run(..., reporter=...)`.
- `generate/evidence_extraction/runner.py` — `TurnAdvanced` per committed turn.
- `generate/workflow.py` — `RunStarted`/`RunFinished`.

A phase runner that wants per-item progress emits via the `reporter` argument it receives. Runners
that do not still accept and ignore it.

## Mode selection

`select_reporter_mode(quiet, isatty)` chooses `quiet` / `live` / `log`. The CLI builds the reporter
in `cmds/common.py::build_cli_reporter`; `--quiet` forces summary-only. The dashboard renders to
stderr so report paths on stdout stay pipeable.

## Coverage

Everything except `progress/console.py` is unit-tested (the reducer and the log path by submitting
the same events the pipeline emits; emit sites via a `RecordingReporter`). `progress/console.py`
(Rich `Live`) is coverage-omitted in `pyproject.toml`, like `integrations/codex_runner.py`, and is
tuned during daily use.
```

- [ ] **Step 2: Register it in the dev index**

In `docs/src/dev/index.md`, add to the bullet list:

```markdown
- [Progress Reporting](./progress-reporting.md) — the events → state → reporter seam that surfaces
  prepare and generate progress in the terminal.
```

- [ ] **Step 3: Add a pointer in generation-pipeline.md**

In `docs/src/dev/generation-pipeline.md`, add a short paragraph (no duplication of detail):

```markdown
## Progress

The scheduler emits `TaskStarted`/`TaskFinished` events and threads a `ProgressReporter` into each
phase runner's `run(...)`; the evidence runner emits `TurnAdvanced` per turn. See
[Progress Reporting](./progress-reporting.md).
```

- [ ] **Step 4: Update the product CLI surface and the guide**

In `docs/src/product.md`, add `--quiet` to the CLI surface block:

```text
prompt-diary prepare   [--date YYYY-MM-DD | --today] [--timezone Area/City] [--force] [--quiet]
prompt-diary generate  [--date YYYY-MM-DD | --today] [--timezone Area/City] [--quiet]
```

In `docs/src/dev/guide.md` (and `README.md` if it documents user flags), note: progress shows a live
dashboard on a TTY and append-only log lines when output is piped/redirected; `--quiet` prints only
the final summary.

- [ ] **Step 5: Commit**

```bash
git add docs/ README.md
git commit -m "docs: document the progress reporting seam and --quiet"
```

---

## Final verification

- [ ] Run the full gate once more: `uv run coverage run -m pytest && uv run coverage report && uv run basedpyright && uv run ruff check && uv run ruff format --check`. Expected: all pass, 100% coverage.
- [ ] Manual smoke (live dashboard): `uv run report prepare --date <a-day-with-sessions> --today` and watch the prepare steps; then `uv run report generate evidence --today --project-key <k> --session-ref <r>` and watch `turn x/y`.
- [ ] Manual smoke (log fallback): pipe it — `uv run report generate evidence --today --project-key <k> --session-ref <r> | cat` — and confirm plain log lines, no ANSI garbage.
- [ ] Manual smoke (quiet): add `--quiet` and confirm only the final summary prints.
```
