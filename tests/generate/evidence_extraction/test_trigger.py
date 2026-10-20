from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from prompt_diary.generate.evidence_extraction.trigger import (
    TriggerReadError,
    TriggerText,
    read_trigger_text,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.models import SourceName


def _session(tmp_path: Path, *records: object) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_bytes("\r\n".join(json.dumps(record) for record in records).encode())
    return path


def _codex(content: object, *, tags: object = None) -> dict[str, object]:
    payload: dict[str, object] = {"type": "message", "role": "user", "content": content}
    if tags is not None:
        payload["internal_chat_message_metadata_passthrough"] = {"content_item_kinds": tags}
    return {"type": "response_item", "payload": payload}


def _claude(content: object, **flags: object) -> dict[str, object]:
    return {"type": "user", "message": {"role": "user", "content": content}, **flags}


@pytest.mark.parametrize("representation", ["response", "event", "item_completed"])
def test_codex_native_input_preserves_long_text_and_only_reads_indexed_record(
    tmp_path: Path, representation: str
) -> None:
    text = (
        " \t用户: keep this literal token=synthetic-test-credential\n" + "🌷漢字 " * 20_000 + "\n "
    )
    content = [{"type": "input_text", "text": text}]
    if representation == "response":
        record = _codex(content)
    elif representation == "event":
        record = {"type": "event_msg", "payload": {"type": "user_message", "message": text}}
    else:
        record = {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "UserMessage", "content": content},
            },
        }
    path = _session(tmp_path, _codex(content), record, _codex(content))

    result = read_trigger_text(session_path=path, source="codex", line=2)

    assert result == TriggerText((text,))


def test_codex_mixed_provenance_keeps_human_blocks_and_excludes_context(tmp_path: Path) -> None:
    texts = ("First\nparagraph", "generated skill instructions", "", " ", "Last")
    path = _session(
        tmp_path,
        _codex(
            [{"type": "input_text", "text": text} for text in texts],
            tags=[
                "user.text",
                "skills.selected_skill_instructions",
                "user.text",
                "user.text",
                "user.text",
            ],
        ),
    )

    assert read_trigger_text(session_path=path, source="codex", line=1) == TriggerText(
        (texts[0], "", " ", texts[-1])
    )


@pytest.mark.parametrize("representation", ["retained", "reply_envelope"])
def test_codex_question_replies_preserve_answers_without_questions(
    tmp_path: Path, representation: str
) -> None:
    answers = (" Keep A.\n", "", "选择 B")
    questions = [{"question": "Agent question", "answer": answer} for answer in answers]
    if representation == "retained":
        record = {
            "type": "retained_context",
            "payload": {"type": "verified_answer", "questions": questions},
        }
    else:
        replies = [
            question | {"questionItemId": str(index)} for index, question in enumerate(questions)
        ]
        text = (
            "<send_user_message_question_reply>"
            + json.dumps(replies)
            + "</send_user_message_question_reply>"
        )
        record = _codex([{"type": "input_text", "text": text}])
    path = _session(tmp_path, record)

    assert read_trigger_text(session_path=path, source="codex", line=1) == TriggerText(answers)


@pytest.mark.parametrize(
    ("source", "record"),
    [
        ("codex", _codex([{"type": "skill", "name": "review", "path": "/skills/review"}])),
        ("codex", _codex([{"type": "input_image", "image_url": "opaque attachment"}])),
        ("codex", _codex([{"type": "input_audio", "data": "opaque attachment"}])),
        ("claude-code", _claude([{"type": "image", "source": {"data": "opaque attachment"}}])),
    ],
)
def test_recognized_nontext_human_actions_have_no_fabricated_quotation(
    tmp_path: Path, source: SourceName, record: dict[str, object]
) -> None:
    path = _session(tmp_path, record)

    assert read_trigger_text(session_path=path, source=source, line=1) == TriggerText(())


@pytest.mark.parametrize("as_blocks", [False, True])
def test_claude_preserves_text_and_ignores_tool_result_blocks(
    tmp_path: Path, *, as_blocks: bool
) -> None:
    text = "\n  Retry with 中文, token=synthetic-literal. \t"
    content = (
        [
            {"type": "text", "text": text},
            {"type": "tool_result", "content": "Agent tool output"},
            {"type": "image", "source": {"data": "opaque attachment"}},
            {"type": "text", "text": ""},
        ]
        if as_blocks
        else text
    )
    path = _session(tmp_path, _claude(content))

    assert read_trigger_text(session_path=path, source="claude-code", line=1) == TriggerText(
        (text, "") if as_blocks else (text,)
    )


def test_claude_agent_command_keeps_command_and_arguments_separate(tmp_path: Path) -> None:
    command = (
        "<command-name>/review</command-name>\n"
        "<command-message>Generated skill explanation</command-message>\n"
        "<command-args>  Inspect A\nand B.  </command-args>"
    )
    path = _session(tmp_path, _claude(command))

    assert read_trigger_text(session_path=path, source="claude-code", line=1) == TriggerText(
        ("/review", "  Inspect A\nand B.  ")
    )


def test_claude_client_local_command_requires_previous_physical_record(tmp_path: Path) -> None:
    notice = _claude(
        "<local-command-caveat>Client-local execution</local-command-caveat>",
        uuid="notice",
        isMeta=True,
    )
    command = _claude(
        "<command-name>/plugin</command-name>"
        "<command-message>plugin</command-message><command-args>update all</command-args>",
        parentUuid="notice",
    )
    path = _session(tmp_path, notice, command)

    result = read_trigger_text(session_path=path, source="claude-code", line=2)

    assert isinstance(result, TriggerReadError)
    assert "not a recognized human" in result.message


