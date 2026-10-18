from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis import DailySynthesisRunner
from prompt_diary.generate.pipeline import GeneratePipelineRunner, build_generation_plan
from prompt_diary.generate.rendering import RenderingRunner
from prompt_diary.models import JsonObject, PrepareResult, SourceName, SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target
from tests.agent_fakes import FakeAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentConfig, AgentTurnResult

_TIMESTAMP = "2026-05-12T01:00:00Z"
_RESET = (
    "A previous agent produced the plan below to accomplish the user's task. Plan: implement X."
)


def _response(text: str, role: str = "user") -> JsonObject:
    return {
        "type": "response_item",
        "timestamp": _TIMESTAMP,
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        },
    }


def _event(text: str, *, timestamp: str = _TIMESTAMP) -> JsonObject:
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {"type": "user_message", "message": text},
    }


def _claude(content: object) -> JsonObject:
    return cast(
        "JsonObject",
        {"type": "user", "timestamp": _TIMESTAMP, "message": {"role": "user", "content": content}},
    )


@pytest.mark.parametrize(
    "text",
    [
        "<environment_context>machine context</environment_context>",
        "# AGENTS.md instructions for project",
        "<turn_aborted>interrupted</turn_aborted>",
        "<subagent_notification>done</subagent_notification>",
        "<INSTRUCTIONS>machine context</INSTRUCTIONS>",
        _RESET,
    ],
)
@pytest.mark.parametrize("force_full_parse", [False, True])
def test_codex_machine_responses_and_echoes_never_create_report_targets(
    tmp_path: Path, text: str, *, force_full_parse: bool
) -> None:
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    records.extend(
        [_response("  \n" + text), _event("  \n" + text), _response("machine result", "assistant")]
    )

    result = _prepare(tmp_path, records)

    assert result.session_count == 0
    assert [task.kind for task in build_generation_plan(result.workspace_path).tasks] == [
        "daily_synthesis",
        "rendering",
    ]


def test_malformed_probe_content_still_filters_machine_prefix_in_full_parser(
    tmp_path: Path,
) -> None:
    record = _response(_RESET)
    payload = cast("JsonObject", record["payload"])
    payload["content"] = [42, {"type": "input_text", "text": _RESET}]

    assert _prepare(tmp_path, [record, _event(_RESET)]).session_count == 0


@pytest.mark.parametrize("whole_message_marker", [False, True])
@pytest.mark.parametrize("force_full_parse", [False, True])
def test_source_marked_compaction_summary_does_not_create_a_human_turn(
    tmp_path: Path, *, whole_message_marker: bool, force_full_parse: bool
) -> None:
    summary = _response("The user previously requested review; next inspect the patch.")
    if whole_message_marker:
        summary["metadata"] = {"compaction_output": True}
    else:
        summary = _with_kinds(summary, ["compaction.summary"])
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    records.extend([summary, _response("Reviewed the patch.", "assistant")])

    assert _prepare(tmp_path, records).session_count == 0


def test_reset_bootstrap_preserves_later_human_followups(tmp_path: Path) -> None:
    result = _prepare(
        tmp_path,
        [
            _response(_RESET),
            _event(_RESET),
            _response("machine implementation", "assistant"),
            _response("Continue"),
            _event("Continue"),
            _response("human-directed result", "assistant"),
            _response("Approved; implement the remaining change"),
        ],
    )

    assert _spans(result) == [(4, 6), (7, 7)]


