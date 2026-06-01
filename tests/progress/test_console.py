"""Tests for the live progress console renderables."""

from __future__ import annotations

import io

from rich.console import Console

from prompt_diary.progress.console import LiveConsoleReporter
from prompt_diary.progress.events import PrepareStep


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