@pytest.mark.parametrize(
    ("source", "record"),
    [
        (
            "codex",
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": []},
            },
        ),
        ("codex", _codex([{"type": "input_text", "text": "# AGENTS.md instructions"}])),
        (
            "codex",
            _codex(
                [
                    {
                        "type": "input_text",
                        "text": "<skill><name>review</name><path>/skills/review</path>"
                        "Instructions</skill>",
                    }
                ]
            ),
        ),
        (
            "codex",
            _codex([{"type": "input_text", "text": "Summary"}])
            | {"metadata": {"compaction_output": True}},
        ),
        (
            "codex",
            _codex(
                [
                    {
                        "type": "input_text",
                        "text": "<user_shell_command><command>pwd</command>"
                        "<result>/tmp</result></user_shell_command>",
                    }
                ]
            ),
        ),
        ("codex", _codex([])),
        (
            "codex",
            {
                "type": "retained_context",
                "payload": {"type": "verified_answer", "questions": [{"question": "Q"}]},
            },
        ),
        ("claude-code", _claude("Generated context", isMeta=True)),
        ("claude-code", _claude("Summary", isCompactSummary=True)),
        ("claude-code", _claude("Side agent", isSidechain=True)),
        ("claude-code", _claude("Tool-owned text", sourceToolAssistantUUID="tool")),
        ("claude-code", _claude([{"type": "tool_result", "content": "Tool result"}])),
        ("claude-code", _claude("<local-command-stdout>Local result</local-command-stdout>")),
        (
            "claude-code",
            {"type": "assistant", "message": {"role": "assistant", "content": "Acknowledged"}},
        ),
        ("claude-code", {"type": "user"}),
        ("claude-code", {"type": "user", "message": {"role": "assistant", "content": "Mismatch"}}),
        ("claude-code", _claude(None)),
    ],
)
def test_nonhuman_or_unrecognized_records_cannot_be_committed_as_empty_quotes(
    tmp_path: Path, source: SourceName, record: dict[str, object]
) -> None:
    path = _session(tmp_path, record)

    assert isinstance(read_trigger_text(session_path=path, source=source, line=1), TriggerReadError)


@pytest.mark.parametrize(
    ("source", "record"),
    [
        (
            "codex",
            _codex(
                [
                    {"type": "input_text", "text": "Human request"},
                    {"type": "future_text", "text": "Unrecognized text schema"},
                ]
            ),
        ),
        (
            "codex",
            _codex(
                [
                    {"type": "input_text", "text": "Human request"},
                    {"type": "input_text", "text": "Unattributed"},
                ],
                tags=["user.text", "future.kind"],
            ),
        ),
        (
            "codex",
            _codex(
                [
                    {"type": "input_text", "text": "Human request"},
                    {"type": "input_text", "text": "<send_user_message_question_reply>malformed"},
                ]
            ),
        ),
        (
            "claude-code",
            _claude(
                [
                    {"type": "text", "text": "Human request"},
                    {"type": "future_attachment", "text": "Unrecognized text schema"},
                ]
            ),
        ),
        (
            "codex",
            _codex([{"type": "text", "text": "Human request"}, {"type": "text", "text": 42}]),
        ),
        (
            "claude-code",
            _claude([{"type": "text", "text": "Human request"}, {"type": "text"}]),
        ),
        (
            "codex",
            _codex(
                [{"type": "text", "text": "Human request"}, {"type": "future_attachment"}],
                tags=["user.text", "future.context"],
            ),
        ),
        (
            "claude-code",
            _claude(
                [
                    {"type": "text", "text": "Human request"},
                    {"type": "text", "text": "<command-name>partial"},
                ]
            ),
        ),
        ("claude-code", _claude([{"type": "text", "text": "Human request"}, None])),
    ],
)
def test_unsupported_parts_fail_instead_of_silently_dropping_potential_human_text(
    tmp_path: Path, source: SourceName, record: dict[str, object]
) -> None:
    path = _session(tmp_path, record)

    result = read_trigger_text(session_path=path, source=source, line=1)

    assert isinstance(result, TriggerReadError)
    assert "unsupported or malformed" in result.message


@pytest.mark.parametrize("raw", ["not json", "null", "[]", "42", ""])
def test_malformed_trigger_line_returns_error(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(raw + "\n", encoding="utf-8")

    assert isinstance(
        read_trigger_text(session_path=path, source="codex", line=1), TriggerReadError
    )


@pytest.mark.parametrize("line", [-1, 0, 2])
def test_trigger_line_outside_session_returns_error(tmp_path: Path, line: int) -> None:
    path = _session(tmp_path, _claude("Human request"))

    result = read_trigger_text(session_path=path, source="claude-code", line=line)

    assert isinstance(result, TriggerReadError)
    assert "outside" in result.message


@pytest.mark.parametrize("is_directory", [False, True])
def test_missing_or_unreadable_source_returns_error(tmp_path: Path, *, is_directory: bool) -> None:
    path = tmp_path if is_directory else tmp_path / "missing.jsonl"

    result = read_trigger_text(session_path=path, source="codex", line=1)

    assert isinstance(result, TriggerReadError)
    assert "could not read" in result.message


def test_source_decoding_matches_workspace_physical_line_numbering(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(
        b'not json\r\n{"type":"event_msg","payload":{"type":"user_message","message":"a\xffb"}}\r\n'
    )

    assert read_trigger_text(session_path=path, source="codex", line=2) == TriggerText(("a�b",))
