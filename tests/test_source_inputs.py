from __future__ import annotations

import json

import pytest

from prompt_diary.source_records import (
    CodexInput,
    HumanAttachment,
    InputPart,
    SkillInvocation,
    UnavailableInput,
    has_claude_human_input,
    is_claude_local_command,
    is_codex_compaction_output,
    parse_claude_input,
    parse_claude_input_text,
    parse_codex_input,
)


def _response(texts: list[str], tags: object = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text} for text in texts],
    }
    if tags is not None:
        payload["internal_chat_message_metadata_passthrough"] = {"content_item_kinds": tags}
    return {"type": "response_item", "payload": payload}


def _codex_parts(content: list[object], tags: object = None) -> dict[str, object]:
    payload: dict[str, object] = {"type": "message", "role": "user", "content": content}
    if tags is not None:
        payload["internal_chat_message_metadata_passthrough"] = {"content_item_kinds": tags}
    return {"type": "response_item", "payload": payload}


def test_metadata_preserves_order_and_exact_human_text_in_mixed_input() -> None:
    parsed = parse_codex_input(
        _response(
            ["  Please use $review.\n", "Generated instructions", "Do not publish."],
            ["user.text", "skills.selected_skill_instructions", "user.text"],
        )
    )

    assert parsed == CodexInput(
        (
            InputPart("human_input", "  Please use $review.\n", "user.text"),
            InputPart(
                "generated_context", "Generated instructions", "skills.selected_skill_instructions"
            ),
            InputPart("human_input", "Do not publish.", "user.text"),
        )
    )
    assert parsed is not None
    assert parsed.is_human_trigger


def test_generated_skill_is_not_a_human_trigger() -> None:
    parsed = parse_codex_input(_response(["Use the skill"], ["skills.selected_skill_instructions"]))
    assert parsed is not None
    assert not parsed.is_human_trigger


def test_native_compaction_summary_tag_is_context_not_a_human_request() -> None:
    text = "The human previously requested a review.\nNext, inspect the pending changes."

    parsed = parse_codex_input(_response([text], ["compaction.summary"]))

    assert parsed == CodexInput((InputPart("context_compaction", text, "compaction.summary"),))
    assert parsed is not None
    assert not parsed.is_human_trigger


def test_whole_message_compaction_marker_overrides_user_role_and_content_tags() -> None:
    record = _response(["Earlier human direction", "Next steps"], ["user.text", "user.text"])
    record["metadata"] = {"compaction_output": True, "client_authored": False}

    parsed = parse_codex_input(record)

    assert parsed == CodexInput(
        (
            InputPart("context_compaction", "Earlier human direction", "compaction.summary"),
            InputPart("context_compaction", "Next steps", "compaction.summary"),
        )
    )
    assert parsed is not None
    assert not parsed.is_human_trigger


@pytest.mark.parametrize("attachment_type", ["input_image", "local_image", "future_attachment"])
def test_whole_message_compaction_marker_does_not_invent_human_attachments_or_skill_choices(
    attachment_type: str,
) -> None:
    record: dict[str, object] = {
        "type": "response_item",
        "metadata": {"compaction_output": True},
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {"type": attachment_type, "image_url": "data:private"},
                {"type": "skill", "name": "review"},
            ],
        },
    }

    parsed = parse_codex_input(record)

    assert parsed == CodexInput((UnavailableInput(attachment_type), UnavailableInput("skill")))
    assert parsed is not None
    assert not parsed.is_human_trigger


@pytest.mark.parametrize(
    "metadata", [None, [], {}, {"compaction_output": "true"}, {"compaction_output": 1}]
)
def test_only_boolean_native_compaction_marker_changes_authorship(metadata: object) -> None:
    record = _response(["Please compact this conversation."], ["user.text"])
    record["metadata"] = metadata

    assert not is_codex_compaction_output(record)
    assert parse_codex_input(record) == CodexInput(
        (InputPart("human_input", "Please compact this conversation.", "user.text"),)
    )


