from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest
from mcp.types import TextContent
from pydantic import TypeAdapter

from prompt_diary.generate.evidence_extraction.session_compaction import (
    CompactRecord,
    compact_record,
    line_provenance,
)
from prompt_diary.generate.evidence_extraction.session_reader import (
    MAX_SESSION_READ_BYTES,
    FullRecord,
    ReadCursor,
    ReadSessionLinesCompactResult,
    ReadSessionLinesFullResult,
    RecordFragment,
    serialize_read_result,
)
from prompt_diary.mcp import server as mcp_server
from tests.support.session_reader import (
    PROJECT_KEY,
    SESSION_REF,
    assert_read_invalid,
    call_read_session_lines,
    copy_session_reader_workspace,
    expect_compact,
    session_file_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_lines(workspace: Path, lines: list[str]) -> None:
    session_file_path(workspace).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _codex_message(text: str, role: str = "user") -> str:
    return json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": role,
                "content": [{"type": "input_text", "text": text}],
            },
        },
        ensure_ascii=True,
    )


@pytest.mark.parametrize("mode", ["compact", "full"])
def test_large_messages_reconstruct_exactly_through_bounded_pages(
    tmp_path: Path, mode: Literal["compact", "full"]
) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    text = '中文🙂"\\\n\t' * 65_000 + "\ud800"
    lines = [_codex_message(text), _codex_message("Observed completion.", "assistant")]
    _write_lines(workspace, lines)
    cursor = None
    fragments: dict[int, str] = {}
    reconstructed: dict[int, CompactRecord | str] = {}
    page_count = 0

    while True:
        page = call_read_session_lines(
            workspace_path=workspace, start_line=1, end_line=2, mode=mode, cursor=cursor
        )
        assert isinstance(page, (ReadSessionLinesCompactResult, ReadSessionLinesFullResult))
        encoded = serialize_read_result(page)
        assert len(encoded.encode("utf-8")) <= MAX_SESSION_READ_BYTES
        assert page.records
        for record in page.records:
            assert (record.raw_bytes, record.raw_sha256) == line_provenance(lines[record.line - 1])
            if isinstance(record, RecordFragment):
                prior = fragments.get(record.line, "")
                assert record.offset == len(prior)
                assert record.content
                fragments[record.line] = prior + record.content
                if len(fragments[record.line]) == record.total_chars:
                    reconstructed[record.line] = (
                        TypeAdapter(CompactRecord).validate_python(
                            json.loads(fragments[record.line])
                        )
                        if mode == "compact"
                        else fragments[record.line]
                    )
            elif isinstance(record, FullRecord):
                reconstructed[record.line] = record.raw_line
            else:
                reconstructed[record.line] = record
        page_count += 1
        if page.next_cursor is None:
            break
        assert cursor is None or (page.next_cursor.line, page.next_cursor.offset) > (
            cursor.line,
            cursor.offset,
        )
        cursor = page.next_cursor

    assert page_count > 2
    if mode == "compact":
        assert reconstructed == {
            number: compact_record(line, line=number, source="codex")
            for number, line in enumerate(lines, 1)
        }
        message = reconstructed[1]
        assert isinstance(message, CompactRecord)
        assert message.text_preview == text
    else:
        assert reconstructed == dict(enumerate(lines, 1))


