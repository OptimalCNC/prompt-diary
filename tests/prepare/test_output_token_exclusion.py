from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import pytest

from prompt_diary.models import JsonObject, JsonValue, PrepareResult, SourceName, SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("source", ["codex", "claude-code"])
@pytest.mark.parametrize("tokens", [0, 1, 99, 100, 101])
def test_prepare_filters_native_output_tokens_before_copying_and_indexing(
    tmp_path: Path, source: SourceName, tokens: int
) -> None:
    records = _turn(source, tokens)
    if tokens == 0 and source == "codex":
        records.pop(1)  # An interrupted request with confirmed zero usage has no agent reply.
        records[-1]["payload"] = {"type": "turn_aborted"}
    path = _write(tmp_path, source, "session", records)
    original = path.read_bytes()

    result = _prepare(tmp_path, source)

    assert result.session_count == result.project_count == int(tokens >= 100)
    assert path.read_bytes() == original
    copied = list(result.workspace_path.glob("projects/*/sessions/*/*.jsonl"))
    audit = _json(result.audit_path)
    if tokens < 100:
        assert copied == []
        assert list((result.workspace_path / "projects").iterdir()) == []
        assert audit["sessions"] == []
    else:
        assert copied[0].read_bytes() == original
        assert set(_rows(result)) == {"session"}
        assert len(cast("list[JsonValue]", audit["sessions"])) == 1


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_threshold_applies_to_each_turn_instead_of_the_session_total(
    tmp_path: Path, source: SourceName
) -> None:
    first = _turn(source, 60)
    second = _turn(source, 60, at="2026-05-12T02:00:00Z", total=120)
    _write(tmp_path, source, "session", [*first, *second])

    assert _prepare(tmp_path, source).session_count == 0


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_one_large_turn_keeps_all_of_the_dates_turns(tmp_path: Path, source: SourceName) -> None:
    first = _turn(source, 5)
    second = _turn(source, 100, at="2026-05-12T02:00:00Z", total=105)
    _write(tmp_path, source, "session", [*first, *second])

    result = _prepare(tmp_path, source)

    assert result.session_count == 1
    assert len(cast("list[JsonValue]", _rows(result)["session"]["turns"])) == 2


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_other_dates_do_not_protect_small_target_date_turns(
    tmp_path: Path, source: SourceName
) -> None:
    yesterday = _turn(source, 1000, at="2026-05-11T01:00:00Z")
    today = _turn(source, 5, total=1005)
    tomorrow = _turn(source, 1000, at="2026-05-13T01:00:00Z", total=2005)
    _write(tmp_path, source, "session", [*yesterday, *today, *tomorrow])

    assert _prepare(tmp_path, source).session_count == 0


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_post_midnight_reactions_belong_to_the_human_trigger_date(
    tmp_path: Path, source: SourceName
) -> None:
    # Asia/Shanghai midnight is 16:00 UTC. The response is on the following local day.
    records = _turn(source, 100, at="2026-05-12T15:59:59Z")
    for record in records[1:]:
        record["timestamp"] = "2026-05-12T16:01:00Z"
    _write(tmp_path, source, "session", records)

    assert _prepare(tmp_path, source).session_count == 1


def test_codex_counts_all_responses_once_even_with_duplicate_usage_snapshots(
    tmp_path: Path,
) -> None:
    records = [
        _human("codex"),
        _assistant("codex"),
        _usage(40, 40),
        _usage(40, 40),
        _assistant("codex"),
        _usage(80, 40),
        _usage(80, 40),
        _terminal(),
    ]
    _write(tmp_path, "codex", "session", records)

    assert _prepare(tmp_path, "codex").session_count == 0


def test_codex_sums_multiple_responses_within_one_human_turn(tmp_path: Path) -> None:
    records = [
        _human("codex"),
        _assistant("codex"),
        _usage(60, 60),
        _assistant("codex"),
        _usage(120, 60),
        _terminal(),
    ]
    _write(tmp_path, "codex", "session", records)

    assert _prepare(tmp_path, "codex").session_count == 1


def test_codex_user_message_echo_does_not_split_the_turn(tmp_path: Path) -> None:
    records = _turn("codex", 5)
    records.insert(
        1,
        {
            "type": "event_msg",
            "timestamp": records[0]["timestamp"],
            "payload": {"type": "user_message", "message": "Task"},
        },
    )
    _write(tmp_path, "codex", "session", records)

    assert _prepare(tmp_path, "codex").session_count == 0