@pytest.mark.parametrize("client_authored", [False, True])
def test_client_authored_does_not_override_source_provenance(*, client_authored: bool) -> None:
    record = _response(["I disagree. Keep the old scope."], ["user.text"])
    record["metadata"] = {"client_authored": client_authored}
    parsed = parse_codex_input(record)
    assert parsed == CodexInput(
        (InputPart("human_input", "I disagree. Keep the old scope.", "user.text"),)
    )


def test_reply_wrapper_separates_questions_from_exact_answers() -> None:
    replies = [
        {"question": "Publish?", "answer": "No.\nOnly review.", "questionItemId": "q1"},
        {"question": "Scope?", "answer": "", "questionItemId": "q2"},
    ]
    wrapper = (
        "<send_user_message_question_reply>\n"
        + json.dumps(replies)
        + "\n</send_user_message_question_reply>"
    )
    parsed = parse_codex_input(_response([wrapper], ["user.text"]))
    assert parsed == CodexInput(
        (
            InputPart("assistant_message", "Publish?", "question_reply.question"),
            InputPart("human_input", "No.\nOnly review.", "question_reply.answer"),
            InputPart("assistant_message", "Scope?", "question_reply.question"),
            InputPart("human_input", "", "question_reply.answer"),
        )
    )


@pytest.mark.parametrize("client_authored", [False, True])
def test_native_verified_answers_preserve_order_and_exact_human_correction(
    *, client_authored: bool
) -> None:
    question = "By next audit page fetch, do you mean the next results request?"
    answer = (
        "None of the above\nuser_note: Let us don't constrain on this. Stay simple. "
        "Viewing set timezone and all operations on the audit page as completely parallel "
        "requests, no any synchronization between them. Don't do any type of synchronization. "
        "Do you understand my point?"
    )
    record: dict[str, object] = {
        "timestamp": "2026-10-11T12:49:25.993Z",
        "ordinal": 144,
        "type": "retained_context",
        "payload": {
            "type": "verified_answer",
            "turn_id": "turn-1",
            "call_id": "call-1",
            "questions": [
                {"question": question, "answer": answer},
                {"question": "Anything else?", "answer": ""},
            ],
            "acceptance_order": 15,
        },
        "metadata": {"client_authored": client_authored},
    }

    parsed = parse_codex_input(record)

    assert parsed == CodexInput(
        (
            InputPart("assistant_message", question, "verified_answer.question"),
            InputPart("human_input", answer, "verified_answer.answer"),
            InputPart("assistant_message", "Anything else?", "verified_answer.question"),
            InputPart("human_input", "", "verified_answer.answer"),
        )
    )
    assert parsed is not None
    assert parsed.is_human_trigger


@pytest.mark.parametrize(
    "questions",
    [
        None,
        {},
        [],
        [None],
        [{"question": "Proceed?"}],
        [{"question": "Proceed?", "answer": None}],
        [{"question": 42, "answer": "No"}],
        [{"question": "Proceed?", "answer": "No", "source": "unknown"}],
        [{"question": "Proceed?", "answer": "No"}, {"question": "Scope?"}],
    ],
)
def test_malformed_native_verified_answer_does_not_certify_human_input(questions: object) -> None:
    assert (
        parse_codex_input(
            {
                "type": "retained_context",
                "payload": {"type": "verified_answer", "questions": questions},
            }
        )
        is None
    )