@pytest.mark.parametrize(
    "text",
    [
        "<subagent_notification>Worker completed the task.</subagent_notification>",
        "<turn_aborted>The human interrupted execution.</turn_aborted>",
    ],
)
@pytest.mark.parametrize("representation", ["response", "event", "both"])
def test_source_results_stay_in_previous_turn_while_next_setup_is_excluded(
    tmp_path: Path, text: str, representation: str
) -> None:
    reactions: list[JsonObject] = []
    if representation in ("response", "both"):
        reactions.append(_response(text))
    if representation in ("event", "both"):
        reactions.append(_event(text))
    records: list[JsonObject | str] = [
        _response("Perform the task"),
        *reactions,
        {"type": "event_msg", "payload": {"type": "task_started"}},
        _response("<environment_context>Next turn environment.</environment_context>"),
        _event("<environment_context>Next turn environment.</environment_context>"),
        {"type": "turn_context", "payload": {}},
        _response("Review the result"),
    ]

    result = _prepare(tmp_path, records)

    assert _spans(result) == [(1, 1 + len(reactions)), (len(records), len(records))]


def test_bootstrap_only_report_generates_without_model_calls(tmp_path: Path) -> None:
    prepared = _prepare(
        tmp_path, [_response(_RESET), _event(_RESET), _response("done", "assistant")]
    )

    def reject_model_call(prompt: str, config: AgentConfig) -> AgentTurnResult:
        del prompt, config
        pytest.fail("Machine-only history must not call a model")

    factory = FakeAgentSessionFactory(script=reject_model_call)
    result = asyncio.run(
        GeneratePipelineRunner(
            phase_runners={
                "daily_synthesis": DailySynthesisRunner(factory),
                "rendering": RenderingRunner(),
            }
        ).run(
            workspace_path=prepared.workspace_path,
            plan=build_generation_plan(prepared.workspace_path),
        )
    )

    assert result.ok
    assert factory.runners == []
    assert (prepared.workspace_path / "report.md").is_file()


@pytest.mark.parametrize("flags", [{"isMeta": True}, {"isCompactSummary": True}])
@pytest.mark.parametrize("force_full_parse", [False, True])
def test_claude_machine_flags_are_not_human_triggers(
    tmp_path: Path, flags: JsonObject, *, force_full_parse: bool
) -> None:
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    records.append({**_claude("generated summary"), **flags})

    assert _prepare(tmp_path, records, source="claude-code").session_count == 0


def test_claude_tool_only_messages_do_not_hide_adjacent_human_messages(tmp_path: Path) -> None:
    tool_result: JsonObject = {"type": "tool_result", "tool_use_id": "tool-1", "content": "done"}
    tool_only = _claude([tool_result])
    mixed = _claude([tool_result, {"type": "text", "text": "Please continue"}])
    result = _prepare(
        tmp_path, [tool_only, mixed, _claude("Continue"), _claude("Approved")], source="claude-code"
    )

    assert _spans(result) == [(2, 2), (3, 3), (4, 4)]


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([_response("first"), _response("second")], [(1, 1), (2, 2)]),
        ([_response("same"), _response("same")], [(1, 1), (2, 2)]),
        ([_response("first"), _event("second")], [(1, 1), (2, 2)]),
        ([_response("same"), _event("same"), _event("same")], [(1, 2), (3, 3)]),
        ([_response("same"), _event("same", timestamp="2026-05-12T01:00:00.100Z")], [(1, 2)]),
        (
            [_response("same"), _event("same", timestamp="2026-05-12T01:00:00.101Z")],
            [(1, 1), (2, 2)],
        ),
        ([_event("same"), _response("same")], [(1, 1), (2, 2)]),
        ([_response("same"), _response("reaction", "assistant"), _event("same")], [(1, 2), (3, 3)]),
    ],
)
def test_only_adjacent_confirmed_echoes_share_a_prepared_turn(
    tmp_path: Path, records: list[JsonObject], expected: list[tuple[int, int]]
) -> None:
    assert _spans(_prepare(tmp_path, list(records))) == expected


def _with_kinds(record: JsonObject, kinds: list[str]) -> JsonObject:
    payload = cast("JsonObject", record["payload"])
    payload["internal_chat_message_metadata_passthrough"] = cast(
        "JsonObject", {"content_item_kinds": kinds}
    )
    return record