def test_claude_sums_distinct_messages_and_uses_maximum_for_repeated_message_ids(
    tmp_path: Path,
) -> None:
    first = _assistant("claude-code", tokens=30, identity="first")
    updated = _assistant("claude-code", tokens=50, identity="first")
    other = _assistant("claude-code", tokens=40, identity="second")
    _write(tmp_path, "claude-code", "session", [_human("claude-code"), first, updated, other])

    assert _prepare(tmp_path, "claude-code").session_count == 0  # 50 + 40, not 30 + 50 + 40.

    _write(tmp_path, "claude-code", "session", [_human("claude-code"), updated, updated])
    assert _prepare(tmp_path, "claude-code", force=True).session_count == 0

    other = _assistant("claude-code", tokens=50, identity="second")
    _write(tmp_path, "claude-code", "session", [_human("claude-code"), updated, other])
    assert _prepare(tmp_path, "claude-code", force=True).session_count == 1


def test_claude_uses_record_uuid_when_message_id_is_absent(tmp_path: Path) -> None:
    assistant = _assistant("claude-code", tokens=5)
    del cast("JsonObject", assistant["message"])["id"]
    assistant["uuid"] = "assistant-uuid"
    _write(tmp_path, "claude-code", "session", [_human("claude-code"), assistant, assistant])

    assert _prepare(tmp_path, "claude-code").session_count == 0


@pytest.mark.parametrize("source", ["codex", "claude-code"])
@pytest.mark.parametrize("tokens", [None, -1, True, 1.5, "5"])
def test_missing_or_invalid_native_usage_is_retained(
    tmp_path: Path, source: SourceName, tokens: JsonValue
) -> None:
    records = _turn(source, 5)
    if source == "codex":
        records[2] = _usage(tokens, tokens)
    else:
        records[1] = _assistant(source, tokens=tokens)
    _write(tmp_path, source, "session", records)

    assert _prepare(tmp_path, source).session_count == 1


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_a_turn_without_usage_keeps_the_session(tmp_path: Path, source: SourceName) -> None:
    _write(tmp_path, source, "session", [*_turn(source, 5), _human(source, "2026-05-12T02:00:00Z")])

    assert _prepare(tmp_path, source).session_count == 1


@pytest.mark.parametrize("case", ["missing-baseline", "reset", "stale", "open", "zero-with-output"])
def test_unproven_codex_usage_does_not_exclude_a_session(tmp_path: Path, case: str) -> None:
    records = _turn("codex", 5)
    if case == "missing-baseline":
        records[2] = _usage(1005, 5)
    elif case == "reset":
        records.insert(2, _usage(50, 50))
    elif case == "stale":
        records.insert(3, _assistant("codex"))
    elif case == "open":
        records.pop()
    else:
        records[2] = _usage(0, 0)
    _write(tmp_path, "codex", "session", records)

    assert _prepare(tmp_path, "codex").session_count == 1


def test_codex_can_use_a_baseline_snapshot_before_the_first_trigger(tmp_path: Path) -> None:
    _write(tmp_path, "codex", "session", [_usage(1000, 50), *_turn("codex", 5, total=1005)])

    assert _prepare(tmp_path, "codex").session_count == 0


@pytest.mark.parametrize("has_output_usage", [False, True])
def test_codex_context_only_token_events_do_not_supply_output_counts(
    tmp_path: Path, *, has_output_usage: bool
) -> None:
    records = _turn("codex", 5)
    context_only = _usage(5, 5)
    cast("JsonObject", context_only["payload"])["info"] = None
    if has_output_usage:
        records.insert(2, context_only)
    else:
        records[2] = context_only
    _write(tmp_path, "codex", "session", records)

    assert _prepare(tmp_path, "codex").session_count == int(not has_output_usage)


@pytest.mark.parametrize("case", ["malformed", "untimestamped-trigger"])
@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_ambiguous_session_records_keep_low_usage_sessions(
    tmp_path: Path, source: SourceName, case: str
) -> None:
    records: list[JsonObject | str] = list(_turn(source, 5))
    if case == "malformed":
        records.insert(1, "not JSON")
    else:
        trigger = _human(source)
        del trigger["timestamp"]
        records.insert(1, trigger)
    _write(tmp_path, source, "session", records)

    assert _prepare(tmp_path, source).session_count == 1