@pytest.mark.parametrize(
    ("record_type", "payload_type"),
    [
        ("retained_context", "unverified_answer"),
        ("retained_context", "unknown"),
        ("response_item", "verified_answer"),
        ("event_msg", "verified_answer"),
    ],
)
def test_question_answer_fields_without_native_verified_type_are_not_human_input(
    record_type: str, payload_type: str
) -> None:
    assert (
        parse_codex_input(
            {
                "type": record_type,
                "payload": {
                    "type": payload_type,
                    "questions": [{"question": "Proceed?", "answer": "No"}],
                },
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "body",
    [
        "not JSON",
        "[]",
        "{}",
        '[{"question":"Publish?","answer":null,"questionItemId":"q"}]',
        '[{"question":"Publish?","answer":"No"}]',
        '[{"question":"Publish?","answer":"No","questionItemId":"q","extra":true}]',
    ],
)
def test_unknown_reply_schema_is_not_misrepresented_as_human_prose(body: str) -> None:
    text = f"<send_user_message_question_reply>{body}</send_user_message_question_reply>"
    parsed = parse_codex_input(_response([text], ["user.text"]))
    assert parsed == CodexInput((InputPart("unknown", text, "user.text"),))
    assert parsed is not None
    assert not parsed.is_human_trigger


@pytest.mark.parametrize(
    "text",
    [
        '<send_user_message_question_reply>[{"question":"q","answer":"a","questionItemId":"i"}]',
        '<send_user_message_question_reply>[{"question":"q","answer":"a",'
        '"questionItemId":"i"}]</send_user_message_question_reply> trailing',
    ],
)
def test_partial_or_extended_reply_envelope_stays_unknown(text: str) -> None:
    assert parse_codex_input(_response([text])) == CodexInput((InputPart("unknown", text),))


@pytest.mark.parametrize("tags", [None, ["user.text"], ["shell.user_command"]])
def test_local_shell_command_and_result_contribute_no_collaboration_input(tags: object) -> None:
    text = (
        "<user_shell_command>\n<command>\ngit status\n</command>\n"
        "<result>\nExit code: 0\nclean\n</result>\n</user_shell_command>"
    )
    parsed = parse_codex_input(_response([text], tags))
    assert parsed == CodexInput(())
    assert parsed is not None
    assert not parsed.is_human_trigger


def test_local_shell_part_does_not_hide_adjacent_human_direction() -> None:
    shell = (
        "<user_shell_command><command>git status</command>"
        "<result>clean</result></user_shell_command>"
    )
    prose = "Please run `git status` and explain the result."
    assert parse_codex_input(
        _response([shell, prose], ["shell.user_command", "user.text"])
    ) == CodexInput((InputPart("human_input", prose, "user.text"),))


def test_malformed_shell_wrapper_remains_unknown() -> None:
    assert parse_codex_input(_response(["git status"], ["shell.user_command"])) == CodexInput(
        (InputPart("unknown", "git status", "shell.user_command"),)
    )


def test_shell_wrapper_without_result_preserves_unknown_source_text() -> None:
    text = "<user_shell_command><command>git status</command></user_shell_command>"
    parsed = parse_codex_input(_response([text], ["shell.user_command"]))

    assert parsed == CodexInput((InputPart("unknown", text, "shell.user_command"),))
    assert parsed is not None
    assert not parsed.is_human_trigger


@pytest.mark.parametrize("tags", [[], ["user.text", "user.text"], [42], "user.text"])
def test_conflicting_metadata_preserves_text_without_certifying_human_authorship(
    tags: object,
) -> None:
    parsed = parse_codex_input(_response(["Uncertain source"], tags))
    assert parsed == CodexInput((InputPart("unknown", "Uncertain source", "conflicting_metadata"),))
    assert parsed is not None
    assert not parsed.is_human_trigger


def test_unknown_metadata_does_not_fall_back_to_human() -> None:
    assert parse_codex_input(
        _response(["Future generated item"], ["future.context"])
    ) == CodexInput((InputPart("unknown", "Future generated item", "future.context"),))


@pytest.mark.parametrize("metadata", [[], "future metadata", 42])
def test_malformed_present_metadata_does_not_use_legacy_human_fallback(metadata: object) -> None:
    record: dict[str, object] = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Uncertain source"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
    }
    assert parse_codex_input(record) == CodexInput(
        (InputPart("unknown", "Uncertain source", "conflicting_metadata"),)
    )


def test_legacy_plain_text_and_complete_skill_context_remain_distinct() -> None:
    skill = (
        "<skill>\n<name>review</name>\n<path>/skills/review.md</path>\n"
        "Review instructions\n</skill>"
    )
    assert parse_codex_input(_response(["Use $review"])) == CodexInput(
        (InputPart("human_input", "Use $review"),)
    )
    assert parse_codex_input(_response([skill])) == CodexInput(
        (InputPart("generated_context", skill, "skills.selected_skill_instructions"),)
    )
    assert parse_codex_input(_response([skill], ["user.text"])) == CodexInput(
        (InputPart("unknown", skill, "user.text"),)
    )


def test_native_skill_selection_does_not_invent_a_human_quote() -> None:
    record: dict[str, object] = {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "UserMessage",
                "content": [
                    {"type": "text", "text": "Use this"},
                    {"type": "skill", "name": "review", "path": "/skills/review.md"},
                    {"type": "input_image", "image_url": "data:private-image"},
                ],
            },
        },
    }
    parsed = parse_codex_input(record)
    assert parsed == CodexInput(
        (
            InputPart("human_input", "Use this"),
            SkillInvocation("review", "/skills/review.md"),
            HumanAttachment("input_image"),
        )
    )