def _native_user(content: list[JsonObject]) -> JsonObject:
    return cast(
        "JsonObject",
        {
            "type": "event_msg",
            "timestamp": _TIMESTAMP,
            "payload": {
                "type": "item_completed",
                "item": {"type": "UserMessage", "content": content},
            },
        },
    )


def test_selected_skill_body_stays_with_human_invocation_and_preserves_source(
    tmp_path: Path,
) -> None:
    invocation = "Please review with $review"
    records: list[JsonObject | str] = [
        _with_kinds(_response(invocation), ["user.text"]),
        _native_user(
            [
                {"type": "text", "text": invocation},
                {"type": "skill", "name": "review", "path": "/skills/review.md"},
            ]
        ),
        _with_kinds(_response("Skill instructions"), ["skills.selected_skill_instructions"]),
        _response("Review complete", "assistant"),
        _response("Please fix the finding"),
        _response("Fixed", "assistant"),
    ]
    result = _prepare(tmp_path, records)
    assert _spans(result) == [(1, 4), (5, 6)]
    copied = next(result.workspace_path.glob("projects/*/sessions/codex/*.jsonl"))
    assert copied.read_bytes() == (tmp_path / "source/session.jsonl").read_bytes()


def test_native_skill_selection_without_text_is_a_real_trigger(tmp_path: Path) -> None:
    records: list[JsonObject | str] = [
        _native_user([{"type": "skill", "name": "review", "path": "/skills/review.md"}]),
        _with_kinds(_response("Skill instructions"), ["skills.selected_skill_instructions"]),
        _response("Reviewed", "assistant"),
    ]
    assert _spans(_prepare(tmp_path, records)) == [(1, 3)]


@pytest.mark.parametrize("force_full_parse", [False, True])
def test_generated_and_unknown_context_never_create_human_turns_even_via_echo(
    tmp_path: Path, *, force_full_parse: bool
) -> None:
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    for tag in ["skills.selected_skill_instructions", "future.context"]:
        records.extend(
            [
                _with_kinds(_response("Source supplied text"), [tag]),
                _event("Source supplied text"),
            ]
        )
    assert _prepare(tmp_path, records).session_count == 0


def test_mixed_metadata_keeps_human_trigger_without_turn_for_context(tmp_path: Path) -> None:
    record = _response("unused")
    payload = cast("JsonObject", record["payload"])
    payload["content"] = [
        {"type": "input_text", "text": "Generated skill instructions"},
        {"type": "input_text", "text": "Review only; do not implement."},
    ]
    _with_kinds(record, ["skills.selected_skill_instructions", "user.text"])
    assert _spans(_prepare(tmp_path, [record, _response("Reviewed", "assistant")])) == [(1, 2)]


def test_adjacent_mixed_context_human_turn_is_never_trimmed_as_scaffolding(tmp_path: Path) -> None:
    mixed = _response("unused")
    payload = cast("JsonObject", mixed["payload"])
    payload["content"] = [
        {"type": "input_text", "text": "# AGENTS.md supplied context"},
        {"type": "input_text", "text": "Please fix it"},
    ]
    _with_kinds(mixed, ["agents_md.instructions", "user.text"])
    assert _spans(_prepare(tmp_path, [mixed, _response("Then review it")])) == [(1, 1), (2, 2)]


def test_skill_selection_adds_human_input_beyond_a_generated_text_echo(tmp_path: Path) -> None:
    records: list[JsonObject | str] = [
        _with_kinds(_response("Selected review"), ["skills.selected_skill_instructions"]),
        _native_user(
            [
                {"type": "text", "text": "Selected review"},
                {"type": "skill", "name": "review", "path": "/skills/review.md"},
            ]
        ),
        _response("Reviewed", "assistant"),
    ]
    assert _spans(_prepare(tmp_path, records)) == [(2, 3)]


