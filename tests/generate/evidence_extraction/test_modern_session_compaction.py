from __future__ import annotations

import json
import shlex
from typing import Any

import pytest

from prompt_diary.generate.evidence_extraction.session_compaction import (
    compact_record,
    compact_record_to_json,
)


def _compact(payload: dict[str, object], *, event: bool = False) -> dict[str, Any]:
    raw = json.dumps(
        {"type": "event_msg" if event else "response_item", "payload": payload},
        ensure_ascii=False,
    )
    record = compact_record(raw, line=17, source="codex")
    assert record is not None
    result = compact_record_to_json(record)
    assert result["line"] == 17
    return result


def _completed_item(item: dict[str, object]) -> dict[str, Any]:
    return _compact({"type": "item_completed", "item": item}, event=True)


def test_custom_tool_call_preserves_patch_input_without_inventing_a_result() -> None:
    patch = "*** Begin Patch\n*** Update File: report.py\n@@\n-old\n+new\n*** End Patch"

    record = _compact(
        {"type": "custom_tool_call", "call_id": "call-1", "name": "apply_patch", "input": patch}
    )

    assert record["tool_uses"][0]["name"] == "apply_patch"
    assert record["tool_uses"][0]["input_summary"] == patch
    assert not record.get("tool_results")


def test_custom_tool_output_preserves_multiple_text_blocks_and_blank_parts() -> None:
    record = _compact(
        {
            "type": "custom_tool_call_output",
            "call_id": "call-1",
            "output": [
                {"type": "input_text", "text": "Applied the source patch."},
                {"type": "input_text", "text": ""},
                {"type": "input_text", "text": " "},
                {"type": "input_text", "text": "The test-file patch failed."},
            ],
        }
    )

    assert record["tool_results"][0]["preview"] == (
        "Applied the source patch.\n\n \nThe test-file patch failed."
    )
    assert not record.get("truncated", False)


def test_human_message_preserves_exact_large_text_and_blank_parts() -> None:
    first = "Preserve 中文, punctuation, and whitespace.\n" * 1000
    parts = [first, "", " ", "Do not claim tests passed before running them."]

    record = _compact(
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text} for text in parts],
        }
    )

    assert record["text"] == "\n".join(parts)
    assert not record.get("truncated", False)
    assert not record.get("tool_results")


def test_command_invocation_does_not_become_observed_test_success() -> None:
    command = "python -m pytest tests/test_report.py"

    record = _compact(
        {
            "type": "function_call",
            "call_id": "call-2",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": command}),
        }
    )

    assert record["tool_uses"][0]["name"] == "exec_command"
    assert json.loads(record["tool_uses"][0]["input_summary"])["cmd"] == command
    assert not record.get("tool_results")


@pytest.mark.parametrize(
    ("status", "exit_code", "stdout", "stderr"),
    [
        ("completed", 0, "8 passed in 0.12s\n", ""),
        ("failed", 2, "collected 0 items\n", "ERROR: test file not found\n"),
    ],
)
def test_command_completion_retains_actual_exit_status_and_separate_error_output(
    status: str, exit_code: int, stdout: str, stderr: str
) -> None:
    command = ["python", "-m", "pytest", "tests/test report.py"]

    record = _completed_item(
        {
            "type": "CommandExecution",
            "id": "command-1",
            "command": command,
            "status": status,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        }
    )

    result = record["tool_results"][0]
    assert result["kind"] == "command"
    assert shlex.split(result["command"]) == command
    assert result["status"] == status
    assert result["exit_code"] == exit_code
    assert result["preview"] == stdout
    assert result.get("stderr", "") == stderr


def test_file_change_completion_preserves_each_path_operation_and_output() -> None:
    record = _completed_item(
        {
            "type": "FileChange",
            "id": "patch-1",
            "status": "completed",
            "changes": {
                "src/new.py": {"type": "add", "content": "print('new')\n"},
                "src/report.py": {"type": "update", "unified_diff": "-old\n+new\n"},
                "src/obsolete.py": {"type": "delete", "content": "old\n"},
            },
            "output": (
                "Success. Updated the following files:\n"
                "A src/new.py\nM src/report.py\nD src/obsolete.py"
            ),
        }
    )

    result = record["tool_results"][0]
    assert result["kind"] == "file_change"
    assert result["status"] == "completed"
    assert {(change["path"], change["operation"]) for change in result["changes"]} == {
        ("src/new.py", "add"),
        ("src/report.py", "update"),
        ("src/obsolete.py", "delete"),
    }
    assert "A src/new.py\nM src/report.py\nD src/obsolete.py" in result["preview"]


