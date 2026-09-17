from __future__ import annotations

import json

import pytest

from prompt_diary.generate.evidence_extraction.session_compaction import compact_record

_LARGE_TEXT = "context body " * 10_000


@pytest.mark.parametrize(
    "prefix",
    [
        "<environment_context>",
        "# AGENTS.md instructions",
        "<INSTRUCTIONS>",
        "<subagent_notification>",
        "<turn_aborted>",
        "A previous agent produced the plan below",
    ],
)
@pytest.mark.parametrize("representation", ["event", "response"])
def test_codex_setup_is_omitted_while_reaction_context_remains_visible(
    prefix: str, representation: str
) -> None:
    text = prefix + "\n" + _LARGE_TEXT
    payload = (
        {"type": "user_message", "message": text}
        if representation == "event"
        else {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
    )
    raw = json.dumps(
        {"type": "event_msg" if representation == "event" else "response_item", "payload": payload}
    )

    record = compact_record(raw, line=1, source="codex")

    if prefix not in ("<subagent_notification>", "<turn_aborted>"):
        assert record is None
        return
    assert record is not None
    assert record.truncated is True
    assert record.text is not None
    assert record.text.startswith(prefix)
    assert len(record.text.encode()) < 600
    assert record.kind == (
        "subagent_message" if prefix == "<subagent_notification>" else "terminal"
    )


@pytest.mark.parametrize("role", ["developer", "system"])
def test_codex_non_conversation_roles_are_omitted(role: str) -> None:
    raw = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": role,
                "content": [{"type": "input_text", "text": _LARGE_TEXT}],
            },
        }
    )
    record = compact_record(raw, line=1, source="codex")
    assert record is None


@pytest.mark.parametrize("flag", ["isMeta", "isCompactSummary"])
def test_claude_explicit_source_context_is_omitted(flag: str) -> None:
    raw = json.dumps(
        {"type": "user", flag: True, "message": {"role": "user", "content": _LARGE_TEXT}}
    )
    record = compact_record(raw, line=1, source="claude-code")
    assert record is None


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("content", [_LARGE_TEXT, [{"type": "text", "text": _LARGE_TEXT}]])
def test_claude_genuine_string_and_list_messages_keep_original_text(
    role: str, content: object
) -> None:
    raw = json.dumps({"type": role, "message": {"role": role, "content": content}})
    record = compact_record(raw, line=1, source="claude-code")
    assert record is not None
    assert record.text == _LARGE_TEXT
    assert record.truncated is False


def test_claude_blank_content_parts_preserve_paragraph_boundaries() -> None:
    raw = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": part} for part in ("First", "", " ", "Last")],
            },
        }
    )
    record = compact_record(raw, line=1, source="claude-code")
    assert record is not None
    assert record.text == "First\n\n \nLast"


def test_assistant_quotes_of_source_context_are_preserved() -> None:
    text = "<subagent_notification>\n" + _LARGE_TEXT
    raw = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        }
    )
    record = compact_record(raw, line=1, source="codex")
    assert record is not None
    assert record.text == text


def test_source_context_preview_handles_escaped_surrogates() -> None:
    raw = json.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "<subagent_notification>" + "\ud800" * 5000,
            },
        }
    )
    record = compact_record(raw, line=1, source="codex")
    assert record is not None
    assert record.truncated is True
    assert record.text is not None
    assert len(record.text.encode("utf-8")) < 600