def test_known_native_image_is_a_human_action_without_text(tmp_path: Path) -> None:
    assert _spans(
        _prepare(
            tmp_path,
            [
                _native_user([{"type": "image", "url": "data:private"}]),
                _response("Inspected the image", "assistant"),
            ],
        )
    ) == [(1, 2)]


@pytest.mark.parametrize("force_full_parse", [False, True])
def test_linked_claude_local_commands_do_not_create_report_targets(
    tmp_path: Path, *, force_full_parse: bool
) -> None:
    command = (
        "<command-name>/plugin</command-name>\n"
        "<command-message>plugin</command-message>\n<command-args></command-args>"
    )
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    records.extend(
        [
            {
                **_claude("<local-command-caveat>Local execution.</local-command-caveat>"),
                "uuid": "local-notice",
                "isMeta": True,
            },
            {**_claude(command), "parentUuid": "local-notice"},
            _claude("<local-command-stdout>Updated 1 plugin</local-command-stdout>"),
        ]
    )
    result = _prepare(tmp_path, records, source="claude-code")
    assert result.session_count == 0
    assert [task.kind for task in build_generation_plan(result.workspace_path).tasks] == [
        "daily_synthesis",
        "rendering",
    ]


def test_reply_is_a_trigger_but_local_shell_execution_does_not_split_it(tmp_path: Path) -> None:
    reply = (
        '<send_user_message_question_reply>[{"question":"Proceed?",'
        '"answer":"Review only","questionItemId":"q"}]</send_user_message_question_reply>'
    )
    shell = (
        "<user_shell_command><command>git status</command>"
        "<result>clean</result></user_shell_command>"
    )
    records: list[JsonObject | str] = [
        _with_kinds(_response(reply), ["user.text"]),
        _response("Reviewed", "assistant"),
        _with_kinds(_response(shell), ["shell.user_command"]),
        _response("Status explained", "assistant"),
    ]
    prepared = _prepare(tmp_path, records)
    assert _spans(prepared) == [(1, 4)]
    copied = next(prepared.workspace_path.glob("projects/*/sessions/codex/*.jsonl"))
    assert copied.read_bytes() == (tmp_path / "source/session.jsonl").read_bytes()


@pytest.mark.parametrize("force_full_parse", [False, True])
@pytest.mark.parametrize("include_followup", [False, True])
def test_native_verified_answer_starts_one_turn_without_recounting_tool_delivery(
    tmp_path: Path, *, force_full_parse: bool, include_followup: bool
) -> None:
    answer = "None of the above\nuser_note: Keep these requests independent."
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    records.extend(
        [
            {
                "type": "response_item",
                "timestamp": _TIMESTAMP,
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "call-1",
                    "arguments": '{"questions":[{"id":"timing","question":"Synchronize?"}]}',
                },
            },
            {
                "type": "retained_context",
                "timestamp": _TIMESTAMP,
                "payload": {
                    "type": "verified_answer",
                    "turn_id": "turn-1",
                    "call_id": "call-1",
                    "questions": [{"question": "Synchronize?", "answer": answer}],
                    "acceptance_order": 15,
                },
            },
            {
                "type": "response_item",
                "timestamp": _TIMESTAMP,
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": json.dumps({"answers": {"timing": {"answers": [answer]}}}),
                },
            },
            _response("The requests will remain independent.", "assistant"),
        ]
    )
    if include_followup:
        records.append(_response("Please implement it."))

    prepared = _prepare(tmp_path, records)

    offset = int(force_full_parse)
    expected = [(2 + offset, 4 + offset)]
    if include_followup:
        expected.append((5 + offset, 5 + offset))
    assert _spans(prepared) == expected
    copied = next(prepared.workspace_path.glob("projects/*/sessions/codex/*.jsonl"))
    assert copied.read_bytes() == (tmp_path / "source/session.jsonl").read_bytes()


