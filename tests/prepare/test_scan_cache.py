from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

import prompt_diary.prepare.workspace as preparation
from prompt_diary.models import PrepareResult, SourceName, SourceSpec
from prompt_diary.prepare.scan_cache import ProbeResult, SessionScanCache, TimestampBounds
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.progress.events import PrepareStep
from prompt_diary.targeting.resolve import resolve_report_target
from tests.support.progress import RecordingReporter

if TYPE_CHECKING:
    from typing import IO

_PREPARED_AT = datetime(2026, 5, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_cached_scan_skips_old_contents_and_preserves_workspace_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: SourceName
) -> None:
    old = _write_session(tmp_path / "source" / "old.jsonl", source, ["2026-05-10T12:00:00Z"])
    _write_session(
        tmp_path / "source" / "resumed.jsonl",
        source,
        ["2026-05-10T12:00:00Z", "2026-05-12T12:00:00Z"],
    )
    _write_session(tmp_path / "source" / "future.jsonl", source, ["2026-05-14T12:00:00Z"])
    first = _prepare(tmp_path, source=source)
    before = _workspace_bytes(first)
    _guard_reads(monkeypatch, {old, old.with_name("future.jsonl")})
    reporter = RecordingReporter()

    refreshed = _prepare(tmp_path, source=source, reporter=reporter)

    assert refreshed.session_count == 1
    assert _workspace_bytes(refreshed) == before
    scanning = [
        event
        for event in reporter.events
        if isinstance(event, PrepareStep) and event.name == "scanning_sessions"
    ]
    assert [(event.done, event.total) for event in scanning] == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_cache_works_across_dates_with_offsets_and_non_monotonic_timestamps(
    tmp_path: Path, source: SourceName
) -> None:
    for name, times in {
        "start": ["2026-05-12T00:00:00+08:00"],
        "end": ["2026-05-13T00:00:00+08:00"],
        "unordered": ["2026-05-14T00:00:00Z", "2026-05-12T12:00:00Z", "2026-05-10T12:00:00Z"],
    }.items():
        _write_session(tmp_path / "source" / f"{name}.jsonl", source, times)
    assert _prepare(tmp_path, source=source, day="2026-05-09").session_count == 0

    cached = _prepare(tmp_path, source=source, zone="Asia/Shanghai")
    before = _workspace_bytes(cached)
    assert cached.session_count == 2
    _cache_path(tmp_path).unlink()
    uncached = _prepare(tmp_path, source=source, zone="Asia/Shanghai")

    assert _workspace_bytes(uncached) == before


@pytest.mark.parametrize("change", ["append", "rewrite", "replace", "truncate"])
def test_changed_files_are_rescanned_even_with_preserved_mtime(tmp_path: Path, change: str) -> None:
    path = _write_session(tmp_path / "source" / "session.jsonl", "codex", ["2026-05-10T12:00:00Z"])
    if change == "truncate":
        with path.open("ab") as handle:
            handle.write(b"{}\n" * 10)
    original = path.stat()
    assert _prepare(tmp_path).session_count == 0
    content = _records("codex", ["2026-05-12T12:00:00Z"])
    if change == "append":
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
    elif change == "replace":
        replacement = tmp_path / "replacement"
        replacement.write_text(content, encoding="utf-8")
        replacement.replace(path)
    else:
        path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_mtime_ns == original.st_mtime_ns
    if change in ("rewrite", "replace"):
        assert path.stat().st_size == original.st_size

    assert _prepare(tmp_path).session_count == 1


def test_new_and_deleted_sources_are_reflected_and_stale_entries_are_pruned(tmp_path: Path) -> None:
    old = _write_session(tmp_path / "source" / "old.jsonl", "codex", ["2026-05-10T12:00:00Z"])
    assert _prepare(tmp_path).session_count == 0
    old.unlink()
    _write_session(tmp_path / "source" / "new.jsonl", "codex", ["2026-05-12T12:00:00Z"])

    assert _prepare(tmp_path).session_count == 1
    entries = json.loads(_cache_path(tmp_path).read_bytes())["entries"]
    assert len(entries) == 1
    assert all("old.jsonl" not in key for key in entries)


@pytest.mark.parametrize("problem", ["json", "version", "type", "naive", "reversed"])
def test_invalid_cache_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    path = _write_session(tmp_path / "source" / "old.jsonl", "codex", ["2026-05-10T12:00:00Z"])
    _prepare(tmp_path)
    cache_path = _cache_path(tmp_path)
    content = json.loads(cache_path.read_bytes())
    entry = next(iter(content["entries"].values()))
    if problem == "json":
        cache_path.write_text("{", encoding="utf-8")
    else:
        if problem == "version":
            content["version"] = 99
        elif problem == "type":
            entry["stamp"]["size"] = "unknown"
        elif problem == "naive":
            entry["timestamps"]["first"] = "2026-05-10T00:00:00"
        else:
            entry["timestamps"]["first"] = "2026-05-15T00:00:00Z"
        cache_path.write_text(json.dumps(content), encoding="utf-8")
    reads = _record_reads(monkeypatch, path)

    assert _prepare(tmp_path).session_count == 0
    assert reads
    assert json.loads(cache_path.read_bytes())["version"] == 1


