from __future__ import annotations

import json
from typing import Any, cast

import pytest

from prompt_diary.generate.evidence_extraction.session_compaction import (
    CompactRecord,
    compact_record,
    compact_record_to_json,
)


def _record(value: object, source: str = "codex") -> CompactRecord:
    result = compact_record(json.dumps(value), line=7, source=source)
    assert result is not None
    return result


def _codex(payload: object, kind: str = "response_item") -> CompactRecord:
    return _record({"type": kind, "payload": payload})


@pytest.mark.parametrize("raw", ["not JSON", "[1, 2, 3]", "null", ""])
def test_malformed_lines_remain_visible_at_their_physical_line(raw: str) -> None:
    record = compact_record(raw, line=4, source="codex")
    assert record is not None
    assert (record.line, record.kind, record.text) == (4, "malformed", raw)


@pytest.mark.parametrize(
    "value",
    [
        {"type": "future", "payload": {"message": "failure"}},
        {"type": "missing-payload"},
        {"role": "user", "text": "uncertain source"},
    ],
)
@pytest.mark.parametrize("source", ["codex", "claude-code", "future-source"])
def test_unknown_records_keep_a_useful_fallback(value: object, source: str) -> None:
    record = _record(value, source)
    assert record.kind == "unknown"
    assert record.text


@pytest.mark.parametrize("source_type", ["future_result", None])
def test_unknown_codex_response_payload_keeps_source_type_and_payload(
    source_type: str | None,
) -> None:
    record = _codex({"type": source_type, "error": "permission denied"})
    assert record.kind == "unknown"
    assert record.text is not None
    assert "permission denied" in record.text
    assert record.source_type == source_type


@pytest.mark.parametrize(
    "kind",
    [
        "session_meta",
        "turn_context",
        "token_usage_record",
        "world_state",
        "inter_agent_communication_metadata",
        "compacted",
    ],
)
def test_known_codex_metadata_emits_nothing(kind: str) -> None:
    assert compact_record(json.dumps({"type": kind, "payload": {}}), line=1, source="codex") is None


@pytest.mark.parametrize("kind", ["token_count", "task_started", "thread_settings_applied"])
def test_known_codex_lifecycle_noise_emits_nothing(kind: str) -> None:
    assert (
        compact_record(
            json.dumps({"type": "event_msg", "payload": {"type": kind}}), line=1, source="codex"
        )
        is None
    )


@pytest.mark.parametrize(
    "item",
    [
        {"type": "Reasoning"},
        {"type": "ContextCompaction"},
        {"type": "SubAgentActivity", "kind": "started"},
        {"type": "SubAgentActivity", "kind": "interacted"},
        {"type": "SubAgentActivity", "kind": "completed"},
        {"type": "Extension", "kind": "clock.sleep"},
    ],
)
def test_known_completed_items_without_work_evidence_are_omitted(item: dict[str, str]) -> None:
    raw = json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": item}})
    assert compact_record(raw, line=1, source="codex") is None


@pytest.mark.parametrize(
    "item",
    [
        {"type": "SubAgentActivity", "kind": "failed", "error": "agent failed"},
        {"type": "Extension", "kind": "future", "error": "extension failed"},
        None,
    ],
)
def test_unknown_completed_items_and_failures_are_visible(item: object) -> None:
    record = _codex({"type": "item_completed", "item": item}, "event_msg")
    assert record.kind == "unknown"
    assert record.text


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "task_complete"},
        {"type": "turn_aborted", "reason": "interrupted"},
        {"type": "error", "message": "disconnected"},
        {"type": "task_failed", "error": {"code": 403, "message": "denied"}},
        {"type": "future_event", "message": "unknown failure"},
    ],
)
def test_terminal_failures_and_unknown_events_are_not_discarded(payload: dict[str, Any]) -> None:
    record = _codex(payload, "event_msg")
    assert record.kind in ("terminal", "unknown")
    assert record.text


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("text", ["", " \n ", "Long exact content.\n" * 10_000])
def test_codex_messages_preserve_exact_text_and_omit_optional_fields(role: str, text: str) -> None:
    record = _codex(
        {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}
    )
    assert compact_record_to_json(record) == {"line": 7, "kind": role, "text": text}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"type": "user_message", "message": "Please proceed."}, "user"),
        ({"type": "agent_message", "message": "Checks failed."}, "assistant"),
    ],
)
def test_legacy_codex_messages_remain_supported(payload: dict[str, str], expected: str) -> None:
    record = _codex(payload, "event_msg")
    assert (record.kind, record.text) == (expected, payload["message"])


