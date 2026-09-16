from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

import pytest

from prompt_diary.source_records import (
    CodexMessage,
    is_claude_tool_result_content,
    is_codex_message_echo,
    parse_codex_message,
)

if TYPE_CHECKING:
    from prompt_diary.models import JsonObject


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("milliseconds", [-1, 0, 50, 100, 101])
def test_echo_requires_source_order_exact_text_and_nearby_forward_timestamps(
    role: Literal["user", "assistant"], milliseconds: int
) -> None:
    timestamp = datetime(2026, 5, 12, tzinfo=timezone.utc)
    first = CodexMessage(role, "response" if role == "user" else "event", "message", timestamp)
    second = CodexMessage(
        role,
        "event" if role == "user" else "response",
        "message",
        timestamp + timedelta(milliseconds=milliseconds),
    )

    assert is_codex_message_echo(first, second) is (0 <= milliseconds <= 100)
    assert not is_codex_message_echo(second, first)
    assert not is_codex_message_echo(first, first)
    assert not is_codex_message_echo(
        first, CodexMessage(second.role, second.representation, "different", second.timestamp)
    )
    assert not is_codex_message_echo(
        first,
        CodexMessage(
            "assistant" if role == "user" else "user",
            second.representation,
            second.text,
            second.timestamp,
        ),
    )


def test_parsed_complete_text_matches_compact_projection_and_timezone() -> None:
    response = parse_codex_message(
        {
            "type": "response_item",
            "timestamp": "2026-05-12T08:00:00+08:00",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "first"},
                    {"type": "input_text", "text": "second"},
                ],
            },
        }
    )
    event = parse_codex_message(
        {
            "type": "event_msg",
            "timestamp": "2026-05-12T00:00:00.001Z",
            "payload": {"type": "user_message", "message": "first\nsecond"},
        }
    )

    assert response is not None
    assert event is not None
    assert response.text == "first\nsecond"
    assert is_codex_message_echo(response, event)


def test_parsed_assistant_echo_uses_event_then_response() -> None:
    event = parse_codex_message(
        {
            "type": "event_msg",
            "timestamp": "2026-05-12T00:00:00Z",
            "payload": {"type": "agent_message", "message": "done"},
        }
    )
    response = parse_codex_message(
        {
            "type": "response_item",
            "timestamp": "2026-05-12T00:00:00Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "done"},
                ],
            },
        }
    )

    assert event is not None
    assert response is not None
    assert is_codex_message_echo(event, response)


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"timestamp": "invalid", "payload": {}},
        {"timestamp": "2026-05-12T00:00:00", "payload": {}},
        {"timestamp": "2026-05-12T00:00:00Z"},
        {"type": "turn_context", "payload": {}},
        {"type": "event_msg", "payload": {"type": "token_count", "message": "text"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": 1}},
        {"type": "response_item", "payload": {"type": "function_call"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": []}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "text"},
        },
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [1]}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "text"},
                    {"type": "input_image", "image_url": "image"},
                ],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": 1},
                ],
            },
        },
    ],
)
def test_unknown_record_shapes_cannot_prove_echoes(record: JsonObject) -> None:
    timestamped: JsonObject = {"timestamp": "2026-05-12T00:00:00Z", **record}
    assert parse_codex_message(timestamped) is None


@pytest.mark.parametrize("content", [None, "text", [], [1], [{"type": "text", "text": "Continue"}]])
def test_unknown_or_human_claude_content_is_not_pure_tool_results(content: object) -> None:
    assert not is_claude_tool_result_content(content)


def test_missing_timestamp_cannot_prove_an_echo() -> None:
    assert parse_codex_message({"payload": {}}) is None
