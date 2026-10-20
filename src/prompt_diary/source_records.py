"""Shared source-record classification for preparation and compact evidence reads."""

from __future__ import annotations

import json
import re
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
_CONTEXT_TAGS = frozenset(
    {
        "skills.selected_skill_instructions",
        "agents_md.instructions",
        "environments.environment_context",
        "multi_agent.subagent_notification",
        "generic.turn_aborted",
    }
)
_REPLY_TAG = "send_user_message_question_reply"
_SHELL_TAG = "user_shell_command"


@dataclass(frozen=True)
class InputPart:
    """Exact source text with parsed provenance, not an interpretation of the user's intent."""

    kind: Literal[
        "human_input",
        "human_command",
        "generated_context",
        "context_compaction",
        "assistant_message",
        "unknown",
    ]
    text: str
    source_tag: str | None = None


@dataclass(frozen=True)
class SkillInvocation:
    """A native skill selection, without inventing a human-authored quotation."""

    name: str
    path: str | None = None
    kind: Literal["skill_invocation"] = "skill_invocation"


@dataclass(frozen=True)
class UnavailableInput:
    """Input with malformed structure or unestablished provenance, not a quotable text block."""

    source_type: str
    kind: Literal["unavailable"] = "unavailable"


@dataclass(frozen=True)
class HumanAttachment:
    """Opaque nontext input from a human; its type is descriptive, not a media allowlist."""

    source_type: str
    kind: Literal["human_attachment"] = "human_attachment"


@dataclass(frozen=True)
class CodexInput:
    """Ordered input parts shared by trigger indexing and evidence presentation."""

    parts: tuple[InputPart | SkillInvocation | UnavailableInput | HumanAttachment, ...]

    @property
    def is_human_trigger(self) -> bool:
        return any(
            part.kind in ("human_input", "human_command", "skill_invocation", "human_attachment")
            for part in self.parts
        )


def is_codex_compaction_output(record: Mapping[str, object]) -> bool:
    """Recognize the native whole-record compaction marker, independent of transport role."""
    metadata = _object(record.get("metadata"))
    return metadata is not None and metadata.get("compaction_output") is True


def parse_codex_input(record: Mapping[str, object]) -> CodexInput | None:
    """Parse known user-record shapes; missing provenance retains legacy ordinary text.

    Source metadata takes precedence over transport role. Unknown metadata or malformed known
    envelopes remain unknown, and ``client_authored`` is deliberately not an authorship signal.
    Recognized local shell commands and their results contribute no collaboration evidence.
    """
    payload = _object(record.get("payload"))
    if payload is None:
        return None
    record_type = record.get("type")
    if record_type == "retained_context" and payload.get("type") == "verified_answer":
        return _verified_answer(payload)
    if record_type == "response_item":
        if payload.get("type") != "message" or payload.get("role") != "user":
            return None
        content = payload.get("content")
        metadata = payload.get("internal_chat_message_metadata_passthrough")
    elif record_type == "event_msg" and payload.get("type") == "user_message":
        content = [{"type": "text", "text": payload.get("message")}]
        metadata = None
    elif record_type == "event_msg" and payload.get("type") == "item_completed":
        item = _object(payload.get("item"))
        if item is None or item.get("type") != "UserMessage":
            return None
        content = item.get("content")
        metadata = None
    else:
        return None
    if is_codex_compaction_output(record) and isinstance(content, list):
        return CodexInput(
            tuple(
                part
                for item in cast("list[object]", content)
                for part in _parse_input_item(item, "compaction.summary")
            )
        )
    return _parse_input_content(content, metadata)


def _verified_answer(payload: Mapping[str, object]) -> CodexInput | None:
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    parts: list[InputPart] = []
    for raw in cast("list[object]", questions):
        reply = _object(raw)
        if reply is None or set(reply) != {"question", "answer"}:
            return None
        question, answer = reply["question"], reply["answer"]
        if not isinstance(question, str) or not isinstance(answer, str):
            return None
        parts.extend(
            (
                InputPart("assistant_message", question, "verified_answer.question"),
                InputPart("human_input", answer, "verified_answer.answer"),
            )
        )
    return CodexInput(tuple(parts))


def _parse_input_content(content: object, metadata: object) -> CodexInput:
    if not isinstance(content, list):
        return CodexInput((_unknown_input(content, None),))
    items = cast("list[object]", content)
    metadata_object = _object(metadata)
    if metadata is not None and metadata_object is None:
        return CodexInput(tuple(_unknown_input(item, "conflicting_metadata") for item in items))
    tags = metadata_object.get("content_item_kinds") if metadata_object is not None else None
    has_tags = metadata_object is not None and "content_item_kinds" in metadata_object
    if has_tags and (
        not isinstance(tags, list)
        or len(cast("list[object]", tags)) != len(items)
        or any(not isinstance(tag, str) for tag in cast("list[object]", tags))
    ):
        return CodexInput(tuple(_unknown_input(item, "conflicting_metadata") for item in items))
    source_tags = cast("list[str]", tags) if has_tags else [None] * len(items)
    parts: list[InputPart | SkillInvocation | UnavailableInput | HumanAttachment] = []
    for item, tag in zip(items, source_tags, strict=True):
        parts.extend(_parse_input_item(item, tag))
    return CodexInput(tuple(parts))


