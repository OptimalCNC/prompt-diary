from __future__ import annotations

import hashlib
import json

from prompt_diary.generate.evidence_extraction.session_compaction import (
    compact_record,
    compact_record_to_json,
)


def test_records_raw_bytes_and_sha256_for_a_known_line() -> None:
    raw = '{"role":"user","content":"hi"}'

    record = compact_record(raw, line=7, source="codex")

    assert record.line == 7
    assert record.raw_bytes == len(raw.encode("utf-8"))
    assert record.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_simplified_role_shape_falls_back_to_top_level_role() -> None:
    raw = '{"role":"user","content":"Please update the docs."}'

    record = compact_record(raw, line=2, source="codex")

    assert record.record_type == "user"
    assert record.role == "user"
    assert record.truncated is False


def test_non_json_line_falls_back_without_raising() -> None:
    raw = "this is not json"

    record = compact_record(raw, line=4, source="claude-code")

    assert record.record_type == "unknown"
    assert record.role is None
    assert record.summary == "Malformed JSONL line."
    assert record.raw_bytes == len(raw.encode("utf-8"))
    assert record.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_json_array_line_is_treated_as_malformed() -> None:
    record = compact_record("[1, 2, 3]", line=9, source="codex")

    assert record.record_type == "unknown"
    assert record.summary == "Malformed JSONL line."


def test_codex_event_user_message_keeps_full_text_as_primary_evidence() -> None:
    raw = json.dumps(
        {
            "payload": {"message": "First redacted task.", "type": "user_message"},
            "type": "event_msg",
        }
    )

    record = compact_record(raw, line=5, source="codex")

    assert record.record_type == "event_msg:user_message"
    assert record.role == "user"
    assert record.content_kinds == ("text",)
    assert record.text_preview == "First redacted task."
    assert record.summary == "User message."
    assert record.truncated is False


def test_codex_event_agent_message_keeps_full_assistant_text() -> None:
    raw = json.dumps(
        {
            "payload": {
                "message": "The release-note check is complete; no edits are needed.",
                "phase": "final_answer",
                "type": "agent_message",
            },
            "type": "event_msg",
        }
    )

    record = compact_record(raw, line=10, source="codex")

    assert record.record_type == "event_msg:agent_message"
    assert record.role == "assistant"
    assert record.content_kinds == ("text",)
    assert record.text_preview == "The release-note check is complete; no edits are needed."
    assert record.summary == "Agent message."
    assert record.truncated is False


def test_codex_event_token_count_is_scaffolding_metadata() -> None:
    raw = json.dumps(
        {
            "payload": {"info": {"model_context_window": 0}, "type": "token_count"},
            "type": "event_msg",
        }
    )

    record = compact_record(raw, line=6, source="codex")

    assert record.record_type == "event_msg:token_count"
    assert record.role is None
    assert record.content_kinds == ()
    assert record.text_preview is None
    assert record.summary == "event_msg:token_count record."