def test_mcp_completion_keeps_all_text_result_blocks() -> None:
    record = _completed_item(
        {
            "type": "McpToolCall",
            "id": "mcp-1",
            "server": "workspace",
            "tool": "inspect_artifact",
            "status": "completed",
            "result": {
                "content": [
                    {"type": "text", "text": "The artifact exists."},
                    {"type": "text", "text": "Its checksum does not match the expected value."},
                ],
                "isError": False,
            },
        }
    )

    result = record["tool_results"][0]
    assert result["kind"] == "mcp"
    assert result["status"] == "completed"
    assert result["preview"] == (
        "The artifact exists.\nIts checksum does not match the expected value."
    )


def test_mcp_failure_keeps_structured_error_evidence() -> None:
    record = _completed_item(
        {
            "type": "McpToolCall",
            "id": "mcp-2",
            "server": "workspace",
            "tool": "inspect_artifact",
            "status": "failed",
            "result": None,
            "error": {"code": 403, "message": "Artifact access denied."},
        }
    )

    result = record["tool_results"][0]
    assert result["kind"] == "mcp"
    assert result["status"] == "failed"
    assert "403" in result["error"]
    assert "Artifact access denied." in result["error"]


@pytest.mark.parametrize("content", [[], [{"type": "text", "text": "Verification ran."}]])
def test_mcp_structured_result_remains_evidence_alongside_optional_text(
    content: list[dict[str, str]],
) -> None:
    record = _completed_item(
        {
            "type": "McpToolCall",
            "tool": "verify",
            "status": "completed",
            "result": {"content": content, "structuredContent": {"failed_checks": 3}},
        }
    )

    preview = record["tool_results"][0]["preview"]
    assert "failed_checks" in preview
    assert "3" in preview
    if content:
        assert "Verification ran." in preview


def test_mcp_error_result_is_not_mistaken_for_successful_tool_execution() -> None:
    record = _completed_item(
        {
            "type": "McpToolCall",
            "tool": "verify",
            "status": "completed",
            "result": {
                "content": [{"type": "text", "text": "Cannot open the artifact."}],
                "isError": True,
            },
        }
    )

    result = record["tool_results"][0]
    assert result["status"] == "failed"
    assert result["preview"] == "Cannot open the artifact."


@pytest.mark.parametrize("item_type", ["CommandExecution", "FileChange"])
def test_nullable_primary_output_does_not_hide_alternate_observed_output(item_type: str) -> None:
    item: dict[str, object] = {"type": item_type, "status": "failed", "stdout": None}
    if item_type == "CommandExecution":
        item.update(command=["pytest"], exit_code=1, aggregated_output="2 failed, 6 passed")
        expected = "2 failed, 6 passed"
    else:
        item.update(changes={}, output="Patch rejected: context did not match.")
        expected = "Patch rejected: context did not match."

    record = _completed_item(item)

    assert record["tool_results"][0]["preview"] == expected


@pytest.mark.parametrize("source", ["codex", "claude-code"])
@pytest.mark.parametrize("include_null_content", [False, True])
def test_missing_message_content_stays_visible_as_unavailable_source_evidence(
    source: str, *, include_null_content: bool
) -> None:
    message: dict[str, object] = {"role": "user"}
    if include_null_content:
        message["content"] = None
    raw = (
        {"type": "response_item", "payload": {"type": "message", **message}}
        if source == "codex"
        else {"type": "user", "message": message}
    )

    record = compact_record(json.dumps(raw), line=23, source=source)

    assert record is not None
    result = compact_record_to_json(record)
    assert result["line"] == 23
    assert result["kind"] == "unknown"
    assert "user" in result["text"]
