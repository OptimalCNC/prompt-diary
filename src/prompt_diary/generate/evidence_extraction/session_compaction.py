"""Pure, deterministic, source-aware compaction of one raw session JSONL line.

This module turns a single physical JSONL line into a bounded, citation-safe ``CompactRecord``. It
does no filesystem, network, time, or randomness work: the same line and arguments always produce
the same record. Large tool-result payloads and assistant reasoning are trimmed to keep compact
reads small, while user-authored and assistant text messages are preserved in full because they are
primary evidence. A malformed line never raises; it still reports its physical line, raw byte count,
and content hash so provenance survives.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, cast

SHORT_TOOL_RESULT_BYTES = 1024
"""Tool-result payloads at or below this UTF-8 byte size pass through untrimmed (1 KiB)."""

PREVIEW_HEAD_BYTES = 320
"""Maximum UTF-8 byte size of a head preview taken from a trimmed payload."""

PREVIEW_TAIL_BYTES = 160
"""Maximum UTF-8 byte size of a tail preview appended after a trimmed payload's head."""

_PREVIEW_ELISION = "\n...[trimmed]...\n"


@dataclass(frozen=True)
class ToolUse:
    """A tool invocation reduced to its name and a bounded input summary."""

    name: str
    input_summary: str


@dataclass(frozen=True)
class ToolResult:
    """A tool result reduced to observable facts and a bounded payload preview."""

    kind: str
    status: str | None
    file_path: str | None
    command: str | None
    preview: str
    raw_bytes: int
    truncated: bool


@dataclass(frozen=True)
class CompactRecord:
    """One physical JSONL line described compactly, with provenance always preserved."""

    line: int
    record_type: str
    role: str | None
    content_kinds: tuple[str, ...]
    summary: str
    text_preview: str | None
    tool_uses: tuple[ToolUse, ...]
    tool_results: tuple[ToolResult, ...]
    raw_bytes: int
    raw_sha256: str
    truncated: bool


