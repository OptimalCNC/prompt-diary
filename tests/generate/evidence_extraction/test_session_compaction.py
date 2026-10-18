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
    ],
)
def test_known_codex_metadata_emits_nothing(kind: str) -> None:
    assert compact_record(json.dumps({"type": kind, "payload": {}}), line=1, source="codex") is None


@pytest.mark.parametrize(
    "kind", ["token_count", "task_started", "thread_settings_applied", "task_complete"]
)
def test_known_codex_lifecycle_noise_emits_nothing(kind: str) -> None:
    assert (
        compact_record(
            json.dumps({"type": "event_msg", "payload": {"type": kind}}), line=1, source="codex"
        )
        is None
    )


@pytest.mark.parametrize("kind", ["task_complete", "error", "task_failed"])
def test_codex_conversation_errors_are_omitted_from_compact_evidence(kind: str) -> None:
    raw = json.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": kind,
                "message": "unexpected status 503 Service Unavailable: Maintenance",
                "error": {
                    "message": "unexpected status 503 Service Unavailable: Maintenance",
                    "codex_error_info": {"http_connection_failed": {"http_status_code": 503}},
                },
            },
        }
    )
    assert compact_record(raw, line=7, source="codex") is None


@pytest.mark.parametrize(
    "item",
    [
        {"type": "Reasoning"},
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
        {"type": "turn_aborted", "reason": "interrupted"},
        {"type": "future_event", "message": "unknown failure"},
    ],
)
def test_explicit_interruptions_and_unknown_events_are_not_discarded(
    payload: dict[str, Any],
) -> None:
    record = _codex(payload, "event_msg")
    assert record.kind in ("terminal", "unknown")
    assert record.text


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("text", ["", " \n ", "Long exact content.\n" * 10_000])
def test_codex_messages_preserve_exact_text_and_omit_optional_fields(role: str, text: str) -> None:
    record = _codex(
        {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}
    )
    assert compact_record_to_json(record) == {
        "line": 7,
        "kind": "human_input" if role == "user" else "assistant_message",
        "text": text,
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"type": "user_message", "message": "Please proceed."}, "human_input"),
        ({"type": "agent_message", "message": "Checks failed."}, "assistant_message"),
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
    wire = compact_record_to_json(record)
    assert wire["parts"] == [
        {"kind": "human_attachment", "source_type": "input_image"},
        {"kind": "unknown", "unavailable": "unknown content"},
        {"kind": "unknown", "unavailable": "unknown content"},
    ]
    assert "binary" not in json.dumps(wire)
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


@pytest.mark.parametrize("subtype", ["turn_duration", "stop_hook_summary"])
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
    wire = compact_record_to_json(record)
    assert wire["kind"] == "mixed"
    assert "text" not in wire
    assert wire["parts"] == [
        {"kind": "assistant_message", "text": "Checking."},
        {"kind": "tool_call", "tool_use": {"name": "Read", "input_summary": '{"path": "x.py"}'}},
        {
            "kind": "tool_result",
            "tool_result": {"kind": "output", "status": "failed", "preview": "permission denied"},
        },
        {"kind": "unknown", "unavailable": "image"},
    ]
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


def test_human_skill_selection_and_expanded_instructions_have_distinct_origins() -> None:
    human = "  Use $review; only inspect the API.\n"
    skill = "Follow every instruction in this skill.\n" * 100
    record = _codex(
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": human},
                {"type": "input_text", "text": skill},
                {"type": "input_text", "text": "Do not publish."},
            ],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": [
                    "user.text",
                    "skills.selected_skill_instructions",
                    "user.text",
                ]
            },
        }
    )

    assert compact_record_to_json(record) == {
        "line": 7,
        "kind": "mixed",
        "parts": [
            {"kind": "human_input", "text": human, "source_tag": "user.text"},
            {
                "kind": "skill_context",
                "text": skill,
                "source_tag": "skills.selected_skill_instructions",
            },
            {"kind": "human_input", "text": "Do not publish.", "source_tag": "user.text"},
        ],
    }


def test_native_skill_selection_does_not_invent_a_human_quote() -> None:
    record = _codex(
        {
            "type": "item_completed",
            "item": {"type": "UserMessage", "content": [{"type": "skill", "name": "review"}]},
        },
        "event_msg",
    )

    assert compact_record_to_json(record) == {
        "line": 7,
        "kind": "skill_invocation",
        "name": "review",
    }


def test_human_attachment_is_visible_without_exposing_its_payload_or_inventing_text() -> None:
    record = _codex(
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_image", "image_url": "data:image/png;base64,binary"}],
        }
    )

    assert compact_record_to_json(record) == {
        "line": 7,
        "kind": "human_attachment",
        "source_type": "input_image",
    }


def test_question_wrapper_preserves_agent_question_and_exact_human_answer_separately() -> None:
    question, answer = "Should we deploy?", "  No, test first.\n"
    wrapper = (
        "<send_user_message_question_reply>"
        + json.dumps([{"questionItemId": "question-id", "question": question, "answer": answer}])
        + "</send_user_message_question_reply>"
    )
    record = _codex(
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": wrapper}]}
    )

    assert compact_record_to_json(record) == {
        "line": 7,
        "kind": "mixed",
        "parts": [
            {
                "kind": "assistant_message",
                "text": question,
                "source_tag": "question_reply.question",
            },
            {"kind": "human_input", "text": answer, "source_tag": "question_reply.answer"},
        ],
    }