@pytest.mark.parametrize("malformation", ["no-message", "no-id", "no-usage"])
def test_incomplete_claude_assistant_records_are_retained(
    tmp_path: Path, malformation: str
) -> None:
    records = _turn("claude-code", 5)
    message = cast("JsonObject", records[1]["message"])
    if malformation == "no-message":
        del records[1]["message"]
    elif malformation == "no-id":
        del message["id"]
    else:
        del message["usage"]
    _write(tmp_path, "claude-code", "session", records)

    assert _prepare(tmp_path, "claude-code").session_count == 1


def test_filter_runs_after_inherited_fork_turns_are_removed(tmp_path: Path) -> None:
    inherited = _turn("codex", 100)
    _write(tmp_path, "codex", "parent", inherited)
    child = _write(
        tmp_path,
        "codex",
        "child",
        [*inherited, *_turn("codex", 5, at="2026-05-12T02:00:00Z", total=105)],
        parent="parent",
    )
    original = child.read_bytes()

    result = _prepare(tmp_path, "codex")

    assert set(_rows(result)) == {"parent"}
    assert child.read_bytes() == original


def test_existing_workspace_is_refiltered_only_with_force(tmp_path: Path) -> None:
    _write(tmp_path, "codex", "session", _turn("codex", 100))
    assert _prepare(tmp_path, "codex").session_count == 1
    _write(tmp_path, "codex", "session", _turn("codex", 5))

    reused = _prepare(tmp_path, "codex")
    assert not reused.created
    assert reused.session_count == 1
    assert _prepare(tmp_path, "codex", force=True).session_count == 0


def _human(source: SourceName, at: str = "2026-05-12T01:00:00Z") -> JsonObject:
    if source == "codex":
        return {
            "type": "response_item",
            "timestamp": at,
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Task"}],
            },
        }
    return {
        "type": "user",
        "timestamp": at,
        "cwd": "/output-token-project",
        "message": {"role": "user", "content": "Task"},
    }


def _assistant(
    source: SourceName, *, tokens: JsonValue = 5, identity: str = "message"
) -> JsonObject:
    if source == "codex":
        return {
            "type": "response_item",
            "timestamp": "2026-05-12T01:00:01Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "OK"}],
            },
        }
    return {
        "type": "assistant",
        "timestamp": "2026-05-12T01:00:01Z",
        "message": {
            "role": "assistant",
            "id": identity,
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"output_tokens": tokens},
            "stop_reason": "end_turn",
        },
    }


def _usage(total: JsonValue, last: JsonValue) -> JsonObject:
    return {
        "type": "event_msg",
        "timestamp": "2026-05-12T01:00:02Z",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"output_tokens": total},
                "last_token_usage": {"output_tokens": last},
            },
        },
    }


def _terminal() -> JsonObject:
    return {
        "type": "event_msg",
        "timestamp": "2026-05-12T01:00:03Z",
        "payload": {"type": "task_complete"},
    }


def _turn(
    source: SourceName,
    tokens: int,
    *,
    at: str = "2026-05-12T01:00:00Z",
    total: int | None = None,
) -> list[JsonObject]:
    records = [_human(source, at), _assistant(source, tokens=tokens, identity=at)]
    if source == "codex":
        records.extend([_usage(tokens if total is None else total, tokens), _terminal()])
    for record in records:
        record["timestamp"] = at
    return records


def _write(
    tmp_path: Path,
    source: SourceName,
    session_id: str,
    records: list[JsonObject] | list[JsonObject | str],
    *,
    parent: str | None = None,
) -> Path:
    if source == "codex":
        payload: JsonObject = {"id": session_id, "cwd": "/output-token-project"}
        if parent is not None:
            payload["forked_from_id"] = parent
        records = [{"type": "session_meta", "payload": payload}, *records]
    path = tmp_path / source / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            (record if isinstance(record, str) else json.dumps(record)) + "\n" for record in records
        ),
        encoding="utf-8",
    )
    return path


def _prepare(tmp_path: Path, source: SourceName, *, force: bool = False) -> PrepareResult:
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    return prepare_workspace(
        target,
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source=source, root=tmp_path / source),),
        force=force,
    )


def _json(path: Path) -> JsonObject:
    return cast("JsonObject", json.loads(path.read_text(encoding="utf-8")))


def _rows(result: PrepareResult) -> dict[str, JsonObject]:
    rows: dict[str, JsonObject] = {}
    for index in result.workspace_path.glob("projects/*/sessions.index.jsonl"):
        for line in index.read_text(encoding="utf-8").splitlines():
            row = cast("JsonObject", json.loads(line))
            rows[cast("str", row["source_session_id"])] = row
    return rows
