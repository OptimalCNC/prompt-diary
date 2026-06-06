"""Tests for the live progress console renderables."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

from rich.console import Console

from prompt_diary.progress.console import LiveConsoleReporter
from prompt_diary.progress.events import PhaseFinished, PhaseStarted, PrepareStep

if TYPE_CHECKING:
    import pytest
    from rich.live import Live


def test_prepare_render_keeps_first_seen_step_order() -> None:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=100)
    with LiveConsoleReporter(console=console) as reporter:
        reporter.emit(
            PrepareStep(
                at=0.1,
                name="scanning_sessions",
                done=12,
                total=20,
                scope="codex ~/.codex/sessions",
            )
        )
        reporter.emit(
            PrepareStep(
                at=0.2,
                name="discovering",
                done=7,
                total=None,
                scope="codex ~/.codex/sessions",
            )
        )
        reporter.emit(PrepareStep(at=0.3, name="discovering", done=16, total=None))

    output = stream.getvalue()
    assert output.index("scanning_sessions") < output.index("discovering")


def test_publish_shows_completed_phase_duration() -> None:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=100)
    with LiveConsoleReporter(console=console) as reporter:
        reporter.emit(PhaseStarted(at=1.0, phase_id="publish", label="publish"))
        reporter.emit(PhaseFinished(at=3.5, phase_id="publish", status="success"))

    output = stream.getvalue()
    assert "publish" in output
    assert "2.5s" in output


def test_live_renderable_refreshes_elapsed_without_new_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1.0
    monkeypatch.setattr("prompt_diary.progress.console.time.monotonic", lambda: now)
    reporter = LiveConsoleReporter(console=Console(file=io.StringIO(), force_terminal=False))

    reporter.emit(PhaseStarted(at=0.0, phase_id="publish", label="publish"))
    live = cast("Live", vars(reporter)["_live"])

    first_stream = io.StringIO()
    Console(file=first_stream, force_terminal=False, width=100).print(live.get_renderable())
    now = 5.0
    second_stream = io.StringIO()
    Console(file=second_stream, force_terminal=False, width=100).print(live.get_renderable())

    assert "1.0s" in first_stream.getvalue()
    assert "5.0s" in second_stream.getvalue()
