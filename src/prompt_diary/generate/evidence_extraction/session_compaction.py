"""Deterministically expose session evidence while omitting recognized source scaffolding.

Physical line numbers locate evidence in the original transcript. Human and assistant text is
preserved exactly; tool output is bounded and explicitly marked when reduced. Unknown records
remain visible. Recognized metadata returns ``None`` so the reader can advance without emitting it.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from prompt_diary.source_records import (
    HumanAttachment,
    InputPart,
    SkillInvocation,
    UnavailableInput,
    is_claude_local_command,
    is_codex_compaction_output,
    is_codex_message_echo,
    is_codex_pre_trigger_context,
    parse_claude_input_text,
    parse_codex_input,
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
    }
)
# Conversation lifecycle and transport diagnostics do not establish the outcome of tool work.
_CODEX_EVENT_METADATA = frozenset(
    {
        "token_count",
        "task_started",
        "thread_settings_applied",
        "task_complete",
        "error",
        "task_failed",
    }
)
_CODEX_ITEM_METADATA = frozenset({"Reasoning"})


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
class TextPart:
    """Text with source attribution, independent of its transport role."""

    kind: str
    text: str
    source_tag: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ToolUsePart:
    kind: Literal["tool_call"]
    tool_use: ToolUse


@dataclass(frozen=True)
class ToolResultPart:
    kind: Literal["tool_result"]
    tool_result: ToolResult


@dataclass(frozen=True)
class UnavailablePart:
    kind: Literal["unknown"]
    unavailable: str


ContentPart = (
    TextPart | ToolUsePart | ToolResultPart | UnavailablePart | SkillInvocation | HumanAttachment
)


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
    source_tag: str | None = None
    name: str | None = None
    path: str | None = None
    parts: tuple[ContentPart, ...] = ()


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
        compaction = _codex_compaction_record(record, line)
        if compaction is not None:
            return compaction
        user_input = parse_codex_input(record)
        if user_input is not None and not user_input.parts:
            return None
        duplicate = _codex_duplicate_of(record, line, previous_line, next_line)
        if duplicate is not None:
            return CompactRecord(line=line, kind="duplicate", duplicate_of=duplicate)
        if user_input is not None:
            return _parts_record(line, tuple(_input_part(part) for part in user_input.parts))
        return _compact_codex(record, line)
    if source == "claude-code":
        previous = _parse_object(previous_line) if previous_line is not None else None
        if is_claude_local_command(record, previous):
            return None
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
        if key == "text" and isinstance(item, str):
            result[key] = item
            continue
        if item is None or item is False or item == "" or item == () or item == []:
            continue
        if isinstance(item, (list, tuple)):
            result[key] = [
                _sparse(cast("dict[str, Any]", part)) if isinstance(part, dict) else part
                for part in cast("list[Any] | tuple[Any, ...]", item)
            ]
        elif isinstance(item, dict):
            result[key] = _sparse(cast("dict[str, Any]", item))
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
        parts = _content_parts(payload.get("content"), "subagent_message")
        return CompactRecord(
            line=line,
            kind="subagent_message",
            text="\n".join(part.text for part in parts if isinstance(part, TextPart)) or None,
            unavailable=tuple(
                part.unavailable for part in parts if isinstance(part, UnavailablePart)
            ),
        )
    return _fallback(line, "unknown", payload, subtype)


def _codex_compaction_record(record: dict[str, Any], line: int) -> CompactRecord | None:
    payload = _object(record.get("payload")) or {}
    if record.get("type") == "compacted":
        return _codex_compaction(payload, line)
    if (
        record.get("type") == "response_item"
        and payload.get("type") == "message"
        and payload.get("role") == "assistant"
        and is_codex_compaction_output(record)
    ):
        summary = _compaction_text(payload.get("content"))
        return CompactRecord(
            line=line,
            kind="context_compaction",
            source_type="compaction_output",
            text=summary,
            unavailable=("compaction_summary",) if summary is None else (),
        )
    return None


def _codex_compaction(payload: dict[str, Any], line: int) -> CompactRecord:
    summary = _string(payload, "message")
    summaries: list[str] = []
    encrypted = False
    history = payload.get("replacement_history")
    if not summary and isinstance(history, list):
        for raw in cast("list[Any]", history):
            item = _object(raw)
            if item is None:
                continue
            encrypted |= item.get("type") == "compaction" and "encrypted_content" in item
            summaries.extend(_tagged_compaction_summary(item))
        summary = "\n".join(summaries)
    return CompactRecord(
        line=line,
        kind="context_compaction",
        source_type="compacted",
        text=summary or None,
        unavailable=("encrypted_compaction_summary",) if encrypted and not summary else (),
    )


def _tagged_compaction_summary(item: dict[str, Any]) -> tuple[str, ...]:
    """Read only explicitly tagged summary text, never replay retained user/setup history."""
    metadata = _object(item.get("internal_chat_message_metadata_passthrough")) or {}
    tags = metadata.get("content_item_kinds")
    content = item.get("content")
    if (
        item.get("type") != "message"
        or not isinstance(tags, list)
        or not isinstance(content, list)
        or len(cast("list[Any]", tags)) != len(cast("list[Any]", content))
        or any(not isinstance(tag, str) for tag in cast("list[Any]", tags))
    ):
        return ()
    return tuple(
        text
        for tag, part in zip(cast("list[str]", tags), cast("list[Any]", content), strict=True)
        if tag == "compaction.summary" and (text := _compaction_text([part])) is not None
    )


def _compaction_text(content: Any) -> str | None:
    """Keep available context text exactly; reasoning and opaque payloads are not summary text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    texts: list[str] = []
    for raw in cast("list[Any]", content):
        part = _object(raw)
        if part is not None and part.get("type") in ("text", "Text", "input_text", "output_text"):
            text = _string(part, "text")
            if text is not None:
                texts.append(text)
    return "\n".join(texts) if texts else None