def test_codex_response_item_user_message_keeps_full_text() -> None:
    raw = json.dumps(
        {
            "payload": {
                "content": [
                    {"text": "Please implement the report generator.", "type": "input_text"}
                ],
                "role": "user",
                "type": "message",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=4, source="codex")

    assert record.record_type == "response_item:message"
    assert record.role == "user"
    assert record.content_kinds == ("text",)
    assert record.text_preview == "Please implement the report generator."
    assert record.summary == "User message."
    assert record.truncated is False


def test_codex_response_item_assistant_message_keeps_full_text() -> None:
    raw = json.dumps(
        {
            "payload": {
                "content": [
                    {"text": "I will start by listing stale branches.", "type": "output_text"}
                ],
                "phase": "commentary",
                "role": "assistant",
                "type": "message",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=8, source="codex")

    assert record.record_type == "response_item:message"
    assert record.role == "assistant"
    assert record.content_kinds == ("text",)
    assert record.text_preview == "I will start by listing stale branches."
    assert record.summary == "Assistant message."


def test_codex_function_call_summarizes_tool_use() -> None:
    raw = json.dumps(
        {
            "payload": {
                "arguments": '{"cmd":"rg -n release docs README.md","workdir":"/fake/projects/x"}',
                "call_id": "call_cross_day_check",
                "name": "exec_command",
                "type": "function_call",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=11, source="codex")

    assert record.record_type == "response_item:function_call"
    assert record.role is None
    assert record.content_kinds == ("tool_use",)
    assert len(record.tool_uses) == 1
    tool_use = record.tool_uses[0]
    assert tool_use.name == "exec_command"
    assert "rg -n release" in tool_use.input_summary
    assert record.summary == "Tool call: exec_command."
    assert record.tool_results == ()
    assert record.truncated is False


def test_codex_function_call_output_small_payload_passes_through() -> None:
    output = "Process exited with code 0\nOutput:\nrelease notes look fine\n"
    raw = json.dumps(
        {
            "payload": {
                "call_id": "call_cross_day_check",
                "output": output,
                "type": "function_call_output",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=12, source="codex")

    assert record.record_type == "response_item:function_call_output"
    assert record.content_kinds == ("tool_result",)
    assert len(record.tool_results) == 1
    result = record.tool_results[0]
    assert result.preview == output
    assert result.truncated is False
    assert result.raw_bytes == len(output.encode("utf-8"))
    assert record.truncated is False
    assert record.summary == "Tool result."


def test_codex_function_call_output_large_payload_is_trimmed_head_and_tail() -> None:
    output = "HEAD-MARKER " + ("x" * 4000) + " TAIL-MARKER"
    raw = json.dumps(
        {
            "payload": {
                "call_id": "call_big",
                "output": output,
                "type": "function_call_output",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=13, source="codex")

    result = record.tool_results[0]
    assert result.truncated is True
    assert record.truncated is True
    assert result.raw_bytes == len(output.encode("utf-8"))
    assert "HEAD-MARKER" in result.preview
    assert "TAIL-MARKER" in result.preview
    assert "...[trimmed]..." in result.preview
    assert len(result.preview.encode("utf-8")) < len(output.encode("utf-8"))


def test_codex_reasoning_is_omitted_and_marked_truncated() -> None:
    raw = json.dumps(
        {
            "payload": {
                "content": None,
                "encrypted_content": "[redacted]",
                "summary": [],
                "type": "reasoning",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=5, source="codex")

    assert record.record_type == "response_item:reasoning"
    assert record.content_kinds == ("thinking",)
    assert record.text_preview is None
    assert record.truncated is True
    assert record.summary == "Assistant reasoning omitted."


def test_codex_session_meta_is_scaffolding() -> None:
    raw = json.dumps(
        {
            "payload": {"cwd": "/fake/projects/x", "id": "abc", "type": "session_meta"},
            "timestamp": "2026-05-11T15:59:58.000Z",
            "type": "session_meta",
        }
    )

    record = compact_record(raw, line=1, source="codex")

    assert record.record_type == "session_meta"
    assert record.role is None
    assert record.content_kinds == ()
    assert record.tool_uses == ()
    assert record.tool_results == ()
    assert record.summary == "session_meta record."
    assert record.truncated is False


def test_codex_turn_context_is_scaffolding() -> None:
    raw = json.dumps(
        {
            "payload": {"cwd": "/fake/projects/x", "turn_id": "t-1"},
            "type": "turn_context",
        }
    )

    record = compact_record(raw, line=3, source="codex")

    assert record.record_type == "turn_context"
    assert record.summary == "turn_context record."


def test_claude_user_trigger_keeps_full_text() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [{"text": "Please review the plan.", "type": "text"}],
                "role": "user",
            },
            "type": "user",
            "uuid": "00000000-0000-4000-8000-000000000002",
        }
    )

    record = compact_record(raw, line=3, source="claude-code")

    assert record.record_type == "user"
    assert record.role == "user"
    assert record.content_kinds == ("text",)
    assert record.text_preview == "Please review the plan."
    assert record.summary == "User message."
    assert record.truncated is False


def test_claude_assistant_text_keeps_full_text() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [{"text": "Done; all tests pass.", "type": "text"}],
                "role": "assistant",
            },
            "type": "assistant",
        }
    )

    record = compact_record(raw, line=4, source="claude-code")

    assert record.record_type == "assistant"
    assert record.role == "assistant"
    assert record.content_kinds == ("text",)
    assert record.text_preview == "Done; all tests pass."
    assert record.summary == "Assistant message."


def test_claude_assistant_tool_use_summarizes_call_with_text() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [
                    {"text": "Launching a review.", "type": "text"},
                    {
                        "id": "toolu_claude_agent",
                        "input": {"description": "review", "subagent_type": "Explore"},
                        "name": "Agent",
                        "type": "tool_use",
                    },
                ],
                "role": "assistant",
            },
            "type": "assistant",
        }
    )

    record = compact_record(raw, line=4, source="claude-code")

    assert record.record_type == "assistant"
    assert record.role == "assistant"
    assert record.content_kinds == ("text", "tool_use")
    assert record.text_preview == "Launching a review."
    assert len(record.tool_uses) == 1
    assert record.tool_uses[0].name == "Agent"
    assert "Explore" in record.tool_uses[0].input_summary
    assert record.summary == "Assistant message."
    assert record.truncated is False


def test_claude_user_tool_result_small_payload_passes_through() -> None:
    payload_text = "Async agent launched successfully.\nagentId: a000000000000001"
    raw = json.dumps(
        {
            "message": {
                "content": [
                    {
                        "content": [{"text": payload_text, "type": "text"}],
                        "tool_use_id": "toolu_claude_agent",
                        "type": "tool_result",
                    }
                ],
                "role": "user",
            },
            "sourceToolAssistantUUID": "00000000-0000-4000-8000-000000000002",
            "toolUseResult": {"agentId": "a000000000000001", "status": "async_launched"},
            "type": "user",
        }
    )

    record = compact_record(raw, line=5, source="claude-code")

    assert record.record_type == "user"
    assert record.role == "user"
    assert record.content_kinds == ("tool_result",)
    assert len(record.tool_results) == 1
    result = record.tool_results[0]
    assert result.status == "async_launched"
    assert result.preview == payload_text
    assert result.truncated is False
    assert result.raw_bytes == len(payload_text.encode("utf-8"))
    assert record.summary == "Tool result."
    assert record.truncated is False


def test_claude_user_tool_result_large_payload_is_trimmed() -> None:
    payload_text = "FILE-HEAD " + ("y" * 5000) + " FILE-TAIL"
    raw = json.dumps(
        {
            "message": {
                "content": [
                    {
                        "content": [{"text": payload_text, "type": "text"}],
                        "tool_use_id": "toolu_read",
                        "type": "tool_result",
                    }
                ],
                "role": "user",
            },
            "toolUseResult": {"file": {"filePath": "/repo/big.txt"}, "type": "text"},
            "type": "user",
        }
    )

    record = compact_record(raw, line=5, source="claude-code")

    result = record.tool_results[0]
    assert result.truncated is True
    assert record.truncated is True
    assert result.raw_bytes == len(payload_text.encode("utf-8"))
    assert "FILE-HEAD" in result.preview
    assert "FILE-TAIL" in result.preview
    assert "...[trimmed]..." in result.preview


def test_claude_attachment_is_scaffolding() -> None:
    raw = json.dumps(
        {
            "attachment": {"content": "[redacted]", "isInitial": True, "type": "file"},
            "type": "attachment",
            "uuid": "00000000-0000-4000-8000-000000000001",
        }
    )

    record = compact_record(raw, line=2, source="claude-code")

    assert record.record_type == "attachment"
    assert record.role is None
    assert record.content_kinds == ()
    assert record.summary == "attachment record."


def test_claude_system_summary_uses_subtype_in_record_type() -> None:
    raw = json.dumps(
        {
            "isMeta": True,
            "subtype": "summary",
            "type": "system",
            "uuid": "00000000-0000-4000-8000-000000000004",
        }
    )

    record = compact_record(raw, line=6, source="claude-code")

    assert record.record_type == "system:summary"
    assert record.role is None
    assert record.summary == "system:summary record."


def test_claude_standalone_thinking_is_omitted_and_marked_truncated() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [
                    {"signature": "[redacted]", "thinking": "[redacted]", "type": "thinking"}
                ],
                "role": "assistant",
            },
            "type": "assistant",
        }
    )

    record = compact_record(raw, line=4, source="claude-code")

    assert record.record_type == "assistant"
    assert record.content_kinds == ("thinking",)
    assert record.text_preview is None
    assert record.tool_uses == ()
    assert record.truncated is True
    assert record.summary == "Assistant reasoning omitted."


