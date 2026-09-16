from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.models import JsonObject, ReportTarget, SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from typing import IO


@pytest.mark.parametrize(
    "metadata",
    [
        {"source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}},
        {"source": {"subagent": {"thread_spawn": {"depth": 1}}}},
        {"source": {"subagent": {"other": "guardian"}}},
        {"source": {"subagent": "review"}},
        {"source": {"subagent": "compact"}},
        {"source": {"subagent": "future-variant"}},
        {"source": {"subagent": {}}},
        {"thread_source": "subagent"},
        {"originator": "Claude Code"},
    ],
    ids=[
        "spawn",
        "spawn-no-parent",
        "guardian",
        "review",
        "compact",
        "future",
        "empty",
        "legacy",
        "claude",
    ],
)
def test_codex_subagents_are_excluded_without_reading_their_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: JsonObject
) -> None:
    source_root = tmp_path / "codex"
    source_root.mkdir()
    child = source_root / "child.jsonl"
    header = {"type": "session_meta", "payload": {"id": "child", **metadata}}
    child.write_text(json.dumps(header) + '\n{"body-must-not-be-read":true}\n', encoding="utf-8")
    _prevent_child_body_reads(monkeypatch, child)
    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    assert result.session_count == 0
    assert not list((result.workspace_path / "projects").iterdir())


@pytest.mark.parametrize("source", [None, "cli", "exec", {"other": "root"}, {"subagent": None}])
def test_codex_root_source_variants_remain_reportable(tmp_path: Path, source: Any) -> None:
    source_root = tmp_path / "codex"
    source_root.mkdir()
    records = [
        {"type": "session_meta", "payload": {"id": "root", "source": source}},
        {
            "type": "event_msg",
            "timestamp": "2026-05-12T01:00:00Z",
            "payload": {"type": "user_message", "message": "Human task"},
        },
    ]
    (source_root / "root.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    assert result.session_count == 1


def test_full_parser_still_excludes_child_when_probe_metadata_is_malformed(tmp_path: Path) -> None:
    source_root = tmp_path / "codex"
    source_root.mkdir()
    records = [
        {
            "type": "session_meta",
            "payload": {
                "id": "child",
                "role": 42,
                "source": {"subagent": {"other": "guardian"}},
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-05-12T01:00:00Z",
            "payload": {"type": "user_message", "message": "Agent-generated task"},
        },
    ]
    (source_root / "child.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    assert result.session_count == 0


@pytest.mark.parametrize("single_file_source", [False, True])
def test_claude_child_path_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, single_file_source: bool
) -> None:
    source_root = tmp_path / "claude"
    child = source_root / "project" / "parent" / "subagents" / "agent-child.jsonl"
    child.parent.mkdir(parents=True)
    child.write_text("must not be read\n", encoding="utf-8")
    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        assert path != child
        return cast("IO[Any]", original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", guarded_open)
    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / "reports",
        source_specs=(
            SourceSpec(source="claude-code", root=child if single_file_source else source_root),
        ),
    )

    assert result.session_count == 0


def test_claude_sidechain_metadata_stops_body_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "claude"
    source_root.mkdir()
    child = source_root / "agent-child.jsonl"
    child.write_text('{"isSidechain":true}\n{"body-must-not-be-read":true}\n', encoding="utf-8")
    _prevent_child_body_reads(monkeypatch, child)
    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source="claude-code", root=source_root),),
    )

    assert result.session_count == 0


@pytest.mark.parametrize("schema", [None, 1, 2, 4])
def test_unsupported_workspace_requires_force_refresh(tmp_path: Path, schema: int | None) -> None:
    reports_root = tmp_path / "reports"
    result = prepare_workspace(_target(), reports_root=reports_root, source_specs=())
    metadata_path = result.workspace_path / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = schema
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    old_child = result.workspace_path / "projects" / "old" / "sessions" / "child.jsonl"
    old_child.parent.mkdir(parents=True)
    old_child.write_text("old child transcript", encoding="utf-8")

    with pytest.raises(PromptDiaryError, match=r"prepare .*--force"):
        prepare_workspace(_target(), reports_root=reports_root, source_specs=())
    with pytest.raises(PromptDiaryError, match="Unsupported prepared workspace schema"):
        load_prepared_workspace(result.workspace_path)
    assert old_child.exists()

    refreshed = prepare_workspace(_target(), reports_root=reports_root, source_specs=(), force=True)

    assert refreshed.created
    assert not old_child.exists()
    assert load_prepared_workspace(refreshed.workspace_path).projects == ()


class _HeaderOnlyFile(io.BytesIO):
    def __next__(self) -> bytes:
        assert self.tell() == 0, "Subagent body was read after its identifying header"
        return super().__next__()

    def read(self, size: int | None = -1) -> bytes:
        del size
        pytest.fail("Subagent transcript was read in full")


def _prevent_child_body_reads(monkeypatch: pytest.MonkeyPatch, child: Path) -> None:
    child_bytes = child.read_bytes()
    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path == child:
            return _HeaderOnlyFile(child_bytes)
        return cast("IO[Any]", original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", guarded_open)


def _target() -> ReportTarget:
    return resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="UTC",
        now=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
