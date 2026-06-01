from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.generate.evidence_extraction.session_compaction import (
    CompactRecord,
    line_provenance,
)
from prompt_diary.generate.evidence_extraction.session_reader import (
    MAX_COMPACT_LINES,
    MAX_FULL_LINES,
    FullRecord,
)
from tests.support.session_reader import (
    PROJECT_KEY,
    SESSION_REF,
    assert_read_invalid,
    call_read_session_lines,
    compact_records_by_line,
    copy_session_reader_workspace,
    expect_ok,
    overwrite_session_line,
    session_file_path,
    session_physical_lines,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_compact_read_returns_compact_records_with_absolute_line_numbers(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_ok(call_read_session_lines(workspace_path=workspace, start_line=2, end_line=8))

    assert ok.status == "ok"
    assert ok.project_key == PROJECT_KEY
    assert ok.session_ref == SESSION_REF
    assert ok.mode == "compact"
    assert (ok.line_range.start, ok.line_range.end) == (2, 8)
    assert all(isinstance(record, CompactRecord) for record in ok.records)
    assert [record.line for record in ok.records] == [2, 3, 4, 5, 6, 7, 8]


def test_compact_read_trims_large_tool_result_and_passes_small_through(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_ok(call_read_session_lines(workspace_path=workspace, start_line=5, end_line=6))
    by_line = compact_records_by_line(ok)

    large = by_line[5].tool_results[0]
    assert large.truncated is True
    assert large.raw_bytes == 1920
    small = by_line[6].tool_results[0]
    assert small.truncated is False
    assert small.preview == "ok: 3 files changed, all tests passed."


def test_compact_read_omits_assistant_reasoning(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_ok(call_read_session_lines(workspace_path=workspace, start_line=7, end_line=7))
    reasoning = ok.records[0]

    assert isinstance(reasoning, CompactRecord)
    assert reasoning.content_kinds == ("thinking",)
    assert reasoning.text_preview is None
    assert reasoning.summary == "Assistant reasoning omitted."


def test_full_read_returns_raw_lines_verbatim_with_matching_provenance(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    physical = session_physical_lines(workspace)

    ok = expect_ok(
        call_read_session_lines(workspace_path=workspace, start_line=2, end_line=4, mode="full")
    )

    assert ok.mode == "full"
    assert [record.line for record in ok.records] == [2, 3, 4]
    for record in ok.records:
        assert isinstance(record, FullRecord)
        raw_line = physical[record.line - 1]
        raw_bytes, raw_sha256 = line_provenance(raw_line)
        assert record.raw_line == raw_line
        assert record.raw_bytes == raw_bytes
        assert record.raw_sha256 == raw_sha256


def test_default_mode_is_compact(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_ok(call_read_session_lines(workspace_path=workspace, start_line=2, end_line=2))

    assert ok.mode == "compact"
    assert all(isinstance(record, CompactRecord) for record in ok.records)


def test_line_numbers_match_true_physical_lines(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    physical = session_physical_lines(workspace)

    ok = expect_ok(
        call_read_session_lines(workspace_path=workspace, start_line=3, end_line=5, mode="full")
    )

    assert [record.line for record in ok.records] == [3, 4, 5]
    for record in ok.records:
        assert isinstance(record, FullRecord)
        assert record.raw_line == physical[record.line - 1]


def test_provenance_parity_between_compact_and_full_for_same_line(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    compact = expect_ok(
        call_read_session_lines(workspace_path=workspace, start_line=5, end_line=5)
    ).records[0]
    full = expect_ok(
        call_read_session_lines(workspace_path=workspace, start_line=5, end_line=5, mode="full")
    ).records[0]

    assert (compact.raw_bytes, compact.raw_sha256) == (full.raw_bytes, full.raw_sha256)


def test_unknown_project_key_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(
        workspace_path=workspace,
        project_key="Missing-000000000000",
        start_line=2,
        end_line=2,
    )

    assert_read_invalid(result, field="project_key")


def test_unknown_session_ref_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(
        workspace_path=workspace, session_ref="S9999", start_line=2, end_line=2
    )

    assert_read_invalid(result, field="session_ref")


def test_missing_session_file_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    session_file_path(workspace).unlink()

    result = call_read_session_lines(workspace_path=workspace, start_line=2, end_line=2)

    assert_read_invalid(result, field="session_ref")


def test_start_line_below_one_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(workspace_path=workspace, start_line=0, end_line=2)

    assert_read_invalid(result, field="start_line")


def test_reversed_range_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(workspace_path=workspace, start_line=5, end_line=3)

    assert_read_invalid(result, field="end_line")


def test_start_line_past_end_of_session_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(workspace_path=workspace, start_line=9, end_line=9)

    assert_read_invalid(result, field="start_line")


def test_end_line_past_end_of_session_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(workspace_path=workspace, start_line=2, end_line=9)

    assert_read_invalid(result, field="end_line")


def test_compact_range_wider_than_cap_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(
        workspace_path=workspace, start_line=1, end_line=MAX_COMPACT_LINES + 1
    )

    assert_read_invalid(result, field="end_line", hint_contains="narrower")


def test_full_range_wider_than_cap_is_invalid(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(
        workspace_path=workspace, start_line=1, end_line=MAX_FULL_LINES + 1, mode="full"
    )

    assert_read_invalid(result, field="end_line", hint_contains="narrower")


def test_malformed_line_in_range_is_handled_gracefully_in_compact_mode(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    malformed = "this is not json {"
    overwrite_session_line(workspace, line=4, raw_line=malformed)

    ok = expect_ok(call_read_session_lines(workspace_path=workspace, start_line=3, end_line=5))
    by_line = compact_records_by_line(ok)

    fallback = by_line[4]
    assert fallback.line == 4
    assert fallback.summary == "Malformed JSONL line."
    raw_bytes, raw_sha256 = line_provenance(malformed)
    assert fallback.raw_bytes == raw_bytes
    assert fallback.raw_sha256 == raw_sha256
    # Surrounding well-formed lines are still parsed normally.
    assert by_line[3].content_kinds == ("text",)


def test_malformed_line_in_range_is_returned_verbatim_in_full_mode(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    malformed = "}{ broken json"
    overwrite_session_line(workspace, line=4, raw_line=malformed)

    ok = expect_ok(
        call_read_session_lines(workspace_path=workspace, start_line=4, end_line=4, mode="full")
    )
    record = ok.records[0]

    assert isinstance(record, FullRecord)
    raw_bytes, raw_sha256 = line_provenance(malformed)
    assert record.raw_line == malformed
    assert record.raw_bytes == raw_bytes
    assert record.raw_sha256 == raw_sha256