def test_unwritable_cache_does_not_prevent_preparation_or_leave_temporary_files(
    tmp_path: Path,
) -> None:
    _write_session(tmp_path / "source" / "session.jsonl", "codex", ["2026-05-12T12:00:00Z"])
    _cache_path(tmp_path).mkdir(parents=True)

    assert _prepare(tmp_path).session_count == 1
    assert not list(_cache_path(tmp_path).parent.glob(".session-scan-*"))


def test_unavailable_source_still_reports_the_probe_read_error(tmp_path: Path) -> None:
    path = tmp_path / "missing.jsonl"
    cache = SessionScanCache(_cache_path(tmp_path))
    window = resolve_report_target(
        date="2026-05-12", today=False, timezone_name="UTC", now=_PREPARED_AT
    ).report_window_utc

    def read_source() -> ProbeResult:
        path.read_bytes()
        return ProbeResult(is_candidate=False)

    with pytest.raises(FileNotFoundError):
        cache.is_candidate(path, "codex", window, read_source)


def test_file_changing_during_probe_is_not_cached(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "source" / "session.jsonl", "codex", ["2026-05-10T12:00:00Z"])
    cache = SessionScanCache(_cache_path(tmp_path))
    timestamp = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
    window = resolve_report_target(
        date="2026-05-09", today=False, timezone_name="UTC", now=_PREPARED_AT
    ).report_window_utc

    def append_after_probe() -> ProbeResult:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_records("codex", ["2026-05-12T12:00:00Z"]))
        return ProbeResult(is_candidate=False, timestamps=TimestampBounds(timestamp, timestamp))

    assert not cache.is_candidate(path, "codex", window, append_after_probe)
    cache.save()
    assert not _cache_path(tmp_path).exists()

    assert _prepare(tmp_path).session_count == 1


def test_malformed_probe_records_cannot_hide_later_report_dates(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "source" / "session.jsonl", "codex", ["2026-05-12T12:00:00Z"])
    # The full parser accepts the trigger despite this unrelated malformed probe field.
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0]["payload"]["item"] = "unexpected"
    path.write_text("not JSON\n" + "".join(json.dumps(record) + "\n" for record in records))
    assert _prepare(tmp_path, day="2026-05-09").session_count == 0
    assert not _cache_path(tmp_path).exists()

    assert _prepare(tmp_path).session_count == 1


def test_out_of_window_malformed_session_does_not_build_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_session(tmp_path / "source" / "old.jsonl", "codex", ["2026-05-10T12:00:00Z"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not JSON\n")

    def unexpected_build(**_kwargs: object) -> None:
        pytest.fail("Out-of-window session should not construct turns or fingerprints")

    monkeypatch.setattr(preparation, "_build_turns", unexpected_build)

    assert _prepare(tmp_path).session_count == 0


def _prepare(
    tmp_path: Path,
    *,
    source: SourceName = "codex",
    day: str = "2026-05-12",
    zone: str = "UTC",
    reporter: RecordingReporter | None = None,
) -> PrepareResult:
    return prepare_workspace(
        resolve_report_target(date=day, today=False, timezone_name=zone, now=_PREPARED_AT),
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source=source, root=tmp_path / "source"),),
        force=True,
        prepared_at=_PREPARED_AT,
        reporter=reporter or RecordingReporter(),
    )


def _write_session(path: Path, source: SourceName, times: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_records(source, times), encoding="utf-8")
    return path


def _records(source: SourceName, times: list[str]) -> str:
    return "".join(
        json.dumps(
            {
                "type": "event_msg" if source == "codex" else "user",
                "timestamp": timestamp,
                "payload": {"type": "user_message", "message": "Do the work"},
                "message": {"role": "user", "content": "Do the work"},
                "cwd": "/cache-test-project",
            }
        )
        + "\n"
        for timestamp in times
    )


def _cache_path(tmp_path: Path) -> Path:
    return tmp_path / "reports" / "private" / "session-scan-cache.json"


def _workspace_bytes(result: PrepareResult) -> dict[Path, bytes]:
    return {
        path: path.read_bytes()
        for path in [*result.workspace_path.rglob("*"), result.audit_path]
        if path.is_file()
    }


def _guard_reads(monkeypatch: pytest.MonkeyPatch, forbidden: set[Path]) -> None:
    original = Path.open

    def guarded(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        assert path not in forbidden, "Unchanged out-of-window transcript was opened"
        return cast("IO[Any]", original(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", guarded)


def _record_reads(monkeypatch: pytest.MonkeyPatch, source: Path) -> list[Path]:
    reads: list[Path] = []
    original = Path.open

    def recording(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path == source:
            reads.append(path)
        return cast("IO[Any]", original(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", recording)
    return reads
