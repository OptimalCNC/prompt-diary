"""Live Rich dashboard reporter and reporter factory (coverage-omitted)."""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from prompt_diary.progress.log import LogReporter
from prompt_diary.progress.reporter import (
    NULL_REPORTER,
    ProgressReporter,
    ReporterMode,
)
from prompt_diary.progress.state import ProgressState, reduce

if TYPE_CHECKING:
    from types import TracebackType
    from typing import TextIO

    from rich.console import RenderableType

    from prompt_diary.progress.events import ProgressEvent

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
        # Draw synchronously on each emit (refresh=True) rather than relying on a
        # background auto-refresh thread: progress between emits is static, and a
        # fast phase can finish inside one auto-refresh tick, leaving only the final
        # frame painted. Synchronous draws make every event visible immediately.
        self._live = Live(
            self._render(),
            console=console,
            auto_refresh=False,
            transient=False,
        )

    def emit(self, event: ProgressEvent) -> None:
        with self._lock:
            self._state = reduce(self._state, event)
            self._live.update(self._render(), refresh=True)

    def __enter__(self) -> LiveConsoleReporter:
        self._live.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._live.update(self._render(), refresh=True)
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
        step_names = [
            *state.prepare_steps.keys(),
            *(name for name in state.prepare_step_scopes if name not in state.prepare_steps),
        ]
        for name in step_names:
            if name in state.prepare_steps:
                done, total = state.prepare_steps[name]
                counter = f"{done}/{total}" if total is not None else str(done)
                table.add_row(Text(f"  {name}"), Text(counter))
            else:
                table.add_row(Text(f"  {name}"), Text(""))
            for scope, (done, total) in state.prepare_step_scopes.get(name, {}).items():
                counter = f"{done}/{total}" if total is not None else str(done)
                table.add_row(Text(f"    {scope}"), Text(counter, style="cyan"))
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
