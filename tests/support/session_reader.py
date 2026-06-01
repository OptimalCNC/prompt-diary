from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest

from prompt_diary.generate.evidence_extraction.session_compaction import compact_record_to_json
from prompt_diary.generate.evidence_extraction.session_reader import (
    FullRecord,
    ReadSessionLinesCompactResult,
    ReadSessionLinesFullResult,
    ReadSessionLinesInvalidResult,
    ReadSessionLinesResult,
    read_session_lines,
)

if TYPE_CHECKING:
    from prompt_diary.generate.evidence_extraction.session_compaction import CompactRecord
    from prompt_diary.generate.evidence_extraction.session_reader import SessionReadError

PROJECT_KEY = "ReportGenerator-e6ff7eeda632"
SESSION_REF = "S0001"
SESSION_REF_CLAUDE = "S0002"
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "session-reader"


def copy_session_reader_workspace(tmp_path: Path) -> Path:
    """Copy the dedicated session-reader fixture workspace into a writable test directory."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE_ROOT / "workspace", workspace)
    return workspace


def session_file_path(workspace_path: Path) -> Path:
    """Return the resolved session file path inside the prepared workspace fixture."""
    return workspace_path / "projects" / PROJECT_KEY / "sessions" / "codex" / "session-001.jsonl"


def session_physical_lines(workspace_path: Path) -> list[str]:
    """Return the session file's physical lines exactly as ``prepare`` numbers them."""
    raw_bytes = session_file_path(workspace_path).read_bytes()
    return raw_bytes.decode("utf-8", errors="replace").splitlines()


def overwrite_session_line(workspace_path: Path, *, line: int, raw_line: str) -> None:
    """Replace one physical line of the copied session file, preserving all other lines."""
    lines = session_physical_lines(workspace_path)
    lines[line - 1] = raw_line
    session_file_path(workspace_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def grow_session_to(workspace_path: Path, *, total_lines: int) -> None:
    """Rewrite the copied session file so it holds exactly ``total_lines`` valid codex records.

    Used to test mode caps against an in-bounds range, where the requested range is contained by
    the session yet still wider than the mode's cap.
    """
    records = [
        json.dumps(
            {
                "payload": {
                    "content": [{"text": f"synthetic line {number}", "type": "input_text"}],
                    "role": "user",
                    "type": "message",
                },
                "type": "response_item",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for number in range(1, total_lines + 1)
    ]
    session_file_path(workspace_path).write_text("\n".join(records) + "\n", encoding="utf-8")


def call_read_session_lines(
    *,
    workspace_path: Path,
    project_key: str = PROJECT_KEY,
    session_ref: str = SESSION_REF,
    start_line: int,
    end_line: int,
    mode: Literal["compact", "full"] = "compact",
) -> ReadSessionLinesResult:
    return read_session_lines(
        workspace_path=workspace_path,
        project_key=project_key,
        session_ref=session_ref,
        start_line=start_line,
        end_line=end_line,
        mode=mode,
    )


def expect_compact(result: ReadSessionLinesResult) -> ReadSessionLinesCompactResult:
    """Assert a successful compact read; a single isinstance narrows ``records`` to compact."""
    assert isinstance(result, ReadSessionLinesCompactResult), result
    return result


def expect_full(result: ReadSessionLinesResult) -> ReadSessionLinesFullResult:
    """Assert a successful full read; a single isinstance narrows ``records`` to full."""
    assert isinstance(result, ReadSessionLinesFullResult), result
    return result


def compact_records_by_line(ok: ReadSessionLinesCompactResult) -> dict[int, CompactRecord]:
    """Index a compact read's records by physical line number.

    ``ok.records`` is already ``tuple[CompactRecord, ...]`` thanks to the discriminated result type,
    so no per-element narrowing is needed here.
    """
    return {record.line: record for record in ok.records}


def result_to_dict(result: object) -> dict[str, Any]:
    """Serialize a reader result into the JSON wire shape FastMCP emits for the same dataclass.

    The MCP wrapper returns the dataclass result and lets FastMCP serialize it, so this mirror
    must match that serialization exactly for invalid-parity assertions to hold. A parsed JSON
    ``Mapping`` (the decoded MCP content block) passes through unchanged.
    """
    if isinstance(result, ReadSessionLinesCompactResult):
        return {
            "status": result.status,
            "project_key": result.project_key,
            "session_ref": result.session_ref,
            "line_range": {"start": result.line_range.start, "end": result.line_range.end},
            "mode": result.mode,
            "records": [compact_record_to_json(record) for record in result.records],
        }
    if isinstance(result, ReadSessionLinesFullResult):
        return {
            "status": result.status,
            "project_key": result.project_key,
            "session_ref": result.session_ref,
            "line_range": {"start": result.line_range.start, "end": result.line_range.end},
            "mode": result.mode,
            "records": [_full_record_to_json(record) for record in result.records],
        }
    if isinstance(result, ReadSessionLinesInvalidResult):
        return {
            "status": result.status,
            "errors": [
                {"field": error.field, "message": error.message, "hint": error.hint}
                for error in result.errors
            ],
        }
    if isinstance(result, Mapping):
        return dict(cast("Mapping[str, Any]", result))
    pytest.fail(f"result must be a read session lines result or mapping, got {type(result)!r}")


def _full_record_to_json(record: FullRecord) -> dict[str, Any]:
    return {
        "line": record.line,
        "raw_line": record.raw_line,
        "raw_bytes": record.raw_bytes,
        "raw_sha256": record.raw_sha256,
    }


def assert_read_invalid(
    result: ReadSessionLinesResult,
    *,
    field: str,
    message_contains: str | None = None,
    hint_contains: str | None = None,
) -> SessionReadError:
    """Assert a structured invalid read carrying ``field`` and return the matching error."""
    assert isinstance(result, ReadSessionLinesInvalidResult), result
    matching = [error for error in result.errors if error.field == field]
    assert matching, f"expected invalid result to include field {field!r}: {result.errors!r}"
    error = matching[0]
    assert error.message
    assert error.hint
    if message_contains is not None:
        assert message_contains in error.message
    if hint_contains is not None:
        assert hint_contains in error.hint
    return error