@dataclass
class _Compaction:
    """Mutable scratch describing a single record while it is being compacted."""

    record_type: str = "unknown"
    role: str | None = None
    content_kinds: tuple[str, ...] = ()
    summary: str = ""
    text_preview: str | None = None
    tool_uses: tuple[ToolUse, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class _ClaudeResultMeta:
    """Observable tool-result metadata lifted from a record-level ``toolUseResult``."""

    kind: str
    status: str | None
    file_path: str | None
    command: str | None


@dataclass
class _ClaudeParts:
    """Accumulated content parts walked from a Claude ``message.content`` list."""

    kinds: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    tool_uses: tuple[ToolUse, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    truncated: bool = False

    @property
    def content_kinds(self) -> tuple[str, ...]:
        return tuple(self.kinds)

    @property
    def text(self) -> str | None:
        return "\n".join(self.texts) if self.texts else None

    def add_kind(self, kind: str) -> None:
        if kind not in self.kinds:
            self.kinds.append(kind)


def line_provenance(raw_line: str) -> tuple[int, str]:
    """Return the UTF-8 byte length and SHA-256 hex digest of one physical line.

    Provenance is computed over exactly the bytes of ``raw_line`` (which must not include a
    trailing newline), so compact and full session reads report identical ``raw_bytes`` and
    ``raw_sha256`` for the same physical line.
    """
    raw_bytes = raw_line.encode("utf-8")
    return len(raw_bytes), hashlib.sha256(raw_bytes).hexdigest()


def compact_record(raw_line: str, *, line: int, source: str) -> CompactRecord:
    """Compact one raw physical JSONL line into a bounded structured record."""
    raw_bytes, raw_sha256 = line_provenance(raw_line)
    record = _parse_object(raw_line)
    compaction = _compact_unknown(record) if record is None else _compact_source(record, source)
    return CompactRecord(
        line=line,
        record_type=compaction.record_type,
        role=compaction.role,
        content_kinds=compaction.content_kinds,
        summary=compaction.summary,
        text_preview=compaction.text_preview,
        tool_uses=compaction.tool_uses,
        tool_results=compaction.tool_results,
        raw_bytes=raw_bytes,
        raw_sha256=raw_sha256,
        truncated=compaction.truncated,
    )


def compact_record_to_json(record: CompactRecord) -> dict[str, Any]:
    """Serialize a compact record into a JSON-ready dict."""
    return {
        "line": record.line,
        "record_type": record.record_type,
        "role": record.role,
        "content_kinds": list(record.content_kinds),
        "summary": record.summary,
        "text_preview": record.text_preview,
        "tool_uses": [_tool_use_to_json(tool_use) for tool_use in record.tool_uses],
        "tool_results": [_tool_result_to_json(result) for result in record.tool_results],
        "raw_bytes": record.raw_bytes,
        "raw_sha256": record.raw_sha256,
        "truncated": record.truncated,
    }


def _tool_use_to_json(tool_use: ToolUse) -> dict[str, Any]:
    return {"name": tool_use.name, "input_summary": tool_use.input_summary}


def _tool_result_to_json(result: ToolResult) -> dict[str, Any]:
    return {
        "kind": result.kind,
        "status": result.status,
        "file_path": result.file_path,
        "command": result.command,
        "preview": result.preview,
        "raw_bytes": result.raw_bytes,
        "truncated": result.truncated,
    }


def _compact_source(record: dict[str, Any], source: str) -> _Compaction:
    if source == "codex":
        return _compact_codex(record)
    if source == "claude-code":
        return _compact_claude(record)
    return _compact_unknown(record)


def _compact_claude(record: dict[str, Any]) -> _Compaction:
    record_type = _string(record, "type")
    if record_type in ("user", "assistant"):
        return _claude_message(record_type, record)
    if record_type == "system":
        return _claude_scaffolding(record)
    return _compact_unknown(record)


def _claude_scaffolding(record: dict[str, Any]) -> _Compaction:
    subtype = _string(record, "subtype")
    record_type = f"system:{subtype}" if subtype is not None else "system"
    return _Compaction(record_type=record_type, summary=f"{record_type} record.")


def _claude_message(record_type: str, record: dict[str, Any]) -> _Compaction:
    message = _object(record, "message")
    role = _string(message, "role") if message is not None else None
    result_meta = _claude_result_meta(_object(record, "toolUseResult"))
    parts = _claude_content_parts(message, result_meta)
    return _Compaction(
        record_type=record_type,
        role=role,
        content_kinds=parts.content_kinds,
        summary=_claude_summary(role, parts),
        text_preview=parts.text,
        tool_uses=parts.tool_uses,
        tool_results=parts.tool_results,
        truncated=parts.truncated,
    )


def _claude_summary(role: str | None, parts: _ClaudeParts) -> str:
    if parts.tool_results and parts.text is None:
        return "Tool result."
    if parts.kinds == ["thinking"]:
        return "Assistant reasoning omitted."
    return _message_summary(role)


def _claude_content_parts(
    message: dict[str, Any] | None,
    result_meta: _ClaudeResultMeta,
) -> _ClaudeParts:
    parts = _ClaudeParts()
    items = message.get("content") if message is not None else None
    if not isinstance(items, list):
        return parts
    for item in cast("list[Any]", items):
        if isinstance(item, dict):
            _claude_content_item(parts, cast("dict[str, Any]", item), result_meta)
    return parts


def _claude_content_item(
    parts: _ClaudeParts,
    item: dict[str, Any],
    result_meta: _ClaudeResultMeta,
) -> None:
    item_type = _string(item, "type")
    if item_type == "text":
        text = _string(item, "text")
        if text is not None:
            parts.add_kind("text")
            parts.texts.append(text)
    elif item_type == "tool_use":
        parts.add_kind("tool_use")
        parts.tool_uses = (*parts.tool_uses, _claude_tool_use(item))
    elif item_type == "tool_result":
        parts.add_kind("tool_result")
        result = _claude_tool_result(item, result_meta)
        parts.tool_results = (*parts.tool_results, result)
        parts.truncated = parts.truncated or result.truncated
    elif item_type == "thinking":
        parts.add_kind("thinking")
        parts.truncated = True


def _claude_result_meta(tool_use_result: dict[str, Any] | None) -> _ClaudeResultMeta:
    if tool_use_result is None:
        return _ClaudeResultMeta(kind="tool_result", status=None, file_path=None, command=None)
    status = _string(tool_use_result, "status")
    file_path = _claude_result_file_path(tool_use_result)
    command = _string(tool_use_result, "command")
    return _ClaudeResultMeta(
        kind=_result_kind(file_path=file_path, command=command),
        status=status,
        file_path=file_path,
        command=command,
    )


def _claude_result_file_path(tool_use_result: dict[str, Any]) -> str | None:
    direct = _string(tool_use_result, "filePath")
    if direct is not None:
        return direct
    nested = _object(tool_use_result, "file")
    return _string(nested, "filePath") if nested is not None else None


def _result_kind(*, file_path: str | None, command: str | None) -> str:
    if file_path is not None:
        return "file"
    if command is not None:
        return "command"
    return "tool_result"


def _claude_tool_result(item: dict[str, Any], result_meta: _ClaudeResultMeta) -> ToolResult:
    payload = _claude_tool_result_payload(item.get("content"))
    return _tool_result(
        payload,
        kind=result_meta.kind,
        status=result_meta.status,
        file_path=result_meta.file_path,
        command=result_meta.command,
    )


def _claude_tool_result_payload(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(_content_item_texts(cast("list[Any]", content)))


def _claude_tool_use(item: dict[str, Any]) -> ToolUse:
    name = _string(item, "name") or "unknown"
    input_summary, _ = _bounded_preview(
        _json_summary(item.get("input")),
        head=PREVIEW_HEAD_BYTES,
        tail=0,
    )
    return ToolUse(name=name, input_summary=input_summary)


def _json_summary(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _compact_codex(record: dict[str, Any]) -> _Compaction:
    record_type = _string(record, "type")
    payload = _object(record, "payload")
    if record_type == "event_msg" and payload is not None:
        return _compact_codex_event_msg(payload)
    if record_type == "response_item" and payload is not None:
        return _compact_codex_response_item(payload)
    return _compact_unknown(record)


def _compact_codex_response_item(payload: dict[str, Any]) -> _Compaction:
    payload_type = _string(payload, "type") or "item"
    record_type = f"response_item:{payload_type}"
    if payload_type == "message":
        return _codex_message(record_type, payload)
    if payload_type == "function_call":
        return _codex_function_call(record_type, payload)
    if payload_type == "function_call_output":
        return _codex_function_call_output(record_type, payload)
    if payload_type == "reasoning":
        return _reasoning_omitted(record_type)
    return _Compaction(record_type=record_type, summary=f"{record_type} record.")


def _reasoning_omitted(record_type: str) -> _Compaction:
    return _Compaction(
        record_type=record_type,
        content_kinds=("thinking",),
        summary="Assistant reasoning omitted.",
        truncated=True,
    )


def _codex_function_call(record_type: str, payload: dict[str, Any]) -> _Compaction:
    name = _string(payload, "name") or "unknown"
    arguments = _string(payload, "arguments") or ""
    input_summary, _ = _bounded_preview(arguments, head=PREVIEW_HEAD_BYTES, tail=0)
    return _Compaction(
        record_type=record_type,
        content_kinds=("tool_use",),
        summary=f"Tool call: {name}.",
        tool_uses=(ToolUse(name=name, input_summary=input_summary),),
    )


def _codex_function_call_output(record_type: str, payload: dict[str, Any]) -> _Compaction:
    output = payload.get("output")
    text = output if isinstance(output, str) else ""
    result = _tool_result(text, kind="output")
    return _Compaction(
        record_type=record_type,
        content_kinds=("tool_result",),
        summary="Tool result.",
        tool_results=(result,),
        truncated=result.truncated,
    )


def _tool_result(
    payload: str,
    *,
    kind: str,
    status: str | None = None,
    file_path: str | None = None,
    command: str | None = None,
) -> ToolResult:
    """Build a tool result, trimming only payloads larger than the short-result threshold."""
    raw_bytes = len(payload.encode("utf-8"))
    if raw_bytes <= SHORT_TOOL_RESULT_BYTES:
        return ToolResult(
            kind=kind,
            status=status,
            file_path=file_path,
            command=command,
            preview=payload,
            raw_bytes=raw_bytes,
            truncated=False,
        )
    preview, _ = _bounded_preview(payload, head=PREVIEW_HEAD_BYTES, tail=PREVIEW_TAIL_BYTES)
    return ToolResult(
        kind=kind,
        status=status,
        file_path=file_path,
        command=command,
        preview=preview,
        raw_bytes=raw_bytes,
        truncated=True,
    )


def _codex_message(record_type: str, payload: dict[str, Any]) -> _Compaction:
    role = _string(payload, "role")
    text = _codex_content_text(payload)
    return _Compaction(
        record_type=record_type,
        role=role,
        content_kinds=("text",) if text is not None else (),
        summary=_message_summary(role),
        text_preview=text,
    )


def _codex_content_text(payload: dict[str, Any]) -> str | None:
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    texts = _content_item_texts(cast("list[Any]", content))
    return "\n".join(texts) if texts else None


def _content_item_texts(content: list[Any]) -> list[str]:
    """Collect the non-empty ``text`` field of every dict item in a content list, in order."""
    return [
        item_text
        for item in content
        if isinstance(item, dict)
        for item_text in (_string(cast("dict[str, Any]", item), "text"),)
        if item_text is not None
    ]


def _message_summary(role: str | None) -> str:
    if role == "user":
        return "User message."
    if role == "assistant":
        return "Assistant message."
    return "Message."


def _compact_codex_event_msg(payload: dict[str, Any]) -> _Compaction:
    payload_type = _string(payload, "type") or "event"
    record_type = f"event_msg:{payload_type}"
    if payload_type == "user_message":
        return _text_message(record_type, role="user", summary="User message.", payload=payload)
    if payload_type == "agent_message":
        return _text_message(
            record_type, role="assistant", summary="Agent message.", payload=payload
        )
    return _Compaction(record_type=record_type, summary=f"{record_type} record.")


def _text_message(
    record_type: str,
    *,
    role: str,
    summary: str,
    payload: dict[str, Any],
) -> _Compaction:
    message = _string(payload, "message")
    return _Compaction(
        record_type=record_type,
        role=role,
        content_kinds=("text",) if message is not None else (),
        summary=summary,
        text_preview=message,
    )


def _compact_unknown(record: dict[str, Any] | None) -> _Compaction:
    if record is None:
        return _Compaction(record_type="unknown", summary="Malformed JSONL line.")
    role = _string(record, "role")
    record_type = role or _string(record, "type") or "unknown"
    return _Compaction(record_type=record_type, role=role, summary=f"{record_type} record.")


def _parse_object(raw_line: str) -> dict[str, Any] | None:
    try:
        parsed = cast("object", json.loads(raw_line))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, Any]", parsed)


def _bounded_preview(text: str, *, head: int, tail: int) -> tuple[str, bool]:
    """Return a byte-bounded preview of ``text`` and whether it was trimmed.

    A preview keeps a head slice and, when a tail budget is given and the gap is non-trivial, a
    tail slice joined by an elision marker. Slicing happens on UTF-8 bytes and decodes with partial
    trailing bytes dropped, so the result stays deterministic and never splits a code point.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= head:
        return text, False
    head_text = encoded[:head].decode("utf-8", errors="ignore")
    if tail <= 0 or len(encoded) <= head + tail:
        return head_text + _PREVIEW_ELISION, True
    tail_text = encoded[-tail:].decode("utf-8", errors="ignore")
    return head_text + _PREVIEW_ELISION + tail_text, True


def _string(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _object(record: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = record.get(key)
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None