def test_simplified_type_only_shape_derives_record_type_from_type() -> None:
    raw = '{"type":"session_metadata","content":"Prepared workspace fixture."}'

    record = compact_record(raw, line=1, source="codex")

    assert record.record_type == "session_metadata"
    assert record.role is None
    assert record.summary == "session_metadata record."


def test_codex_function_call_with_large_arguments_trims_head_only() -> None:
    arguments = "START-ARGS " + ("z" * 4000) + " END-ARGS"
    raw = json.dumps(
        {
            "payload": {
                "arguments": arguments,
                "call_id": "call_big_args",
                "name": "exec_command",
                "type": "function_call",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=11, source="codex")

    summary = record.tool_uses[0].input_summary
    assert summary.startswith("START-ARGS")
    assert "END-ARGS" not in summary
    assert summary.endswith("...[trimmed]...\n")


def test_claude_tool_result_command_kind_and_direct_file_path() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [
                    {
                        "content": "ok",
                        "tool_use_id": "toolu_bash",
                        "type": "tool_result",
                    }
                ],
                "role": "user",
            },
            "toolUseResult": {
                "command": "ls -la",
                "filePath": "/repo/file.py",
                "status": "completed",
            },
            "type": "user",
        }
    )

    record = compact_record(raw, line=5, source="claude-code")

    result = record.tool_results[0]
    assert result.kind == "file"
    assert result.file_path == "/repo/file.py"
    assert result.command == "ls -la"
    assert result.status == "completed"
    assert result.preview == "ok"


