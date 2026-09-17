"""Deterministically expose session evidence while omitting recognized source scaffolding.

Physical line numbers locate evidence in the original transcript. Human and assistant text is
preserved exactly; tool output is bounded and explicitly marked when reduced. Unknown records
remain visible. Recognized metadata returns ``None`` so the reader can advance without emitting it.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from prompt_diary.source_records import (
    is_codex_message_echo,
    is_codex_pre_trigger_context,
    parse_codex_message,
)

SHORT_TOOL_RESULT_BYTES = 1024
PREVIEW_HEAD_BYTES = 320
PREVIEW_TAIL_BYTES = 160
_PREVIEW_ELISION = "\n...[trimmed]...\n"
_CODEX_METADATA = frozenset(
    {
        "session_meta",
        "turn_context",
        "token_usage_record",
        "world_state",
        "inter_agent_communication_metadata",
        "compacted",
    }
)
_CODEX_EVENT_METADATA = frozenset({"token_count", "task_started", "thread_settings_applied"})
_CODEX_ITEM_METADATA = frozenset({"Reasoning", "ContextCompaction"})


@dataclass(frozen=True)
class ToolUse:
    """A tool invocation with a bounded input preview."""

    name: str
    input_summary: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class FileChange:
    """A source-reported file operation; paths remain intact for later inspection."""

    path: str
    operation: str
    preview: str = ""
    move_path: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ToolResult:
    """Observable tool evidence; completion status and exit code have separate meanings."""

    kind: str
    status: str | None = None
    file_path: str | None = None
    command: str | None = None
    preview: str = ""
    truncated: bool = False
    exit_code: int | None = None
    stderr: str | None = None
    error: str | None = None
    changes: tuple[FileChange, ...] = ()
    name: str | None = None


@dataclass(frozen=True)
class CompactRecord:
    """Retained evidence for one physical source line, without transport or audit metadata."""

    line: int
    kind: str
    text: str | None = None
    tool_uses: tuple[ToolUse, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    truncated: bool = False
    duplicate_of: int | None = None
    source_type: str | None = None
    unavailable: tuple[str, ...] = ()


@dataclass
class _Parts:
    texts: list[str] = field(default_factory=list)
    calls: list[ToolUse] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    @property
    def text(self) -> str | None:
        return "\n".join(self.texts) if self.texts else None


def compact_record(
    raw_line: str,
    *,
    line: int,
    source: str,
    previous_line: str | None = None,
    next_line: str | None = None,
) -> CompactRecord | None:
    """Normalize one line; known metadata is omitted and confirmed message echoes are linked."""
    record = _parse_object(raw_line)
    if record is None:
        return _fallback(line, "malformed", raw_line)
    if source == "codex":
        duplicate = _codex_duplicate_of(record, line, previous_line, next_line)
        if duplicate is not None:
            return CompactRecord(line=line, kind="duplicate", duplicate_of=duplicate)
        return _compact_codex(record, line)
    if source == "claude-code":
        return _compact_claude(record, line)
    return _fallback(line, "unknown", record, _string(record, "type"))


def compact_record_to_json(record: CompactRecord) -> dict[str, Any]:
    """Emit evidence fields only; preserve meaningful empty message text and zero exit codes."""
    result = _sparse(asdict(record))
    if record.text is not None:
        result["text"] = record.text
    return result


def _sparse(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if item is None or item is False or item == "" or item == () or item == []:
            continue
        if isinstance(item, (list, tuple)):
            result[key] = [
                _sparse(cast("dict[str, Any]", part)) if isinstance(part, dict) else part
                for part in cast("list[Any] | tuple[Any, ...]", item)
            ]
        else:
            result[key] = item
    return result


def _compact_codex(record: dict[str, Any], line: int) -> CompactRecord | None:
    record_type = _string(record, "type")
    if record_type in _CODEX_METADATA:
        return None
    payload = _object(record.get("payload"))
    if payload is None:
        return _fallback(line, "unknown", record, record_type)
    subtype = _string(payload, "type")
    if record_type == "event_msg":
        return _codex_event(payload, line)
    if record_type != "response_item":
        return _fallback(line, "unknown", payload, record_type)
    if subtype == "reasoning":
        return None
    if subtype == "message":
        return _message(line, _string(payload, "role"), payload.get("content"))
    if subtype in ("function_call", "custom_tool_call"):
        value = payload.get("arguments") if subtype == "function_call" else payload.get("input")
        return CompactRecord(line=line, kind="tool_call", tool_uses=(_tool_use(payload, value),))
    if subtype in ("function_call_output", "custom_tool_call_output"):
        return CompactRecord(
            line=line, kind="tool_result", tool_results=(_tool_result(payload.get("output")),)
        )
    if subtype == "agent_message":
        parts = _content_parts(payload.get("content"))
        preview, truncated = _preview(parts.text or "")
        return CompactRecord(
            line=line,
            kind="subagent_message",
            text=preview or None,
            truncated=truncated,
            unavailable=tuple(parts.unavailable),
        )
    return _fallback(line, "unknown", payload, subtype)


def _codex_event(payload: dict[str, Any], line: int) -> CompactRecord | None:
    subtype = _string(payload, "type")
    if subtype in _CODEX_EVENT_METADATA:
        return None
    if subtype in ("user_message", "agent_message"):
        role = "user" if subtype == "user_message" else "assistant"
        return _message(line, role, payload.get("message"))
    if subtype == "item_completed":
        item = _object(payload.get("item"))
        return (
            _codex_item(item, line)
            if item is not None
            else _fallback(line, "unknown", payload, subtype)
        )
    if subtype in ("task_complete", "turn_aborted", "error", "task_failed"):
        reason = _string(payload, "reason") or _string(payload, "message")
        error = payload.get("error")
        return _fallback(line, "terminal", error or reason or subtype)
    return _fallback(line, "unknown", payload, subtype)


def _codex_item(item: dict[str, Any], line: int) -> CompactRecord | None:
    kind = _string(item, "type")
    if kind in _CODEX_ITEM_METADATA:
        return None
    if (
        kind == "SubAgentActivity"
        and item.get("kind") in ("started", "interacted", "completed")
        and item.keys() <= {"type", "id", "kind", "agent_thread_id", "agent_path"}
    ):
        return None
    if (
        kind == "Extension"
        and item.get("kind") == "clock.sleep"
        and item.keys() <= {"type", "kind", "id", "durationMs"}
    ):
        return None
    if kind in ("UserMessage", "AgentMessage"):
        return _message(line, "user" if kind == "UserMessage" else "assistant", item.get("content"))
    if kind == "CommandExecution":
        result = _command_result(item)
    elif kind == "FileChange":
        result = _file_result(item)
    elif kind == "McpToolCall":
        result = _mcp_result(item)
    else:
        return _fallback(line, "unknown", item, kind)
    return CompactRecord(line=line, kind="tool_result", tool_results=(result,))


def _command_result(item: dict[str, Any]) -> ToolResult:
    output, trimmed = _preview(
        _output_text(
            item.get("stdout") if item.get("stdout") is not None else item.get("aggregated_output")
        )
    )
    stderr, stderr_trimmed = _preview(_output_text(item.get("stderr")))
    error, error_trimmed = _preview(_output_text(item.get("error")))
    command, command_trimmed = _preview(_command(item.get("command")))
    exit_code = item.get("exit_code")
    return ToolResult(
        kind="command",
        status=_string(item, "status"),
        command=command or None,
        preview=output,
        truncated=trimmed or stderr_trimmed or error_trimmed or command_trimmed,
        exit_code=exit_code
        if isinstance(exit_code, int) and not isinstance(exit_code, bool)
        else None,
        stderr=stderr or None,
        error=error or None,
    )


def _command(value: Any) -> str:
    if isinstance(value, list) and all(isinstance(part, str) for part in cast("list[Any]", value)):
        return shlex.join(cast("list[str]", value))
    return _json_text(value)


def _file_result(item: dict[str, Any]) -> ToolResult:
    changes: list[FileChange] = []
    raw_changes = item.get("changes")
    if isinstance(raw_changes, dict):
        for path, raw in cast("dict[str, Any]", raw_changes).items():
            changes.append(_file_change(path, _object(raw) or {}))
    elif isinstance(raw_changes, list):
        for raw in cast("list[Any]", raw_changes):
            change = _object(raw)
            if change is not None:
                changes.append(_file_change(_string(change, "path") or "unknown", change))
            else:
                preview, trimmed = _preview(_json_text(raw))
                changes.append(FileChange("unknown", "unknown", preview, truncated=trimmed))
    output, trimmed = _preview(
        _output_text(item.get("stdout") if item.get("stdout") is not None else item.get("output"))
    )
    stderr, stderr_trimmed = _preview(_output_text(item.get("stderr")))
    error, error_trimmed = _preview(_output_text(item.get("error")))
    return ToolResult(
        kind="file_change",
        status=_string(item, "status"),
        changes=tuple(changes),
        preview=output,
        stderr=stderr or None,
        error=error or None,
        truncated=trimmed
        or stderr_trimmed
        or error_trimmed
        or any(change.truncated for change in changes),
    )


def _file_change(path: str, change: dict[str, Any]) -> FileChange:
    kind = _object(change.get("kind"))
    operation = _string(change, "type") or (
        _string(kind, "type") if kind is not None else _string(change, "kind")
    )
    content = change.get("unified_diff", change.get("diff", change.get("content")))
    preview, trimmed = _preview(_json_text(content))
    move_path = _string(change, "move_path") or (
        _string(kind, "move_path") if kind is not None else None
    )
    return FileChange(path, operation or "unknown", preview, move_path, trimmed)


def _mcp_result(item: dict[str, Any]) -> ToolResult:
    result = _object(item.get("result"))
    output = result.get("content", result) if result is not None else item.get("result")
    if result is not None and "structuredContent" in result:
        output = {
            "content": _output_text(result.get("content")),
            "structuredContent": result["structuredContent"],
        }
    preview, trimmed = _preview(_output_text(output))
    error, error_trimmed = _preview(_output_text(item.get("error")))
    return ToolResult(
        kind="mcp",
        name=_string(item, "tool"),
        status="failed"
        if result is not None and result.get("isError") is True
        else _string(item, "status"),
        preview=preview,
        error=error or None,
        truncated=trimmed or error_trimmed,
    )


def _compact_claude(record: dict[str, Any], line: int) -> CompactRecord | None:
    record_type = _string(record, "type")
    if record_type == "system" and record.get("subtype") in (
        "compact_boundary",
        "turn_duration",
        "stop_hook_summary",
    ):
        return None
    if record_type not in ("user", "assistant"):
        return _fallback(line, "unknown", record, record_type)
    message = _object(record.get("message"))
    if message is None:
        return _fallback(line, "unknown", record, record_type)
    if record.get("isMeta") is True or record.get("isCompactSummary") is True:
        return None
    if message.get("content") is None:
        return _fallback(line, "unknown", message, record_type)
    role = _string(message, "role") or record_type
    parts = _content_parts(message.get("content"), _object(record.get("toolUseResult")))
    return _parts_record(line, role, parts)


def _message(line: int, role: str | None, content: Any) -> CompactRecord | None:
    if role in ("system", "developer"):
        return None
    if content is None:
        return _fallback(line, "unknown", {"role": role, "content": content})
    parts = _content_parts(content)
    if role == "user" and parts.text is not None:
        if is_codex_pre_trigger_context(parts.text):
            return None
        stripped = parts.text.lstrip()
        if stripped.startswith(("<subagent_notification>", "<turn_aborted>")):
            return _fallback(
                line,
                "subagent_message"
                if stripped.startswith("<subagent_notification>")
                else "terminal",
                parts.text,
            )
    return _parts_record(line, role or "unknown", parts)


def _parts_record(line: int, role: str, parts: _Parts) -> CompactRecord | None:
    if not (parts.texts or parts.calls or parts.results or parts.unavailable):
        return None
    kind = "tool_result" if parts.results and not parts.texts and not parts.calls else role
    return CompactRecord(
        line=line,
        kind=kind,
        text=parts.text,
        tool_uses=tuple(parts.calls),
        tool_results=tuple(parts.results),
        unavailable=tuple(parts.unavailable),
    )


def _content_parts(content: Any, result_meta: dict[str, Any] | None = None) -> _Parts:
    parts = _Parts()
    if isinstance(content, str):
        parts.texts.append(content)
        return parts
    if not isinstance(content, list):
        if content is not None:
            parts.unavailable.append(_preview(_json_text(content))[0])
        return parts
    for raw in cast("list[Any]", content):
        item = _object(raw)
        if item is None:
            parts.unavailable.append(_preview(_json_text(raw))[0])
            continue
        kind = _string(item, "type")
        if kind in ("text", "Text", "input_text", "output_text") and isinstance(
            item.get("text"), str
        ):
            parts.texts.append(cast("str", item["text"]))
        elif kind == "tool_use":
            parts.calls.append(_tool_use(item, item.get("input")))
        elif kind == "tool_result":
            parts.results.append(_claude_tool_result(item, result_meta))
        elif kind not in ("thinking", "redacted_thinking"):
            parts.unavailable.append(kind or "unknown content")
    return parts


def _tool_use(item: dict[str, Any], value: Any) -> ToolUse:
    summary, trimmed = _bounded_preview(_json_text(value), head=PREVIEW_HEAD_BYTES, tail=0)
    return ToolUse(_string(item, "name") or "unknown", summary, trimmed)


def _tool_result(output: Any) -> ToolResult:
    preview, trimmed = _preview(_output_text(output))
    return ToolResult(kind="output", preview=preview, truncated=trimmed)


def _claude_tool_result(item: dict[str, Any], meta: dict[str, Any] | None) -> ToolResult:
    meta = meta or {}
    file = _object(meta.get("file")) or {}
    file_path = _string(meta, "filePath") or _string(file, "filePath")
    command, command_trimmed = _preview(_json_text(meta.get("command")))
    preview, trimmed = _preview(_output_text(item.get("content")))
    code = meta.get("exitCode", meta.get("exit_code"))
    return ToolResult(
        kind="file" if file_path is not None else "command" if command else "output",
        status="failed" if item.get("is_error") is True else _string(meta, "status"),
        file_path=file_path,
        command=command or None,
        preview=preview,
        truncated=trimmed or command_trimmed,
        exit_code=code if isinstance(code, int) and not isinstance(code, bool) else None,
    )


def _output_text(output: Any) -> str:
    if not isinstance(output, list):
        return _json_text(output)
    values: list[str] = []
    for raw in cast("list[Any]", output):
        item = _object(raw)
        if item is not None and isinstance(item.get("text"), str):
            values.append(cast("str", item["text"]))
        elif item is not None and item.get("type") in (
            "image",
            "input_image",
            "audio",
            "encrypted_content",
        ):
            values.append(f"[{item['type']} content unavailable]")
        else:
            values.append(_json_text(raw))
    return "\n".join(values)


def _codex_duplicate_of(
    record: dict[str, Any], line: int, previous: str | None, following: str | None
) -> int | None:
    message = parse_codex_message(_echo_record(record))
    if message is None or message.representation != "event":
        return None
    neighbor_raw = previous if message.role == "user" else following
    neighbor = _parse_object(neighbor_raw) if neighbor_raw is not None else None
    parsed = parse_codex_message(neighbor) if neighbor is not None else None
    if parsed is None:
        return None
    first, second = (parsed, message) if message.role == "user" else (message, parsed)
    if not is_codex_message_echo(first, second):
        return None
    # Source scaffolding must not leave a dangling echo marker when its canonical form is omitted.
    if message.role == "user" and is_codex_pre_trigger_context(message.text):
        return None
    return line - 1 if message.role == "user" else line + 1


def _echo_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = _object(record.get("payload")) or {}
    item = _object(payload.get("item")) or {}
    if (
        record.get("type") != "event_msg"
        or payload.get("type") != "item_completed"
        or item.get("type") not in ("UserMessage", "AgentMessage")
    ):
        return record
    content = item.get("content")
    if not isinstance(content, list) or not content:
        return record
    texts: list[str] = []
    for raw in cast("list[Any]", content):
        part = _object(raw)
        if (
            part is None
            or part.get("type") not in ("text", "Text")
            or not isinstance(part.get("text"), str)
        ):
            return record
        texts.append(cast("str", part["text"]))
    return {
        "type": "event_msg",
        "timestamp": record.get("timestamp"),
        "payload": {
            "type": "user_message" if item["type"] == "UserMessage" else "agent_message",
            "message": "\n".join(texts),
        },
    }


def _fallback(line: int, kind: str, value: Any, source_type: str | None = None) -> CompactRecord:
    preview, trimmed = _preview(_json_text(value))
    return CompactRecord(
        line=line, kind=kind, text=preview, truncated=trimmed, source_type=source_type
    )


def _preview(text: str) -> tuple[str, bool]:
    if len(text.encode("utf-8", errors="backslashreplace")) <= SHORT_TOOL_RESULT_BYTES:
        return text, False
    return _bounded_preview(text, head=PREVIEW_HEAD_BYTES, tail=PREVIEW_TAIL_BYTES)


def _bounded_preview(text: str, *, head: int, tail: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="backslashreplace")
    if len(encoded) <= head:
        return text, False
    head_text = encoded[:head].decode("utf-8", errors="ignore")
    if tail <= 0 or len(encoded) <= head + tail:
        return head_text + _PREVIEW_ELISION, True
    tail_text = encoded[-tail:].decode("utf-8", errors="ignore")
    return head_text + _PREVIEW_ELISION + tail_text, True


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    return (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def _parse_object(raw: str) -> dict[str, Any] | None:
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return _object(value)


def _object(value: Any) -> dict[str, Any] | None:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _string(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) and value.strip() else None