@pytest.mark.parametrize("force_full_parse", [False, True])
@pytest.mark.parametrize("payload_type", ["verified_answer", "unknown"])
def test_unknown_retained_context_does_not_create_a_human_turn(
    tmp_path: Path, payload_type: str, *, force_full_parse: bool
) -> None:
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    records.append(
        {
            "type": "retained_context",
            "timestamp": _TIMESTAMP,
            "payload": {
                "type": payload_type,
                "questions": [{"question": "Synchronize?", "answer": None}],
            },
        }
    )

    assert _prepare(tmp_path, records).session_count == 0


@pytest.mark.parametrize("force_full_parse", [False, True])
def test_local_shell_commands_and_echoes_never_create_extraction_tasks(
    tmp_path: Path, *, force_full_parse: bool
) -> None:
    shell = (
        "<user_shell_command><command>git status</command>"
        "<result>clean</result></user_shell_command>"
    )
    records: list[JsonObject | str] = ["malformed"] if force_full_parse else []
    records.extend(
        [
            _with_kinds(_response(shell), ["shell.user_command"]),
            _event(shell),
            _native_user([{"type": "text", "text": shell}]),
        ]
    )
    result = _prepare(tmp_path, records)
    assert result.session_count == 0
    assert [task.kind for task in build_generation_plan(result.workspace_path).tasks] == [
        "daily_synthesis",
        "rendering",
    ]


def test_claude_slash_request_and_plain_followup_survive_local_notice(tmp_path: Path) -> None:
    request = (
        "<command-name>/review</command-name><command-message>Review skill</command-message>"
        "<command-args>this patch</command-args>"
    )
    records: list[JsonObject | str] = [
        _claude(request),
        {
            **_claude("<local-command-caveat>Local execution.</local-command-caveat>"),
            "uuid": "local-notice",
            "isMeta": True,
        },
        {**_claude("Please explain the status."), "parentUuid": "local-notice"},
        _claude("<local-command-stdout>clean</local-command-stdout>"),
    ]
    assert _spans(_prepare(tmp_path, records, source="claude-code")) == [(1, 2), (3, 4)]


@pytest.mark.parametrize("kinds", [["user.text", "user.text"], ["future.context"]])
def test_conflicting_or_unknown_authorship_does_not_create_a_report_target(
    tmp_path: Path, kinds: list[str]
) -> None:
    assert _prepare(tmp_path, [_with_kinds(_response("Uncertain"), kinds)]).session_count == 0


def test_old_prepared_schema_requires_explicit_refresh_without_mutating_snapshot(
    tmp_path: Path,
) -> None:
    prepared = _prepare(tmp_path, [_response("Review")])
    metadata_path = prepared.workspace_path / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = 5
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    before = {
        path: path.read_bytes() for path in prepared.workspace_path.rglob("*") if path.is_file()
    }
    source_specs = (SourceSpec(source="codex", root=tmp_path / "source"),)
    with pytest.raises(PromptDiaryError, match=r"prepare .*--force"):
        prepare_workspace(
            prepared.target, reports_root=tmp_path / "reports", source_specs=source_specs
        )

    assert {path: path.read_bytes() for path in before} == before
    refreshed = prepare_workspace(
        prepared.target, reports_root=tmp_path / "reports", source_specs=source_specs, force=True
    )
    assert _spans(refreshed) == [(1, 1)]


def _prepare(
    tmp_path: Path, records: list[JsonObject | str], *, source: SourceName = "codex"
) -> PrepareResult:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "session.jsonl").write_text(
        "".join(
            (json.dumps(record) if isinstance(record, dict) else record) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="UTC",
        now=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    return prepare_workspace(
        target,
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source=source, root=source_root),),
    )


def _spans(result: PrepareResult) -> list[tuple[int, int]]:
    index = next((result.workspace_path / "projects").glob("*/sessions.index.jsonl"))
    row = json.loads(index.read_text(encoding="utf-8"))
    return [(turn["turn_start_line"], turn["turn_end_line"]) for turn in row["turns"]]