def test_legacy_user_event_works_without_source_metadata() -> None:
    record: dict[str, object] = {
        "type": "event_msg",
        "payload": {"type": "user_message", "message": "Continue"},
    }
    assert parse_codex_input(record) == CodexInput((InputPart("human_input", "Continue"),))


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        {
            "type": "event_msg",
            "payload": {"type": "item_completed", "item": {"type": "CommandExecution"}},
        },
    ],
)
def test_non_input_records_do_not_parse_as_human(record: dict[str, object]) -> None:
    assert parse_codex_input(record) is None


@pytest.mark.parametrize(
    "attachment_type",
    ["image", "input_image", "local_image", "audio", "input_audio", "future_attachment"],
)
@pytest.mark.parametrize("representation", ["response", "item_completed"])
def test_nontext_codex_user_blocks_are_opaque_without_a_media_type_allowlist(
    attachment_type: str, representation: str
) -> None:
    text = "  Inspect this attachment.\n保留原文。  "
    content: list[object] = [
        {"type": attachment_type, "path": "/missing/private.png", "data": "private bytes"},
        {"type": "text", "text": text},
    ]
    record = (
        _codex_parts(content)
        if representation == "response"
        else {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "UserMessage", "content": content},
            },
        }
    )

    parsed = parse_codex_input(record)

    assert parsed == CodexInput((HumanAttachment(attachment_type), InputPart("human_input", text)))
    assert parsed is not None
    assert parsed.is_human_trigger
    assert "private" not in repr(parsed)


@pytest.mark.parametrize("tag", ["user.image", "user.audio", "user.video", "user.attachment"])
def test_codex_opaque_user_provenance_does_not_require_matching_media_types(tag: str) -> None:
    parsed = parse_codex_input(_codex_parts([{"type": "local_image", "path": "private"}], [tag]))

    assert parsed == CodexInput((HumanAttachment("local_image"),))
    assert parsed is not None
    assert parsed.is_human_trigger


@pytest.mark.parametrize(
    "tag",
    [
        "user.text",
        "user.",
        "future.context",
        "compaction.summary",
        "skills.selected_skill_instructions",
    ],
)
def test_codex_nontext_block_cannot_override_nonhuman_or_incompatible_provenance(tag: str) -> None:
    parsed = parse_codex_input(_codex_parts([{"type": "future_attachment"}], [tag]))

    assert parsed == CodexInput((UnavailableInput("future_attachment"),))
    assert parsed is not None
    assert not parsed.is_human_trigger


