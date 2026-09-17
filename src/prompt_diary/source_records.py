"""Shared source-record classification for preparation and compact evidence reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

CODEX_REPORT_ORIGINATOR = "prompt_diary"
"""Persisted Codex client identity for report generation, independent of workspace location."""

_CODEX_SOURCE_CONTEXT_PREFIXES = (
    "<environment_context>",
    "# AGENTS.md",
    "<turn_aborted>",
    "<subagent_notification>",
    "<INSTRUCTIONS>",
    "A previous agent produced the plan below",
)
_CODEX_REACTION_PREFIXES = ("<turn_aborted>", "<subagent_notification>")


@dataclass(frozen=True)
class CodexMessage:
    """A recognized text message or event echo with an authoritative source timestamp."""

    role: Literal["user", "assistant"]
    representation: Literal["response", "event"]
    text: str
    timestamp: datetime


def is_codex_source_context(text: str) -> bool:
    """Identify source-generated user-shaped context, including reset-plan bootstraps."""
    return text.lstrip().startswith(_CODEX_SOURCE_CONTEXT_PREFIXES)


def is_codex_pre_trigger_context(text: str) -> bool:
    """Identify setup context while preserving source-owned results and terminal states."""
    return is_codex_source_context(text) and not text.lstrip().startswith(_CODEX_REACTION_PREFIXES)


def is_claude_tool_result_content(content: object) -> bool:
    """Identify a nonempty message containing tool results without new human content."""
    if not isinstance(content, list):
        return False
    parts = [_object(item) for item in cast("list[object]", content)]
    return bool(parts) and all(
        part is not None and part.get("type") == "tool_result" for part in parts
    )


def parse_codex_message(record: Mapping[str, object]) -> CodexMessage | None:
    """Parse only known text-only Codex message shapes; unknown records cannot prove an echo."""
    payload = _object(record.get("payload"))
    timestamp = _timestamp(record.get("timestamp"))
    if payload is None or timestamp is None:
        return None
    if record.get("type") == "event_msg":
        event_type = payload.get("type")
        text = payload.get("message")
        if event_type not in ("user_message", "agent_message") or not isinstance(text, str):
            return None
        return CodexMessage(
            role="user" if event_type == "user_message" else "assistant",
            representation="event",
            text=text,
            timestamp=timestamp,
        )
    if record.get("type") != "response_item" or payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in ("user", "assistant"):
        return None
    text = _response_text(payload.get("content"))
    if text is None:
        return None
    return CodexMessage(
        role=role,
        representation="response",
        text=text,
        timestamp=timestamp,
    )


def is_codex_message_echo(first: CodexMessage, second: CodexMessage) -> bool:
    """Match one known echo pair; callers must enforce physical adjacency and consume it once."""
    expected_order = ("response", "event") if first.role == "user" else ("event", "response")
    return (
        first.role == second.role
        and (first.representation, second.representation) == expected_order
        and first.text == second.text
        and timedelta(0) <= second.timestamp - first.timestamp <= timedelta(milliseconds=100)
    )


def _response_text(content: object) -> str | None:
    if not isinstance(content, list) or not content:
        return None
    texts: list[str] = []
    for item in cast("list[object]", content):
        part = _object(item)
        if part is None or part.get("type") not in ("input_text", "output_text"):
            return None
        text = part.get("text")
        if not isinstance(text, str):
            return None
        texts.append(text)
    return "\n".join(texts)


def _object(value: object) -> Mapping[str, object] | None:
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp.astimezone(timezone.utc) if timestamp.tzinfo is not None else None