def test_more_than_two_thousand_lines_page_without_an_extra_record_cap(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    _write_lines(workspace, ['{"type":"turn_context"}'] * 2100)
    cursor = None
    seen: list[int] = []
    page_sizes: list[int] = []
    while True:
        page = expect_compact(
            call_read_session_lines(
                workspace_path=workspace, start_line=1, end_line=2100, cursor=cursor
            )
        )
        assert len(serialize_read_result(page).encode()) <= MAX_SESSION_READ_BYTES
        seen.extend(record.line for record in page.records)
        page_sizes.append(len(page.records))
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == list(range(1, 2101))
    assert page_sizes[0] > 64


def test_oversized_tool_metadata_fragments_without_losing_its_structure(tmp_path: Path) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    raw = json.dumps(
        {"type": "response_item", "payload": {"type": "function_call", "name": "tool" * 20_000}}
    )
    _write_lines(workspace, [raw])
    cursor = None
    content = ""
    while True:
        page = expect_compact(
            call_read_session_lines(
                workspace_path=workspace, start_line=1, end_line=1, cursor=cursor
            )
        )
        assert len(serialize_read_result(page).encode()) <= MAX_SESSION_READ_BYTES
        for record in page.records:
            assert isinstance(record, RecordFragment)
            content += record.content
        cursor = page.next_cursor
        if cursor is None:
            break
    assert TypeAdapter(CompactRecord).validate_json(content) == compact_record(
        raw, line=1, source="codex"
    )


@pytest.mark.parametrize("mode", ["compact", "full"])
@pytest.mark.parametrize(
    "cursor", [ReadCursor(0), ReadCursor(9), ReadCursor(2, -1), ReadCursor(2, 99_999)]
)
def test_invalid_cursors_return_bounded_errors(
    tmp_path: Path, mode: Literal["compact", "full"], cursor: ReadCursor
) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    result = call_read_session_lines(
        workspace_path=workspace, start_line=2, end_line=8, mode=mode, cursor=cursor
    )
    assert_read_invalid(result, field="cursor")
    assert len(serialize_read_result(result).encode()) <= MAX_SESSION_READ_BYTES


@pytest.mark.parametrize("mode", ["compact", "full"])
def test_mcp_emits_exact_bounded_json_and_exposes_cursor_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Literal["compact", "full"]
) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    _write_lines(workspace, [_codex_message('字"\\\n' * 50_000)])
    monkeypatch.setenv("PROMPT_DIARY_WORKSPACE", str(workspace))
    server = mcp_server.build_mcp_server()
    tool = next(
        item for item in asyncio.run(server.list_tools()) if item.name == "read_session_lines"
    )
    assert "cursor" in tool.inputSchema["properties"]
    assert tool.outputSchema is None
    arguments: dict[str, Any] = {
        "project_key": PROJECT_KEY,
        "session_ref": SESSION_REF,
        "start_line": 1,
        "end_line": 1,
        "mode": mode,
    }
    cursor = None
    for _ in range(2):
        response = asyncio.run(server.call_tool("read_session_lines", arguments))
        assert isinstance(response, list)
        assert len(response) == 1
        block = response[0]
        assert isinstance(block, TextContent)
        assert len(block.text.encode("utf-8")) <= MAX_SESSION_READ_BYTES
        api_result = call_read_session_lines(
            workspace_path=workspace, start_line=1, end_line=1, mode=mode, cursor=cursor
        )
        assert block.text == serialize_read_result(api_result)
        decoded = cast("dict[str, Any]", json.loads(block.text))
        assert decoded["next_cursor"] is not None
        cursor = TypeAdapter(ReadCursor).validate_python(decoded["next_cursor"])
        arguments["cursor"] = decoded["next_cursor"]


@pytest.mark.parametrize("field", ["project_key", "session_ref"])
def test_mcp_bounds_errors_that_echo_giant_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    monkeypatch.setenv("PROMPT_DIARY_WORKSPACE", str(workspace))
    server = mcp_server.build_mcp_server()
    arguments: dict[str, object] = {
        "project_key": PROJECT_KEY,
        "session_ref": SESSION_REF,
        "start_line": 1,
        "end_line": 1,
    }
    arguments[field] = "巨" * 100_000
    response = asyncio.run(server.call_tool("read_session_lines", arguments))
    assert isinstance(response, list)
    assert len(response) == 1
    block = response[0]
    assert isinstance(block, TextContent)
    assert len(block.text.encode("utf-8")) <= MAX_SESSION_READ_BYTES
    assert json.loads(block.text)["status"] == "invalid"


@pytest.mark.parametrize("mode", ["compact", "full"])
def test_indexed_identifier_cannot_make_result_metadata_exceed_the_page_budget(
    tmp_path: Path, mode: Literal["compact", "full"]
) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    entries = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    session_ref = "S" * MAX_SESSION_READ_BYTES
    entries[0]["session_ref"] = session_ref
    index_path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8"
    )

    result = call_read_session_lines(
        workspace_path=workspace,
        session_ref=session_ref,
        start_line=2,
        end_line=2,
        mode=mode,
    )

    assert_read_invalid(result, field="cursor", message_contains="no room")
    assert len(serialize_read_result(result).encode()) <= MAX_SESSION_READ_BYTES