@pytest.mark.parametrize("tags", [[], ["user.image", "user.text"], [42], "user.image"])
def test_conflicting_codex_metadata_does_not_certify_opaque_blocks(tags: object) -> None:
    parsed = parse_codex_input(_codex_parts([{"type": "future_attachment"}], tags))

    assert parsed == CodexInput((UnavailableInput("future_attachment"),))
    assert parsed is not None
    assert not parsed.is_human_trigger


@pytest.mark.parametrize(
    "part",
    [
        None,
        42,
        "untyped text",
        {},
        {"type": ""},
        {"type": " "},
        {"type": 42},
        {"type": []},
        {"type": {}},
        {"type": "text"},
        {"type": "text", "text": 42},
        {"type": "input_text", "text": None},
        {"type": "Text", "text": []},
        {"type": "skill"},
        {"type": "skill", "name": 42},
        {"type": "skill", "name": "review", "path": 42},
    ],
)
def test_malformed_codex_blocks_do_not_become_generic_human_attachments(part: object) -> None:
    parsed = parse_codex_input(_codex_parts([part]))

    assert parsed is not None
    assert not parsed.is_human_trigger
    assert not any(isinstance(item, HumanAttachment) for item in parsed.parts)


@pytest.mark.parametrize("text", ["Unclassified human text", None, 42, []])
def test_unknown_text_bearing_codex_blocks_cannot_be_dropped_as_opaque(text: object) -> None:
    parsed = parse_codex_input(_codex_parts([{"type": "future_attachment", "text": text}]))

    assert parsed is not None
    assert not parsed.is_human_trigger
    assert not any(isinstance(item, HumanAttachment) for item in parsed.parts)


def test_opaque_nontext_payload_does_not_expose_private_bytes() -> None:
    record: dict[str, object] = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "encrypted_content", "data": "private bytes"},
            ],
        },
    }
    assert parse_codex_input(record) == CodexInput((HumanAttachment("encrypted_content"),))


def test_claude_command_selection_and_output_are_distinct() -> None:
    command = (
        "<command-name>/plugin</command-name>\n"
        "<command-message>plugin</command-message>\n<command-args>update all</command-args>"
    )
    assert parse_claude_input_text(command) == (
        InputPart("human_command", "/plugin", "claude.command_name"),
        InputPart("generated_context", "plugin", "claude.command_message"),
        InputPart("human_input", "update all", "claude.command_args"),
    )
    assert (
        parse_claude_input_text("<local-command-stdout>Updated plugin</local-command-stdout>") == ()
    )


def _local_claude_pair() -> tuple[dict[str, object], dict[str, object]]:
    notice: dict[str, object] = {
        "type": "user",
        "uuid": "local-notice",
        "isMeta": True,
        "message": {
            "role": "user",
            "content": "<local-command-caveat>Local client execution.</local-command-caveat>",
        },
    }
    command: dict[str, object] = {
        "type": "user",
        "parentUuid": "local-notice",
        "message": {
            "role": "user",
            "content": "<command-name>/plugin</command-name>"
            "<command-message>plugin</command-message>"
            "<command-args>update all</command-args>",
        },
    }
    return notice, command


def test_claude_local_command_requires_linked_client_provenance_not_a_command_name() -> None:
    notice, command = _local_claude_pair()
    assert is_claude_local_command(command, notice)
    assert not is_claude_local_command(command)
    assert is_claude_local_command({"type": "system", "subtype": "local_command"})


@pytest.mark.parametrize(
    "notice_change",
    [
        {"type": "assistant"},
        {"uuid": "unrelated"},
        {"uuid": ""},
        {"uuid": None},
        {"promptId": "unrelated prompt"},
        {"isMeta": False},
        {"message": None},
        {
            "message": {
                "role": "assistant",
                "content": "<local-command-caveat>x</local-command-caveat>",
            }
        },
        {"message": {"role": "user", "content": "<local-command-caveat>partial"}},
        {"message": {"role": "user", "content": None}},
    ],
)
def test_uncertain_claude_local_notice_does_not_discard_a_request(
    notice_change: dict[str, object],
) -> None:
    notice, command = _local_claude_pair()
    assert not is_claude_local_command(command, notice | notice_change)