def _codex_event(payload: dict[str, Any], line: int) -> CompactRecord | None:
    subtype = _string(payload, "type")
    if subtype in _CODEX_EVENT_METADATA:
        return None
    if subtype == "context_compacted":
        return CompactRecord(line=line, kind="context_compaction", source_type=subtype)
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
    if subtype == "exec_command_end":
        return CompactRecord(
            line=line, kind="tool_result", tool_results=(_command_result(payload),)
        )
    if subtype == "turn_aborted":
        reason = _string(payload, "reason") or _string(payload, "message")
        error = payload.get("error")
        return _fallback(line, "terminal", error or reason or subtype)
    return _fallback(line, "unknown", payload, subtype)


def _codex_item(item: dict[str, Any], line: int) -> CompactRecord | None:
    kind = _string(item, "type")
    if kind in _CODEX_ITEM_METADATA:
        return None
    if kind == "ContextCompaction":
        return CompactRecord(line=line, kind="context_compaction", source_type=kind)
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
    output, trimmed = _preview(_output_text(item.get("stdout") or item.get("aggregated_output")))
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
    if record_type == "system" and record.get("subtype") == "compact_boundary":
        return CompactRecord(
            line=line,
            kind="context_compaction",
            source_type="compact_boundary",
            text=_compaction_text(record.get("content")),
        )
    if record_type == "system" and record.get("subtype") in (
        "turn_duration",
        "stop_hook_summary",
    ):
        return None
    if record_type not in ("user", "assistant"):
        return _fallback(line, "unknown", record, record_type)
    message = _object(record.get("message"))
    if record.get("isCompactSummary") is True:
        summary = _compaction_text(message.get("content")) if message is not None else None
        return CompactRecord(
            line=line,
            kind="context_compaction",
            source_type="isCompactSummary",
            text=summary,
            unavailable=("compaction_summary",) if summary is None else (),
        )
    if message is None:
        return _fallback(line, "unknown", record, record_type)
    if record.get("isMeta") is True:
        return None
    if message.get("content") is None:
        return _fallback(line, "unknown", message, record_type)
    role = _string(message, "role") or record_type
    text_kind = (
        "tool_result"
        if role == "user" and record.get("sourceToolAssistantUUID") is not None
        else _message_kind(role)
    )
    parts = _content_parts(
        message.get("content"),
        text_kind,
        _object(record.get("toolUseResult")),
        source="claude-code",
    )
    return _parts_record(line, parts)


def _message(line: int, role: str | None, content: Any) -> CompactRecord | None:
    if role in ("system", "developer"):
        return None
    if content is None:
        return _fallback(line, "unknown", {"role": role, "content": content})
    return _parts_record(line, _content_parts(content, _message_kind(role)))


def _message_kind(role: str | None) -> str:
    return {"user": "human_input", "assistant": "assistant_message"}.get(role or "", "unknown")