def test_claude_tool_result_command_kind_without_file_path() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [{"content": [], "tool_use_id": "toolu_bash", "type": "tool_result"}],
                "role": "user",
            },
            "toolUseResult": {"command": "pytest -q"},
            "type": "user",
        }
    )

    record = compact_record(raw, line=5, source="claude-code")

    result = record.tool_results[0]
    assert result.kind == "command"
    assert result.command == "pytest -q"
    assert result.preview == ""


def test_claude_tool_use_with_missing_input_has_empty_summary() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [{"id": "toolu_x", "name": "TodoWrite", "type": "tool_use"}],
                "role": "assistant",
            },
            "type": "assistant",
        }
    )

    record = compact_record(raw, line=4, source="claude-code")

    assert record.tool_uses[0].name == "TodoWrite"
    assert record.tool_uses[0].input_summary == ""


def test_claude_tool_use_with_string_input_keeps_string_summary() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [
                    {
                        "id": "toolu_x",
                        "input": "raw-string-input",
                        "name": "Bash",
                        "type": "tool_use",
                    }
                ],
                "role": "assistant",
            },
            "type": "assistant",
        }
    )

    record = compact_record(raw, line=4, source="claude-code")

    assert record.tool_uses[0].input_summary == "raw-string-input"


def test_claude_tool_result_with_missing_content_has_empty_preview() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [{"tool_use_id": "toolu_x", "type": "tool_result"}],
                "role": "user",
            },
            "toolUseResult": {"status": "completed"},
            "type": "user",
        }
    )

    record = compact_record(raw, line=5, source="claude-code")

    result = record.tool_results[0]
    assert result.preview == ""
    assert result.raw_bytes == 0
    assert result.truncated is False


def test_claude_message_with_missing_content_is_well_formed() -> None:
    raw = json.dumps({"message": {"role": "assistant"}, "type": "assistant"})

    record = compact_record(raw, line=4, source="claude-code")

    assert record.record_type == "assistant"
    assert record.role == "assistant"
    assert record.content_kinds == ()
    assert record.text_preview is None
    assert record.summary == "Assistant message."


def test_codex_message_without_role_summarizes_generically() -> None:
    raw = json.dumps(
        {
            "payload": {
                "content": [{"text": "system instructions", "type": "input_text"}],
                "type": "message",
            },
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=4, source="codex")

    assert record.record_type == "response_item:message"
    assert record.role is None
    assert record.text_preview == "system instructions"
    assert record.summary == "Message."


def test_codex_message_with_non_list_content_has_no_text() -> None:
    raw = json.dumps(
        {
            "payload": {"content": "not-a-list", "role": "user", "type": "message"},
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=4, source="codex")

    assert record.content_kinds == ()
    assert record.text_preview is None
    assert record.summary == "User message."


def test_codex_response_item_unknown_payload_type_is_generic() -> None:
    raw = json.dumps(
        {
            "payload": {"type": "custom_future_item"},
            "type": "response_item",
        }
    )

    record = compact_record(raw, line=4, source="codex")

    assert record.record_type == "response_item:custom_future_item"
    assert record.content_kinds == ()
    assert record.summary == "response_item:custom_future_item record."


def test_unrecognized_source_uses_generic_fallback() -> None:
    raw = json.dumps({"role": "user", "type": "message"})

    record = compact_record(raw, line=4, source="gemini")

    assert record.record_type == "user"
    assert record.role == "user"
    assert record.summary == "user record."


def test_to_json_round_trips_through_json_dumps() -> None:
    raw = '{"role":"user","content":"hi"}'

    payload = compact_record_to_json(compact_record(raw, line=3, source="codex"))

    assert json.loads(json.dumps(payload)) == payload
    assert payload["line"] == 3
    assert payload["record_type"] == "user"
    assert payload["content_kinds"] == []


def test_to_json_serializes_tool_uses_and_tool_results() -> None:
    raw = json.dumps(
        {
            "message": {
                "content": [
                    {
                        "id": "toolu_x",
                        "input": {"a": 1},
                        "name": "Bash",
                        "type": "tool_use",
                    },
                    {
                        "content": "ok",
                        "tool_use_id": "toolu_x",
                        "type": "tool_result",
                    },
                ],
                "role": "assistant",
            },
            "toolUseResult": {"status": "completed"},
            "type": "assistant",
        }
    )

    payload = compact_record_to_json(compact_record(raw, line=4, source="claude-code"))

    assert json.loads(json.dumps(payload)) == payload
    assert payload["tool_uses"] == [{"name": "Bash", "input_summary": '{"a": 1}'}]
    assert payload["tool_results"][0]["kind"] == "tool_result"
    assert payload["tool_results"][0]["status"] == "completed"
    assert payload["tool_results"][0]["preview"] == "ok"
