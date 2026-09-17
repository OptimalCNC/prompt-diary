from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

import pytest

from prompt_diary.generate.evidence_extraction.session_compaction import (
    compact_record_to_json,
)
from tests.support.session_reader import (
    call_read_session_lines,
    compact_records_by_line,
    copy_session_reader_workspace,
    expect_compact,
    read_compact_records,
    read_full_records,
    session_file_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def _message(
    representation: Literal["response", "event"],
    role: Literal["user", "assistant"],
    text: str,
    timestamp: str = "2026-06-01T00:00:00.000Z",
) -> str:
    payload: dict[str, object]
    if representation == "response":
        payload = {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        }
    else:
        payload = {"type": "user_message" if role == "user" else "agent_message", "message": text}
    return json.dumps(
        {
            "type": "response_item" if representation == "response" else "event_msg",
            "timestamp": timestamp,
            "payload": payload,
        }
    )


def _workspace_with_lines(tmp_path: Path, lines: list[str]) -> Path:
    workspace = copy_session_reader_workspace(tmp_path)
    session_file_path(workspace).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return workspace


def test_large_codex_echoes_preserve_one_message_and_physical_line_citations(
    tmp_path: Path,
) -> None:
    user_text = "U" * 50_000
    assistant_text = "A" * 30_000
    lines = [
        _message("response", "user", user_text),
        _message("event", "user", user_text, "2026-06-01T00:00:00.001Z"),
        _message("event", "assistant", assistant_text, "2026-06-01T00:00:01.000Z"),
        _message("response", "assistant", assistant_text, "2026-06-01T00:00:01.011Z"),
    ]
    workspace = _workspace_with_lines(tmp_path, lines)

    compact = read_compact_records(workspace_path=workspace, start_line=1, end_line=4)
    full = read_full_records(workspace_path=workspace, start_line=1, end_line=4)

    assert [record.line for record in compact] == [1, 2, 3, 4]
    assert [record.text for record in compact] == [
        user_text,
        None,
        None,
        assistant_text,
    ]
    assert sum(len(record.text or "") for record in compact) == 80_000
    assert [record.duplicate_of for record in compact] == [None, 1, 4, None]
    assert [record.raw_line for record in full] == lines
    for record in compact:
        assert compact_record_to_json(record).get("duplicate_of") == record.duplicate_of
        singleton = read_compact_records(
            workspace_path=workspace, start_line=record.line, end_line=record.line
        )
        assert singleton == (record,)
    assert compact_record_to_json(compact[1]) == {"line": 2, "kind": "duplicate", "duplicate_of": 1}


def test_repeated_human_actions_and_unmatched_events_remain_distinct(tmp_path: Path) -> None:
    text = "Continue."
    lines = [
        _message("response", "user", text),
        _message("event", "user", text),
        _message("response", "user", text),
        _message("event", "user", text),
        _message("event", "user", text),
        _message("response", "user", text),
        _message("response", "user", text),
    ]
    workspace = _workspace_with_lines(tmp_path, lines)

    result = expect_compact(
        call_read_session_lines(workspace_path=workspace, start_line=1, end_line=7)
    )

    records = tuple(compact_records_by_line(result).values())
    assert [record.duplicate_of for record in records] == [
        None,
        1,
        None,
        3,
        None,
        None,
        None,
    ]
    assert [record.line for record in records if record.text == text] == [
        1,
        3,
        5,
        6,
        7,
    ]


def test_canonical_message_preserves_blank_content_parts_from_confirmed_echo(
    tmp_path: Path,
) -> None:
    text = "First paragraph.\n\n \nLast paragraph."
    canonical = json.dumps(
        {
            "type": "response_item",
            "timestamp": "2026-06-01T00:00:00.000Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": part}
                    for part in ("First paragraph.", "", " ", "Last paragraph.")
                ],
            },
        }
    )
    workspace = _workspace_with_lines(tmp_path, [canonical, _message("event", "user", text)])

    result = expect_compact(
        call_read_session_lines(workspace_path=workspace, start_line=1, end_line=2)
    )

    records = compact_records_by_line(result)
    assert records[1].text == text
    assert records[2].text is None
    assert records[2].duplicate_of == 1