@pytest.mark.parametrize(
    "command_change",
    [
        {"type": "assistant"},
        {"sourceToolAssistantUUID": "agent-tool"},
        {"message": None},
        {"message": {"role": "assistant", "content": "irrelevant"}},
        {"message": {"role": "user", "content": "Please review the plugin update."}},
        {"message": {"role": "user", "content": "<command-name>/review</command-name>"}},
        {"message": {"role": "user", "content": ["uncertain shape"]}},
    ],
)
def test_linked_local_notice_does_not_discard_other_content(
    command_change: dict[str, object],
) -> None:
    notice, command = _local_claude_pair()
    assert not is_claude_local_command(command | command_change, notice)


@pytest.mark.parametrize(
    "text", ["<command-name>/plugin</command-name>", "<local-command-stdout>partial"]
)
def test_partial_claude_command_wrappers_remain_unknown(text: str) -> None:
    assert parse_claude_input_text(text) == (InputPart("unknown", text),)


@pytest.mark.parametrize("content", [None, {"type": "text", "text": "Unsupported container"}])
def test_unsupported_claude_content_container_does_not_invent_human_action(content: object) -> None:
    assert not has_claude_human_input(content)


def test_malformed_claude_blocks_do_not_turn_tool_results_into_human_input() -> None:
    content: list[object] = [
        None,
        "untyped text",
        {"type": "tool_result", "content": "Command finished"},
    ]
    assert not has_claude_human_input(content)

    content.append({"type": "text", "text": "Please retry."})
    assert has_claude_human_input(content)


def test_image_only_claude_content_is_a_human_action_without_transcribed_text() -> None:
    assert has_claude_human_input(
        [{"type": "image", "source": {"type": "base64", "data": "unread image bytes"}}]
    )


@pytest.mark.parametrize("attachment_type", ["image", "local_image", "future_attachment"])
def test_nontext_claude_user_blocks_are_opaque_without_a_media_type_allowlist(
    attachment_type: str,
) -> None:
    content = [{"type": attachment_type, "data": "private bytes", "path": "/private/attachment"}]
    record = {"type": "user", "message": {"role": "user", "content": content}}

    parsed = parse_claude_input(record)

    assert parsed is not None
    assert parsed.parts == (HumanAttachment(attachment_type),)
    assert parsed.is_human_trigger
    assert has_claude_human_input(content)
    assert "private" not in repr(parsed)


@pytest.mark.parametrize(
    "part",
    [
        None,
        {},
        {"type": ""},
        {"type": " "},
        {"type": 42},
        {"type": []},
        {"type": {}},
        {"type": "text"},
        {"type": "text", "text": 42},
        {"type": "future_attachment", "text": "Unclassified human text"},
        {"type": "future_attachment", "text": None},
        {"type": "tool_result", "content": "Agent tool output"},
    ],
)
def test_malformed_or_tool_owned_claude_blocks_do_not_become_human_attachments(
    part: object,
) -> None:
    content = [part]
    record = {"type": "user", "message": {"role": "user", "content": content}}

    parsed = parse_claude_input(record)

    assert parsed is not None
    assert not parsed.is_human_trigger
    assert not any(isinstance(item, HumanAttachment) for item in parsed.parts)
    assert not has_claude_human_input(content)


@pytest.mark.parametrize(
    "flags",
    [
        {"isMeta": True},
        {"isSidechain": True},
        {"isCompactSummary": True},
        {"sourceToolAssistantUUID": "agent-tool"},
    ],
)
def test_generic_claude_attachments_do_not_override_nonhuman_record_provenance(
    flags: dict[str, object],
) -> None:
    record = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "future_attachment"}]},
        **flags,
    }

    assert parse_claude_input(record) is None