def test_codex_reasoning_and_claude_reasoning_only_messages_are_omitted() -> None:
    assert (
        compact_record(
            '{"type":"response_item","payload":{"type":"reasoning","summary":"private"}}',
            line=1,
            source="codex",
        )
        is None
    )
    assert (
        compact_record(
            '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"private"},{"type":"redacted_thinking"}]}}',
            line=1,
            source="claude-code",
        )
        is None
    )


def test_unknown_message_content_remains_visible_without_exposing_binary_payloads() -> None:
    record = _codex(
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_image", "data": "binary"}, {"x": 1}, 42],
        }
    )
    assert record.unavailable == ("input_image", "unknown content", "42")
    assert "binary" not in json.dumps(compact_record_to_json(record))
    assert _codex({"type": "message", "content": {"new": "format"}}).unavailable


def test_subagent_message_exposes_readable_text_and_marks_encrypted_parts() -> None:
    record = _codex(
        {
            "type": "agent_message",
            "content": [
                {"type": "input_text", "text": "Agent reported:"},
                {"type": "encrypted_content", "encrypted_content": "ciphertext"},
            ],
        }
    )
    assert record.kind == "subagent_message"
    assert record.text == "Agent reported:"
    assert record.unavailable == ("encrypted_content",)
    assert "ciphertext" not in json.dumps(compact_record_to_json(record))


@pytest.mark.parametrize("arguments", [None, {"command": "pwd"}, "", "x" * 400, "漢" * 1000])
def test_legacy_tool_call_input_is_bounded_and_never_becomes_an_outcome(arguments: object) -> None:
    record = _codex({"type": "function_call", "name": "shell", "arguments": arguments})
    assert record.kind == "tool_call"
    assert record.tool_results == ()
    assert len(record.tool_uses[0].input_summary.encode()) <= 350


def test_missing_tool_name_does_not_discard_its_arguments() -> None:
    record = _codex({"type": "function_call", "arguments": "command"})
    assert record.tool_uses[0].name == "unknown"
    assert record.tool_uses[0].input_summary == "command"


@pytest.mark.parametrize(
    "output",
    [
        "",
        None,
        {"exit_code": 0, "passed": True},
        [
            {"type": "text", "text": "first"},
            {"type": "text", "text": ""},
            {"type": "text", "text": "last"},
        ],
        [{"type": "image", "data": "binary"}, 5],
    ],
)
def test_legacy_tool_results_support_structured_and_list_output(output: object) -> None:
    record = _codex({"type": "function_call_output", "output": output})
    assert record.kind == "tool_result"
    assert len(record.tool_results) == 1
    assert "binary" not in record.tool_results[0].preview


@pytest.mark.parametrize("text", ["x" * 1024, "x" * 1025, "漢" * 1000, "\ud800" * 2000])
def test_output_budget_preserves_short_results_and_both_ends_of_large_results(text: str) -> None:
    result = _codex({"type": "function_call_output", "output": text}).tool_results[0]
    if len(text.encode("utf-8", errors="backslashreplace")) <= 1024:
        assert result.preview == text
        assert not result.truncated
    else:
        assert result.truncated
        assert "...[trimmed]..." in result.preview
        assert len(result.preview.encode()) < 600


@pytest.mark.parametrize("subtype", ["compact_boundary", "turn_duration", "stop_hook_summary"])
def test_claude_recognized_system_noise_is_omitted(subtype: str) -> None:
    assert (
        compact_record(
            json.dumps({"type": "system", "subtype": subtype}), line=1, source="claude-code"
        )
        is None
    )


def test_claude_unknown_system_records_remain_visible() -> None:
    record = _record({"type": "system", "subtype": "error", "error": "hook failed"}, "claude-code")
    assert record.kind == "unknown"
    assert record.text is not None
    assert "hook failed" in record.text


def test_claude_mixed_content_keeps_text_calls_results_and_unavailable_parts() -> None:
    record = _record(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "name": "Read", "input": {"path": "x.py"}},
                    {"type": "tool_result", "content": "permission denied", "is_error": True},
                    {"type": "thinking", "thinking": "private"},
                    {"type": "image"},
                ],
            },
        },
        "claude-code",
    )
    assert record.text == "Checking."
    assert record.tool_uses[0].name == "Read"
    assert record.tool_results[0].status == "failed"
    assert record.tool_results[0].preview == "permission denied"
    assert record.unavailable == ("image",)
    wire = compact_record_to_json(record)
    assert wire["tool_uses"][0]["input_summary"] == '{"path": "x.py"}'
    assert "private" not in json.dumps(wire)


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"filePath": "/project/x", "status": "completed"}, "file"),
        ({"file": {"filePath": "/project/y"}}, "file"),
        ({"command": "pytest", "exitCode": 0}, "command"),
        ({"command": "x" * 2000, "exit_code": 1}, "command"),
        ({"exitCode": True}, "output"),
        ({}, "output"),
    ],
)
def test_claude_result_metadata_preserves_observable_file_and_command_facts(
    metadata: dict[str, object], expected: str
) -> None:
    record = _record(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "completed output"}]},
            "toolUseResult": metadata,
        },
        "claude-code",
    )
    assert record.kind == "tool_result"
    result = record.tool_results[0]
    assert result.kind == expected
    assert result.preview == "completed output"
    if metadata.get("exitCode") == 0:
        assert compact_record_to_json(record)["tool_results"][0]["exit_code"] == 0


