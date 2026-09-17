from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.generate.evidence_extraction.session_compaction import (
    compact_record_to_json,
)
from prompt_diary.generate.evidence_extraction.session_reader import (
    FullRecord,
    ReadSessionLinesCompactResult,
)
from tests.support.session_reader import (
    PROJECT_KEY,
    SESSION_REF,
    SESSION_REF_CLAUDE,
    assert_read_invalid,
    call_read_session_lines,
    compact_records_by_line,
    copy_session_reader_workspace,
    expect_compact,
    expect_full,
    overwrite_session_line,
    session_file_path,
    session_physical_lines,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_compact_read_returns_compact_records_with_absolute_line_numbers(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_compact(call_read_session_lines(workspace_path=workspace, start_line=2, end_line=8))

    assert ok.status == "ok"
    assert ok.project_key == PROJECT_KEY
    assert ok.session_ref == SESSION_REF
    assert ok.mode == "compact"
    assert (ok.line_range.start, ok.line_range.end) == (2, 8)
    assert [record.line for record in ok.records] == [2, 3, 4, 5, 6, 8]


def test_compact_result_type_narrows_records_for_serialization(tmp_path: Path) -> None:
    """A single isinstance on the result narrows ``records`` to compact for the Task-3 serializer.

    The MCP serializer dispatches on the result subtype, then maps ``compact_record_to_json`` over
    ``result.records`` with no per-element ``isinstance`` narrowing. This test exercises exactly
    that path, so it fails type checking if the ok-result stops discriminating ``records`` by mode.
    """
    workspace = copy_session_reader_workspace(tmp_path)

    result = call_read_session_lines(workspace_path=workspace, start_line=2, end_line=8)
    assert isinstance(result, ReadSessionLinesCompactResult), result

    payload = [
        compact_record_to_json(record) for record in compact_records_by_line(result).values()
    ]

    assert [entry["line"] for entry in payload] == [2, 3, 4, 5, 6, 8]


def test_compact_read_trims_large_tool_result_and_passes_small_through(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_compact(call_read_session_lines(workspace_path=workspace, start_line=5, end_line=6))
    by_line = compact_records_by_line(ok)

    large = by_line[5].tool_results[0]
    assert large.truncated is True
    small = by_line[6].tool_results[0]
    assert small.truncated is False
    assert small.preview == "ok: 3 files changed, all tests passed."


def test_compact_read_omits_assistant_reasoning(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_compact(call_read_session_lines(workspace_path=workspace, start_line=7, end_line=7))
    assert ok.records == ()
    assert ok.next_cursor is None
    assert (ok.line_range.start, ok.line_range.end) == (7, 7)


def test_full_read_returns_raw_lines_verbatim(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    physical = session_physical_lines(workspace)

    ok = expect_full(
        call_read_session_lines(workspace_path=workspace, start_line=2, end_line=4, mode="full")
    )

    assert ok.mode == "full"
    assert [record.line for record in ok.records] == [2, 3, 4]
    for record in ok.records:
        assert isinstance(record, FullRecord)
        raw_line = physical[record.line - 1]
        assert record.raw_line == raw_line


def test_default_mode_is_compact(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_compact(call_read_session_lines(workspace_path=workspace, start_line=2, end_line=2))

    assert ok.mode == "compact"


def test_compact_read_uses_the_resolved_session_source(tmp_path: Path) -> None:
    """The reader must compact with the session's real source, not a hardcoded one."""
    workspace = copy_session_reader_workspace(tmp_path)

    ok = expect_compact(
        call_read_session_lines(
            workspace_path=workspace, session_ref=SESSION_REF_CLAUDE, start_line=1, end_line=2
        )
    )
    by_line = compact_records_by_line(ok)

    assert by_line[1].kind == "user"
    assert by_line[1].text == "Summarize today's changes."
    assert by_line[2].kind == "assistant"
    assert by_line[2].text == "Here is the summary of changes."


def test_line_numbers_match_true_physical_lines(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    physical = session_physical_lines(workspace)

    ok = expect_full(
        call_read_session_lines(workspace_path=workspace, start_line=3, end_line=5, mode="full")
    )

    assert [record.line for record in ok.records] == [3, 4, 5]
    for record in ok.records:
        assert isinstance(record, FullRecord)
        assert record.raw_line == physical[record.line - 1]


def test_compact_and_full_read_cite_the_same_physical_line(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)

    compact = expect_compact(
        call_read_session_lines(workspace_path=workspace, start_line=5, end_line=5)
    ).records[0]
    full = expect_full(
        call_read_session_lines(workspace_path=workspace, start_line=5, end_line=5, mode="full")
    ).records[0]

    assert compact.line == full.line == 5


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


def test_malformed_line_in_range_is_handled_gracefully_in_compact_mode(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    malformed = "this is not json {"
    overwrite_session_line(workspace, line=4, raw_line=malformed)

    ok = expect_compact(call_read_session_lines(workspace_path=workspace, start_line=3, end_line=5))
    by_line = compact_records_by_line(ok)

    fallback = by_line[4]
    assert fallback.line == 4
    assert fallback.kind == "malformed"
    assert fallback.text == malformed
    # Surrounding well-formed lines are still parsed normally.
    assert by_line[3].kind == "assistant"


def test_malformed_line_in_range_is_returned_verbatim_in_full_mode(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    malformed = "}{ broken json"
    overwrite_session_line(workspace, line=4, raw_line=malformed)

    ok = expect_full(
        call_read_session_lines(workspace_path=workspace, start_line=4, end_line=4, mode="full")
    )
    record = ok.records[0]
    assert isinstance(record, FullRecord)

    assert record.raw_line == malformed