def test_user_shell_command_and_result_are_absent_from_compact_evidence() -> None:
    output = "start\n" + "x" * 2000 + "\nfinished"
    wrapper = (
        "<user_shell_command><command> pwd </command>"
        f"<result>{output}</result></user_shell_command>"
    )
    raw = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": wrapper}],
            },
        }
    )

    assert compact_record(raw, line=7, source="codex") is None


def test_shell_filter_preserves_adjacent_direction_and_agent_work() -> None:
    wrapper = (
        "<user_shell_command><command>git status</command>"
        "<result>clean</result></user_shell_command>"
    )
    record = _codex(
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": wrapper},
                {"type": "input_text", "text": "Please run `git status` and explain it."},
            ],
        }
    )
    assert record.kind == "human_input"
    assert record.text == "Please run `git status` and explain it."
    agent = _codex(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": wrapper}],
        }
    )
    assert agent.kind == "assistant_message"
    assert agent.text == wrapper


@pytest.mark.parametrize(
    "text",
    [
        "<send_user_message_question_reply>broken</send_user_message_question_reply>",
        "<skill>this may be quoted text</skill>",
    ],
)
def test_uncertain_user_shaped_wrappers_are_visible_without_claiming_human_authorship(
    text: str,
) -> None:
    record = _codex(
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
    )

    assert record.kind == "unknown"
    assert record.text == text


def test_claude_tool_owned_text_is_not_human_input() -> None:
    record = _record(
        {
            "type": "user",
            "sourceToolAssistantUUID": "tool-owner",
            "message": {"role": "user", "content": "User has approved your plan."},
        },
        "claude-code",
    )

    assert record.kind == "tool_result"
    assert record.tool_results[0].preview == "User has approved your plan."


def test_claude_mixed_tool_result_and_human_correction_preserves_source_order() -> None:
    record = _record(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": "Deployment complete."},
                    {"type": "text", "text": " That was the wrong target.\n"},
                ],
            },
        },
        "claude-code",
    )

    assert compact_record_to_json(record) == {
        "line": 7,
        "kind": "mixed",
        "parts": [
            {
                "kind": "tool_result",
                "tool_result": {"kind": "output", "preview": "Deployment complete."},
            },
            {"kind": "human_input", "text": " That was the wrong target.\n"},
        ],
    }


def test_claude_slash_command_separates_human_action_arguments_and_generated_message() -> None:
    wrapper = (
        "<command-name>/review</command-name>\n"
        "<command-message>Skill instructions from the client.</command-message>\n"
        "<command-args> only this patch\n</command-args>"
    )
    wire = compact_record_to_json(
        _record({"type": "user", "message": {"content": wrapper}}, "claude-code")
    )

    assert wire == {
        "line": 7,
        "kind": "mixed",
        "parts": [
            {"kind": "human_command", "text": "/review", "source_tag": "claude.command_name"},
            {
                "kind": "generated_context",
                "text": "Skill instructions from the client.",
                "source_tag": "claude.command_message",
            },
            {
                "kind": "human_input",
                "text": " only this patch\n",
                "source_tag": "claude.command_args",
            },
        ],
    }


def test_claude_local_command_output_is_absent_from_compact_evidence() -> None:
    raw = json.dumps(
        {
            "type": "user",
            "message": {"content": "<local-command-stdout>Updated plugin.</local-command-stdout>"},
        }
    )
    assert compact_record(raw, line=7, source="claude-code") is None


def test_claude_explicit_system_local_command_is_absent_from_compact_evidence() -> None:
    raw = json.dumps({"type": "system", "subtype": "local_command", "content": "Updated model."})
    assert compact_record(raw, line=7, source="claude-code") is None


def test_claude_malformed_command_wrapper_is_not_labeled_human_input() -> None:
    wrapper = "<command-name>/review</command-name> unrecognized trailing text"
    record = _record({"type": "user", "message": {"content": wrapper}}, "claude-code")

    assert record.kind == "unknown"
    assert record.text == wrapper


def test_claude_human_image_has_no_fabricated_transcription() -> None:
    record = _record(
        {"type": "user", "message": {"content": [{"type": "image", "source": {"data": "binary"}}]}},
        "claude-code",
    )

    assert compact_record_to_json(record) == {
        "line": 7,
        "kind": "human_attachment",
        "source_type": "image",
    }


@pytest.mark.parametrize("content", ["The review is complete.", None])
def test_native_agent_message_without_echo_preserves_text_or_missing_content(
    content: str | None,
) -> None:
    record = _codex(
        {"type": "item_completed", "item": {"type": "AgentMessage", "content": content}},
        "event_msg",
    )

    if content is None:
        assert record.kind == "unknown"
        assert record.text is not None
        assert '"content": null' in record.text
    else:
        assert record.kind == "assistant_message"
        assert record.text == content


def test_claude_tool_call_without_prose_remains_an_invocation_not_an_outcome() -> None:
    record = _record(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Read", "input": {"path": "x.py"}}]
            },
        },
        "claude-code",
    )

    assert record.kind == "tool_call"
    assert record.tool_uses[0].name == "Read"
    assert record.tool_results == ()


def test_claude_unrecognized_block_does_not_merge_with_human_text() -> None:
    record = _record(
        {"type": "user", "message": {"content": [42, {"type": "text", "text": "Please review."}]}},
        "claude-code",
    )

    assert compact_record_to_json(record)["parts"] == [
        {"kind": "unknown", "unavailable": "42"},
        {"kind": "human_input", "text": "Please review."},
    ]
