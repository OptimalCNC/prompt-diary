from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

import prompt_diary.prepare.workspace as preparation
from prompt_diary.language import GENERATED_AGENTS_MARKER
from prompt_diary.models import JsonObject, PrepareResult, ReportTarget, SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from typing import IO


@pytest.mark.parametrize("malformed_probe_field", [False, True])
def test_report_originator_excludes_session_before_body_with_unrelated_reports_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, malformed_probe_field: bool
) -> None:
    source = _session(tmp_path, originator="prompt_diary", cwd=tmp_path / "deleted-workspace")
    if malformed_probe_field:
        records = [json.loads(line) for line in source.read_text().splitlines()]
        records[0]["payload"]["role"] = 42
        source.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
    original = source.read_bytes()
    _guard_report_body(monkeypatch, source)

    result = _prepare(tmp_path, source)

    assert result.session_count == 0
    assert not list(result.workspace_path.glob("projects/*/sessions/**/*.jsonl"))
    monkeypatch.undo()
    assert source.read_bytes() == original


def test_full_parser_also_excludes_report_originator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _session(tmp_path, originator="prompt_diary", cwd=tmp_path / "deleted-workspace")

    def include_from_probe(**_arguments: object) -> bool:
        return True

    monkeypatch.setattr(preparation, "_probe_source_file", include_from_probe)
    result = _prepare(tmp_path, source)

    assert result.session_count == 0
    assert source.exists()


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_legacy_sdk_reports_require_positive_workspace_provenance_before_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: int
) -> None:
    previous_workspace = _legacy_workspace(tmp_path)
    metadata_path = previous_workspace / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["schema_version"] = schema
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    source = _session(tmp_path, originator="codex_python_sdk", cwd=previous_workspace)
    _guard_report_body(monkeypatch, source)

    result = _prepare(tmp_path, source)

    assert result.session_count == 0
    assert previous_workspace.is_dir()


@pytest.mark.parametrize(
    "originator", ["codex_python_sdk", "codex-tui", "another_sdk", "prompt_diary_other", None]
)
def test_unrelated_clients_and_sdk_sessions_are_reportable(
    tmp_path: Path, originator: str | None
) -> None:
    source = _session(tmp_path, originator=originator, cwd=tmp_path / "ordinary-project")

    assert _prepare(tmp_path, source).session_count == 1


def test_interactive_session_in_old_report_workspace_remains_reportable(tmp_path: Path) -> None:
    previous_workspace = _legacy_workspace(tmp_path)
    source = _session(tmp_path, originator="codex-tui", cwd=previous_workspace)

    assert _prepare(tmp_path, source).session_count == 1


@pytest.mark.parametrize(
    "problem",
    [
        "missing-marker",
        "wrong-marker",
        "unreadable-marker",
        "missing-metadata",
        "malformed-metadata",
        "missing-field",
        "invalid-date",
        "naive-prepared-at",
        "directory-reused-later",
        "wrong-date-folder",
        "unknown-schema",
    ],
)
def test_legacy_sdk_with_unproven_workspace_origin_remains_reportable(
    tmp_path: Path, problem: str
) -> None:
    previous_workspace = _legacy_workspace(tmp_path)
    marker_path = previous_workspace / "AGENTS.md"
    metadata_path = previous_workspace / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    if problem == "missing-marker":
        marker_path.unlink()
    elif problem == "wrong-marker":
        marker_path.write_text("User-maintained instructions", encoding="utf-8")
    elif problem == "unreadable-marker":
        marker_path.write_bytes(b"\xff")
    elif problem == "missing-metadata":
        metadata_path.unlink()
    elif problem == "malformed-metadata":
        metadata_path.write_text("not JSON", encoding="utf-8")
    else:
        if problem == "missing-field":
            del metadata["prepared_at"]
        else:
            field, value = {
                "invalid-date": ("report_date", "not-a-date"),
                "naive-prepared-at": ("prepared_at", "2026-05-12T00:00:00"),
                "directory-reused-later": ("prepared_at", "2026-05-13T00:00:00Z"),
                "wrong-date-folder": ("report_date", "2026-05-10"),
                "unknown-schema": ("schema_version", 99),
            }[problem]
            metadata[field] = value
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    source = _session(tmp_path, originator="codex_python_sdk", cwd=previous_workspace)

    assert _prepare(tmp_path, source).session_count == 1


@pytest.mark.parametrize("missing_header_field", ["cwd", "timestamp"])
def test_legacy_sdk_requires_cwd_and_authoritative_session_timestamp(
    tmp_path: Path, missing_header_field: str
) -> None:
    previous_workspace = _legacy_workspace(tmp_path)
    source = _session(tmp_path, originator="codex_python_sdk", cwd=previous_workspace)
    records = [json.loads(line) for line in source.read_text().splitlines()]
    if missing_header_field == "cwd":
        del records[0]["payload"]["cwd"]
    else:
        del records[0]["timestamp"]
    source.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    assert _prepare(tmp_path, source).session_count == 1


def test_relative_legacy_cwd_does_not_resolve_against_preparation_process(tmp_path: Path) -> None:
    source = _session(tmp_path, originator="codex_python_sdk", cwd=Path("work/2026-05-11"))

    assert _prepare(tmp_path, source).session_count == 1


def _session(tmp_path: Path, *, originator: str | None, cwd: Path) -> Path:
    source = tmp_path / "sdk-session.jsonl"
    header: JsonObject = {
        "type": "session_meta",
        "timestamp": "2026-05-12T01:00:00Z",
        "payload": {
            "id": "sdk-session",
            "source": "vscode",
            "originator": originator,
            "cwd": str(cwd),
        },
    }
    assignment: JsonObject = {
        "type": "response_item",
        "timestamp": "2026-05-12T01:00:01Z",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "## Role\n\nYou are an evidence extractor for Prompt Diary.",
                }
            ],
        },
    }
    source.write_text(json.dumps(header) + "\n" + json.dumps(assignment) + "\n", encoding="utf-8")
    return source


def _legacy_workspace(tmp_path: Path) -> Path:
    result = prepare_workspace(
        _target("2026-05-11"),
        reports_root=tmp_path / "old-reports",
        source_specs=(),
        prepared_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
    )
    (result.workspace_path / "AGENTS.md").write_text(
        f"# Prompt Diary Runtime Instructions\n\n{GENERATED_AGENTS_MARKER}\n", encoding="utf-8"
    )
    return result.workspace_path


def _target(day: str = "2026-05-12") -> ReportTarget:
    return resolve_report_target(
        date=day,
        today=False,
        timezone_name="UTC",
        now=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )


def _prepare(tmp_path: Path, source: Path) -> PrepareResult:
    return prepare_workspace(
        _target(),
        reports_root=tmp_path / "unrelated-reports",
        source_specs=(SourceSpec(source="codex", root=source),),
    )


class _HeaderOnlyFile(io.BytesIO):
    def __next__(self) -> bytes:
        assert self.tell() == 0, "Report session body was read after its identifying header"
        return super().__next__()

    def read(self, size: int | None = -1) -> bytes:
        del size
        pytest.fail("Report transcript was read in full")


def _guard_report_body(monkeypatch: pytest.MonkeyPatch, source: Path) -> None:
    source_bytes = source.read_bytes()
    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path == source:
            return _HeaderOnlyFile(source_bytes)
        return cast("IO[Any]", original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", guarded_open)