def _input_part(
    part: InputPart | SkillInvocation | UnavailableInput | HumanAttachment,
) -> ContentPart:
    if isinstance(part, (SkillInvocation, HumanAttachment)):
        return part
    if isinstance(part, UnavailableInput):
        return UnavailablePart("unknown", part.source_type)
    kind = part.kind
    if kind == "generated_context" and part.source_tag == "skills.selected_skill_instructions":
        return TextPart("skill_context", part.text, part.source_tag)
    stripped = part.text.lstrip()
    if kind == "generated_context" and stripped.startswith("<subagent_notification>"):
        return TextPart("subagent_message", part.text, part.source_tag)
    if kind == "generated_context" and stripped.startswith("<turn_aborted>"):
        return _reaction_part("terminal", part)
    return TextPart(kind, part.text, part.source_tag)


def _reaction_part(kind: str, part: InputPart) -> TextPart:
    text, truncated = _preview(part.text)
    return TextPart(kind, text, part.source_tag, truncated)


def _parts_record(line: int, parts: tuple[ContentPart, ...]) -> CompactRecord | None:
    if not parts:
        return None
    texts = tuple(part for part in parts if isinstance(part, TextPart))
    if len(texts) == len(parts):
        first = texts[0]
        if all((part.kind, part.source_tag) == (first.kind, first.source_tag) for part in texts):
            text = "\n".join(part.text for part in texts)
            if first.kind == "generated_context" and is_codex_pre_trigger_context(text):
                return None
            return CompactRecord(
                line=line,
                kind=first.kind,
                text=text,
                source_tag=first.source_tag,
                truncated=any(part.truncated for part in texts),
            )
    calls = tuple(part.tool_use for part in parts if isinstance(part, ToolUsePart))
    if len(calls) == len(parts):
        return CompactRecord(line=line, kind="tool_call", tool_uses=calls)
    results = tuple(part.tool_result for part in parts if isinstance(part, ToolResultPart))
    if len(results) == len(parts):
        return CompactRecord(line=line, kind="tool_result", tool_results=results)
    unavailable = tuple(part.unavailable for part in parts if isinstance(part, UnavailablePart))
    if len(unavailable) == len(parts):
        return CompactRecord(line=line, kind="unknown", unavailable=unavailable)
    if len(parts) == 1 and isinstance(parts[0], SkillInvocation):
        return CompactRecord(
            line=line, kind="skill_invocation", name=parts[0].name, path=parts[0].path
        )
    if len(parts) == 1 and isinstance(parts[0], HumanAttachment):
        return CompactRecord(line=line, kind="human_attachment", source_type=parts[0].source_type)
    return CompactRecord(line=line, kind="mixed", parts=parts)


def _content_parts(
    content: Any,
    text_kind: str,
    result_meta: dict[str, Any] | None = None,
    *,
    source: Literal["codex", "claude-code"] = "codex",
) -> tuple[ContentPart, ...]:
    parts: list[ContentPart] = []
    if isinstance(content, str):
        return _text_parts(content, text_kind, source)
    if not isinstance(content, list):
        if content is not None:
            parts.append(UnavailablePart("unknown", _preview(_json_text(content))[0]))
        return tuple(parts)
    for raw in cast("list[Any]", content):
        parts.extend(_content_item(raw, text_kind, result_meta, source))
    return tuple(parts)


def _content_item(
    raw: Any,
    text_kind: str,
    result_meta: dict[str, Any] | None,
    source: Literal["codex", "claude-code"],
) -> tuple[ContentPart, ...]:
    item = _object(raw)
    if item is None:
        return (UnavailablePart("unknown", _preview(_json_text(raw))[0]),)
    kind = _string(item, "type")
    if kind in ("text", "Text", "input_text", "output_text") and isinstance(item.get("text"), str):
        return _text_parts(cast("str", item["text"]), text_kind, source)
    if kind == "tool_use":
        return (ToolUsePart("tool_call", _tool_use(item, item.get("input"))),)
    if kind == "tool_result":
        return (ToolResultPart("tool_result", _claude_tool_result(item, result_meta)),)
    if kind == "image" and text_kind == "human_input" and source == "claude-code":
        return (HumanAttachment("image"),)
    if kind in ("thinking", "redacted_thinking"):
        return ()
    return (UnavailablePart("unknown", kind or "unknown content"),)


def _text_parts(
    text: str, kind: str, source: Literal["codex", "claude-code"]
) -> tuple[ContentPart, ...]:
    if kind == "tool_result":
        return (ToolResultPart("tool_result", _tool_result(text)),)
    if kind == "human_input" and source == "claude-code":
        return tuple(_input_part(part) for part in parse_claude_input_text(text))
    return (TextPart(kind, text),)


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
    user_input = parse_codex_input(record)
    if user_input is not None and any(
        isinstance(part, SkillInvocation) for part in user_input.parts
    ):
        # A text echo does not prove the selected skill is represented on the canonical line.
        return None
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