@pytest.mark.parametrize("command", [None, "pytest", ["pytest", "-q"], ["pytest", 4]])
def test_command_execution_preserves_unknown_command_shapes(command: object) -> None:
    record = _codex(
        {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "command": command,
                "status": "failed",
                "exit_code": True,
                "error": {"message": "failed"},
            },
        },
        "event_msg",
    )
    assert record.tool_results[0].error is not None
    assert record.tool_results[0].exit_code is None


@pytest.mark.parametrize(
    "changes", [[{"path": "x.py", "kind": {"type": "update"}}, {}, 1], {"x.py": None}, None]
)
def test_file_change_shapes_preserve_paths_without_fabricating_success(changes: object) -> None:
    record = _codex(
        {
            "type": "item_completed",
            "item": {
                "type": "FileChange",
                "changes": changes,
                "status": "failed",
                "stderr": "write denied",
                "error": "disk full",
            },
        },
        "event_msg",
    )
    result = record.tool_results[0]
    assert result.status == "failed"
    assert result.stderr == "write denied"
    assert result.error == "disk full"


@pytest.mark.parametrize(
    "result",
    [
        None,
        "raw result",
        {"value": "structured"},
        {"content": [{"type": "text", "text": "tool failed"}], "isError": True},
    ],
)
def test_mcp_result_fallbacks_and_error_flag_remain_visible(result: object) -> None:
    record = _codex(
        {
            "type": "item_completed",
            "item": {
                "type": "McpToolCall",
                "tool": "check",
                "status": "completed",
                "result": result,
            },
        },
        "event_msg",
    )
    output = record.tool_results[0]
    assert output.name == "check"
    if isinstance(result, dict) and cast("dict[str, Any]", result).get("isError"):
        assert output.status == "failed"
        assert output.preview == "tool failed"


def test_sparse_serialization_does_not_retain_audit_hashes_or_ids() -> None:
    wire = compact_record_to_json(
        _codex({"type": "function_call_output", "call_id": "routing-id", "output": "done"})
    )
    assert wire == {
        "line": 7,
        "kind": "tool_result",
        "tool_results": [{"kind": "output", "preview": "done"}],
    }


def test_claude_missing_message_remains_an_unknown_record() -> None:
    record = _record({"type": "user", "message": None}, "claude-code")
    assert record.kind == "unknown"
    assert record.text is not None


@pytest.mark.parametrize(
    "changes",
    [
        {
            "old.py": {
                "type": "update",
                "move_path": "new.py",
                "unified_diff": "-old\n+new",
            },
            "large.py": {"type": "add", "content": "begin\n" + "x" * 2000 + "\nend"},
        },
        [
            {
                "path": "old.py",
                "kind": {"type": "update", "move_path": "new.py"},
                "diff": "-old\n+new",
            },
            {
                "path": "large.py",
                "kind": {"type": "add"},
                "diff": "begin\n" + "x" * 2000 + "\nend",
            },
        ],
    ],
)
def test_file_change_content_and_rename_target_remain_inspectable(changes: object) -> None:
    record = _codex(
        {
            "type": "item_completed",
            "item": {
                "type": "FileChange",
                "status": "completed",
                "changes": changes,
            },
        },
        "event_msg",
    )
    result = record.tool_results[0]
    assert (result.changes[0].path, result.changes[0].operation) == ("old.py", "update")
    assert result.changes[0].move_path == "new.py"
    assert result.changes[0].preview == "-old\n+new"
    assert result.changes[1].truncated
    assert result.changes[1].preview.startswith("begin\n")
    assert result.changes[1].preview.endswith("\nend")
    assert result.truncated


@pytest.mark.parametrize(
    "item",
    [
        {"type": "SubAgentActivity", "kind": "completed", "message": "review found a defect"},
        {"type": "SubAgentActivity", "kind": "started", "error": "launch failed"},
        {"type": "Extension", "kind": "clock.sleep", "error": "interrupted"},
    ],
)
def test_lifecycle_items_with_results_or_errors_are_not_treated_as_empty_metadata(
    item: dict[str, str],
) -> None:
    record = _codex({"type": "item_completed", "item": item}, "event_msg")
    assert record.kind == "unknown"
    assert record.text is not None
    assert item.get("message", item.get("error", "")) in record.text