def _parse_input_item(
    item: object, tag: str | None
) -> tuple[InputPart | SkillInvocation | UnavailableInput | HumanAttachment, ...]:
    part = _object(item)
    if part is None:
        return (_unknown_input(item, tag),)
    if part.get("type") == "skill" and tag is None:
        name = part.get("name")
        path = part.get("path")
        if isinstance(name, str) and (path is None or isinstance(path, str)):
            return (SkillInvocation(name, path),)
    if part.get("type") in ("input_text", "text", "Text") and isinstance(part.get("text"), str):
        return _input_text_parts(cast("str", part["text"]), tag)
    attachment = _human_attachment(part, tag)
    if attachment is not None:
        return (attachment,)
    return (_unknown_input(item, tag),)


def _human_attachment(part: Mapping[str, object], tag: str | None) -> HumanAttachment | None:
    """Separate opaque typed content from text/skill schemas and source provenance errors."""
    source_type = part.get("type")
    if (
        not isinstance(source_type, str)
        or not source_type.strip()
        or source_type in ("input_text", "text", "Text", "skill")
        or "text" in part
    ):
        return None
    if tag is not None:
        owner, _, content_kind = tag.partition(".")
        if owner != "user" or not content_kind.strip() or content_kind == "text":
            return None
    return HumanAttachment(source_type)


def parse_claude_input_text(text: str) -> tuple[InputPart, ...]:
    """Parse slash requests as input while omitting recognized local-command output."""
    stripped = text.strip()
    if stripped.startswith("<local-command-stdout>"):
        output = _envelope_body(stripped, "local-command-stdout")
        return () if output is not None else (InputPart("unknown", text),)
    if stripped.startswith(("<command-name>", "<command-message>", "<command-args>")):
        match = re.fullmatch(
            r"<command-name>(.*?)</command-name>\s*"
            r"<command-message>(.*?)</command-message>\s*"
            r"<command-args>(.*?)</command-args>",
            stripped,
            re.DOTALL,
        )
        if match is None:
            return (InputPart("unknown", text),)
        parts = [
            InputPart("human_command", match[1], "claude.command_name"),
            InputPart("generated_context", match[2], "claude.command_message"),
        ]
        if match[3]:
            parts.append(InputPart("human_input", match[3], "claude.command_args"))
        return tuple(parts)
    return (InputPart("human_input", text),)


@dataclass(frozen=True)
class ClaudeInput:
    """Ordered Claude input parts after source-owned context and tool records are excluded."""

    parts: tuple[InputPart | HumanAttachment | UnavailableInput, ...]

    @property
    def is_human_trigger(self) -> bool:
        return any(
            part.kind in ("human_input", "human_command", "human_attachment") for part in self.parts
        )


def parse_claude_input(
    record: Mapping[str, object], previous: Mapping[str, object] | None = None
) -> ClaudeInput | None:
    """Parse user input with the same record-level provenance used to index human triggers."""
    message = _object(record.get("message"))
    tool_owner = record.get("sourceToolAssistantUUID")
    if (
        record.get("type") != "user"
        or message is None
        or message.get("role") != "user"
        or (isinstance(tool_owner, str) and bool(tool_owner.strip()))
        or any(record.get(flag) is True for flag in ("isSidechain", "isMeta", "isCompactSummary"))
        or is_claude_local_command(record, previous)
    ):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return ClaudeInput(parse_claude_input_text(content))
    if not isinstance(content, list):
        return ClaudeInput((UnavailableInput("unknown content"),))
    return ClaudeInput(
        tuple(
            part
            for item in cast("list[object]", content)
            for part in _parse_claude_input_item(item)
        )
    )


def _parse_claude_input_item(
    item: object,
) -> tuple[InputPart | HumanAttachment | UnavailableInput, ...]:
    part = _object(item)
    if part is not None:
        kind = part.get("type")
        if kind == "text" and isinstance(part.get("text"), str):
            return parse_claude_input_text(cast("str", part["text"]))
        if kind == "tool_result":
            return ()
        attachment = _human_attachment(part, None)
        if attachment is not None:
            return (attachment,)
    return (_unknown_input(item, None),)


def is_claude_local_command(
    record: Mapping[str, object], previous: Mapping[str, object] | None = None
) -> bool:
    """Recognize client-local execution from explicit metadata, never from a slash name."""
    if record.get("type") == "system" and record.get("subtype") == "local_command":
        return True
    if (
        record.get("type") != "user"
        or record.get("sourceToolAssistantUUID") is not None
        or previous is None
        or previous.get("type") != "user"
        or previous.get("isMeta") is not True
        or not isinstance(previous.get("uuid"), str)
        or not previous["uuid"]
        or record.get("parentUuid") != previous["uuid"]
        or record.get("promptId") != previous.get("promptId")
    ):
        return False
    notice = _object(previous.get("message"))
    message = _object(record.get("message"))
    if notice is None or message is None:
        return False
    content = notice.get("content")
    if (
        notice.get("role") != "user"
        or message.get("role") != "user"
        or not isinstance(content, str)
        or _envelope_body(content.strip(), "local-command-caveat") is None
    ):
        return False
    command = message.get("content")
    return isinstance(command, str) and any(
        part.kind == "human_command" for part in parse_claude_input_text(command)
    )


