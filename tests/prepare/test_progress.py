"""Prepare emits progress events for each stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.models import SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.progress.events import PrepareStep
from prompt_diary.targeting.resolve import resolve_report_target
from tests.support.progress import RecordingReporter

if TYPE_CHECKING:
    from pathlib import Path


def test_prepare_emits_full_event_sequence_with_zero_sessions(tmp_path: Path) -> None:
    target = resolve_report_target(date="2026-05-30", today=False, timezone_name="UTC")
    reporter = RecordingReporter()
    prepare_workspace(
        target,
        reports_root=tmp_path / ".reports",
        source_specs=(),
        reporter=reporter,
    )
    names = [type(event).__name__ for event in reporter.events]
    assert names == ["PrepareStarted", "PrepareStep", "PrepareStep", "PrepareFinished"]
    steps = [event for event in reporter.events if isinstance(event, PrepareStep)]
    assert [(step.name, step.done, step.total) for step in steps] == [
        ("discovering", 0, None),
        ("assigning_projects", 0, None),
    ]


def test_prepare_emits_scanning_progress_during_the_scan(tmp_path: Path) -> None:
    source_root = tmp_path / "codex"
    source_root.mkdir()
    for index in range(3):
        (source_root / f"s{index}.jsonl").write_text("{}\n", encoding="utf-8")
    target = resolve_report_target(date="2026-05-30", today=False, timezone_name="UTC")
    reporter = RecordingReporter()
    prepare_workspace(
        target,
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
        reporter=reporter,
    )
    scanning = [
        event
        for event in reporter.events
        if isinstance(event, PrepareStep) and event.name == "scanning_sessions"
    ]
    assert scanning, "expected per-file scanning progress during the session scan"
    assert (scanning[-1].done, scanning[-1].total) == (3, 3)


def test_prepare_emits_source_scoped_scan_and_discovery_counts(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    claude_root = tmp_path / "claude"
    codex_root.mkdir()
    claude_root.mkdir()
    (codex_root / "target.jsonl").write_text(
        (
            '{"type":"session_meta","timestamp":"2026-05-30T00:00:00Z",'
            '"payload":{"id":"codex-target","cwd":"/tmp/project"}}\n'
            '{"type":"response_item","timestamp":"2026-05-30T00:01:00Z",'
            '"payload":{"role":"user","type":"message",'
            '"content":[{"text":"Work.","type":"input_text"}]}}\n'
        ),
        encoding="utf-8",
    )
    (claude_root / "target.jsonl").write_text(
        (
            '{"type":"user","timestamp":"2026-05-30T00:02:00Z",'
            '"message":{"role":"user","content":"Work."},"cwd":"/tmp/project"}\n'
        ),
        encoding="utf-8",
    )
    (claude_root / "outside.jsonl").write_text(
        (
            '{"type":"user","timestamp":"2026-05-29T00:02:00Z",'
            '"message":{"role":"user","content":"Old work."},"cwd":"/tmp/project"}\n'
        ),
        encoding="utf-8",
    )
    target = resolve_report_target(date="2026-05-30", today=False, timezone_name="UTC")
    reporter = RecordingReporter()

    prepare_workspace(
        target,
        reports_root=tmp_path / ".reports",
        source_specs=(
            SourceSpec(source="codex", root=codex_root),
            SourceSpec(source="claude-code", root=claude_root),
        ),
        reporter=reporter,
    )

    scanning = [
        event
        for event in reporter.events
        if isinstance(event, PrepareStep) and event.name == "scanning_sessions"
    ]
    discovering = [
        event
        for event in reporter.events
        if isinstance(event, PrepareStep) and event.name == "discovering"
    ]
    copying = [
        event
        for event in reporter.events
        if isinstance(event, PrepareStep) and event.name == "copying_transcripts"
    ]
    assert [(event.scope, event.done, event.total) for event in scanning] == [
        (f"codex {codex_root}", 1, 1),
        (f"claude-code {claude_root}", 1, 2),
        (f"claude-code {claude_root}", 2, 2),
    ]
    assert [(event.scope, event.done, event.total) for event in discovering] == [
        (f"codex {codex_root}", 1, None),
        (f"claude-code {claude_root}", 1, None),
        (None, 2, None),
    ]
    assert copying == []