@pytest.mark.parametrize(
    "lines",
    [
        [_message("response", "user", "One"), _message("event", "user", "Two")],
        [_message("response", "user", "One"), _message("event", "assistant", "One")],
        [
            _message("response", "user", "One"),
            _message("event", "user", "One", "2026-06-01T00:00:00.101Z"),
        ],
        [
            _message("response", "user", "One"),
            _message("event", "user", "One", "2026-05-31T23:59:59.999Z"),
        ],
        [_message("response", "user", "One"), _message("event", "user", "One", "")],
        [_message("event", "user", "One"), _message("response", "user", "One")],
        [
            _message("response", "user", "One"),
            '{"type":"event_msg","payload":{"type":"token_count"}}',
            _message("event", "user", "One"),
        ],
        ["not JSON", _message("event", "user", "One")],
    ],
)
def test_uncertain_codex_pairs_keep_message_text(tmp_path: Path, lines: list[str]) -> None:
    workspace = _workspace_with_lines(tmp_path, lines)

    result = expect_compact(
        call_read_session_lines(workspace_path=workspace, start_line=1, end_line=len(lines))
    )

    records = tuple(compact_records_by_line(result).values())
    assert all(record.duplicate_of is None for record in records)
    expected_texts = [
        "Two" if '"Two"' in line else "One" for line in lines if '"One"' in line or '"Two"' in line
    ]
    assert [
        record.text for record in records if record.kind in ("user", "assistant")
    ] == expected_texts


def _modern_message(role: str, content: object) -> str:
    return json.dumps(
        {
            "type": "event_msg",
            "timestamp": "2026-06-01T00:00:00.000Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "UserMessage" if role == "user" else "AgentMessage",
                    "content": content,
                },
            },
        }
    )


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_modern_message_echo_requires_identical_adjacent_text(
    tmp_path: Path, role: Literal["user", "assistant"]
) -> None:
    text = "Please continue.\n\n "
    event = _modern_message(
        role,
        [
            {"type": "text" if role == "user" else "Text", "text": part}
            for part in ("Please continue.", "", " ")
        ],
    )
    canonical = _message("response", role, text)
    lines = [canonical, event] if role == "user" else [event, canonical]
    workspace = _workspace_with_lines(tmp_path, lines)

    records = read_compact_records(workspace_path=workspace, start_line=1, end_line=2)

    assert [record.text for record in records if record.text is not None] == [text]
    echo = records[1] if role == "user" else records[0]
    assert echo.duplicate_of == (1 if role == "user" else 2)
    assert read_compact_records(
        workspace_path=workspace, start_line=echo.line, end_line=echo.line
    ) == (echo,)


@pytest.mark.parametrize(
    "content",
    [
        "different text",
        [{"type": "text", "text": "different text"}],
        [{"type": "image"}],
        [1],
        None,
    ],
)
def test_modern_message_uncertainty_keeps_evidence_visible(tmp_path: Path, content: object) -> None:
    workspace = _workspace_with_lines(
        tmp_path, [_message("response", "user", "Original"), _modern_message("user", content)]
    )

    records = read_compact_records(workspace_path=workspace, start_line=1, end_line=2)

    assert len(records) == 2
    assert records[1].duplicate_of is None
    assert records[1].text is not None or records[1].unavailable


def test_source_context_echo_does_not_leave_a_dangling_pointer(tmp_path: Path) -> None:
    text = "<environment_context>injected setup</environment_context>"
    workspace = _workspace_with_lines(
        tmp_path, [_message("response", "user", text), _message("event", "user", text)]
    )

    assert read_compact_records(workspace_path=workspace, start_line=1, end_line=2) == ()