def has_claude_human_input(content: object) -> bool:
    """Recognize human actions in content after callers exclude tool-owned/meta messages."""
    if isinstance(content, str):
        return any(
            part.kind in ("human_input", "human_command")
            for part in parse_claude_input_text(content)
        )
    if not isinstance(content, list):
        return False
    return any(
        part.kind in ("human_input", "human_command", "human_attachment")
        for item in cast("list[object]", content)
        for part in _parse_claude_input_item(item)
    )


def _input_text_parts(text: str, tag: str | None) -> tuple[InputPart, ...]:
    if tag == "compaction.summary":
        return (InputPart("context_compaction", text, tag),)
    if tag in _CONTEXT_TAGS:
        return (InputPart("generated_context", text, tag),)
    if tag not in (None, "user.text", "shell.user_command"):
        return (InputPart("unknown", text, tag),)
    stripped = text.strip()
    if stripped.startswith(f"<{_REPLY_TAG}>"):
        reply = _question_reply(stripped) if tag != "shell.user_command" else None
        return reply if reply is not None else (InputPart("unknown", text, tag),)
    if stripped.startswith(f"<{_SHELL_TAG}>") or tag == "shell.user_command":
        shell = _shell_input(stripped)
        return shell if shell is not None else (InputPart("unknown", text, tag),)
    if stripped.startswith("<skill>"):
        # Legacy selected-skill bodies lack provenance metadata; malformed lookalikes are unknown.
        if tag is None and re.fullmatch(
            r"<skill>\s*<name>[^<]+</name>\s*<path>[^<]+</path>.*</skill>", stripped, re.DOTALL
        ):
            return (InputPart("generated_context", text, "skills.selected_skill_instructions"),)
        return (InputPart("unknown", text, tag),)
    if is_codex_source_context(text):
        return (InputPart("generated_context" if tag is None else "unknown", text, tag),)
    return (InputPart("human_input", text, tag),)


def _question_reply(text: str) -> tuple[InputPart, ...] | None:
    body = _envelope_body(text, _REPLY_TAG)
    if body is None:
        return None
    try:
        value = cast("object", json.loads(body))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or not value:
        return None
    parts: list[InputPart] = []
    for raw in cast("list[object]", value):
        reply = _object(raw)
        if (
            reply is None
            or set(reply) != {"answer", "question", "questionItemId"}
            or not all(
                isinstance(reply[key], str) for key in ("answer", "question", "questionItemId")
            )
        ):
            return None
        parts.extend(
            (
                InputPart(
                    "assistant_message", cast("str", reply["question"]), "question_reply.question"
                ),
                InputPart("human_input", cast("str", reply["answer"]), "question_reply.answer"),
            )
        )
    return tuple(parts)


def _shell_input(text: str) -> tuple[InputPart, ...] | None:
    body = _envelope_body(text, _SHELL_TAG)
    if body is None:
        return None
    matched = re.fullmatch(
        r"\s*<command>(.*?)</command>\s*<result>(.*?)</result>\s*", body, re.DOTALL
    )
    if matched is None:
        return None
    return ()


def _envelope_body(text: str, tag: str) -> str | None:
    opening, closing = f"<{tag}>", f"</{tag}>"
    return (
        text[len(opening) : -len(closing)]
        if text.startswith(opening) and text.endswith(closing)
        else None
    )


def _unknown_input(value: object, tag: str | None) -> InputPart | UnavailableInput:
    item = _object(value)
    text = item.get("text") if item is not None else None
    if isinstance(text, str):
        return InputPart("unknown", text, tag)
    if isinstance(value, str):
        return InputPart("unknown", value, tag)
    kind = item.get("type") if item is not None else None
    return UnavailableInput(kind if isinstance(kind, str) else "unknown content")


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
        if event_type == "item_completed":
            item = _object(payload.get("item"))
            if item is None or item.get("type") not in ("UserMessage", "AgentMessage"):
                return None
            text = _native_message_text(item.get("content"))
            event_type = "user_message" if item["type"] == "UserMessage" else "agent_message"
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


def _native_message_text(content: object) -> str | None:
    if not isinstance(content, list) or not content:
        return None
    texts: list[str] = []
    for raw in cast("list[object]", content):
        part = _object(raw)
        if part is None:
            return None
        if part.get("type") == "skill" and isinstance(part.get("name"), str):
            continue
        if part.get("type") not in ("text", "Text") or not isinstance(part.get("text"), str):
            return None
        texts.append(cast("str", part["text"]))
    return "\n".join(texts) if texts else None


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
