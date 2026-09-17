from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import pytest

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
