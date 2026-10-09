from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from tests.support.project_synthesis import (
    COMMITTED_TURNS,
    PROJECT_KEY,
    copy_basic_project_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_load_returns_committed_chains_in_index_then_card_order(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    chains = load_committed_chains(workspace, PROJECT_KEY)

    assert [(chain.session_ref, chain.turn_ref) for chain in chains] == [
        ("S0001", "T0001"),
        ("S0001", "T0002"),
        ("S0002", "T0001"),
    ]


def test_load_skips_the_gap_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    keys = committed_turn_keys(load_committed_chains(workspace, PROJECT_KEY))

    assert keys == set(COMMITTED_TURNS)
    assert ("S0001", "T0003") not in keys


def test_committed_chain_carries_trimmed_fields_and_verbatim_quotes(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    chains = load_committed_chains(workspace, PROJECT_KEY)
    first = chains[0]

    assert first.materiality == "material"
    assert "simplify" in first.trigger_summary
    assert first.reaction_summaries == (
        "Updated the MCP tools page, evidence contract, and extractor prompt.",
    )
    assert first.outcomes[0].category == "document_outcome"
    assert first.terminal_type == "material_result"
    assert first.messages == ("Please simplify the MCP evidence tools and drop chain_ref.",)


def test_load_tolerates_a_missing_card(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    (workspace / "projects" / PROJECT_KEY / "evidence" / "S0002.json").unlink()

    keys = committed_turn_keys(load_committed_chains(workspace, PROJECT_KEY))

    assert keys == {("S0001", "T0001"), ("S0001", "T0002")}


def test_load_returns_empty_for_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    assert load_committed_chains(workspace, "Missing-000000000000") == ()


@pytest.mark.parametrize("source", ["codex", "claude-code"])
def test_source_times_follow_indexed_boundaries_and_normalize_timezone(
    tmp_path: Path, source: str
) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    project_dir = workspace / "projects" / PROJECT_KEY
    _set_first_source(project_dir, source)
    _write_source(
        project_dir,
        {
            1: {"timestamp": "2000-01-01T00:00:00Z"},
            2: _user_record(source, "2026-05-28T10:00:00+08:00"),
            8: {"timestamp": "2026-05-28T02:30:00Z", "type": "terminal"},
            9: _user_record(source, "2026-05-28T02:31:00Z"),
            12: {"timestamp": "2026-05-28T02:32:00Z"},
            16: {"timestamp": "2099-01-01T00:00:00Z"},
        },
    )

    chains = load_committed_chains(workspace, PROJECT_KEY, include_source_times=True)

    assert chains[0].trigger_at == datetime(2026, 5, 28, 2, tzinfo=timezone.utc)
    assert chains[0].evidence_end_at == datetime(2026, 5, 28, 2, 30, tzinfo=timezone.utc)
    assert chains[1].trigger_at == datetime(2026, 5, 28, 2, 31, tzinfo=timezone.utc)
    assert chains[1].evidence_end_at == datetime(2026, 5, 28, 2, 32, tzinfo=timezone.utc)
    assert chains[2].trigger_at is None  # Its copied source file is absent.
    assert chains[2].evidence_end_at is None


@pytest.mark.parametrize(
    "timestamp",
    [None, 123, "bad-time", "2026-05-28T02:00:00"],
)
def test_unavailable_source_times_remain_unknown(tmp_path: Path, timestamp: object) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    project_dir = workspace / "projects" / PROJECT_KEY
    _set_first_source(project_dir, "claude-code")
    _write_source(
        project_dir,
        {2: _user_record("claude-code", timestamp), 8: {"timestamp": timestamp}},
    )

    first = load_committed_chains(workspace, PROJECT_KEY, include_source_times=True)[0]

    assert first.trigger_at is None
    assert first.evidence_end_at is None


@pytest.mark.parametrize("raw_line", ["not json", "[]"])
def test_malformed_boundary_never_borrows_another_record_time(
    tmp_path: Path, raw_line: str
) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    project_dir = workspace / "projects" / PROJECT_KEY
    source_path = _write_source(
        project_dir,
        {7: {"timestamp": "2026-05-28T02:29:00Z"}},
    )
    lines = source_path.read_text(encoding="utf-8").splitlines()
    lines[1] = raw_line
    lines[7] = raw_line
    source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    first = load_committed_chains(workspace, PROJECT_KEY, include_source_times=True)[0]

    assert first.trigger_at is None
    assert first.evidence_end_at is None


@pytest.mark.parametrize(
    ("source", "extra"),
    [
        (
            "codex",
            {
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "reply"}],
                }
            },
        ),
        ("codex", {"type": "unknown"}),
        ("unknown-source", {}),
        ("claude-code", {"type": "assistant"}),
        ("claude-code", {"message": {"role": "assistant", "content": "reply"}}),
        ("claude-code", {"sourceToolAssistantUUID": "tool-parent"}),
        ("claude-code", {"isSidechain": True}),
        ("claude-code", {"isMeta": True}),
        ("claude-code", {"isCompactSummary": True}),
        (
            "claude-code",
            {"message": {"role": "user", "content": [{"type": "tool_result"}]}},
        ),
    ],
)
def test_only_known_human_trigger_forms_supply_trigger_time(
    tmp_path: Path, source: str, extra: dict[str, object]
) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    project_dir = workspace / "projects" / PROJECT_KEY
    _set_first_source(project_dir, source)
    record = _user_record(source, "2026-05-28T02:00:00Z") | extra
    _write_source(project_dir, {2: record})

    first = load_committed_chains(workspace, PROJECT_KEY, include_source_times=True)[0]

    assert first.trigger_at is None


def test_source_times_are_optional_and_do_not_expand_the_evidence_boundary(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    project_dir = workspace / "projects" / PROJECT_KEY
    source_path = _write_source(project_dir, {2: _user_record("codex", "2026-05-28T02:00:00Z")})

    assert load_committed_chains(workspace, PROJECT_KEY)[0].trigger_at is None
    source_path.write_text("{}\n", encoding="utf-8")
    first = load_committed_chains(workspace, PROJECT_KEY, include_source_times=True)[0]
    assert first.trigger_at is None
    assert first.evidence_end_at is None


def test_source_times_handle_a_session_without_indexed_turns(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    project_dir = workspace / "projects" / PROJECT_KEY
    _write_source(project_dir, {})
    index_path = project_dir / "sessions.index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["turns"] = []
    index_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    first = load_committed_chains(workspace, PROJECT_KEY, include_source_times=True)[0]

    assert first.trigger_at is None
    assert first.evidence_end_at is None


def _user_record(source: str, timestamp: object) -> dict[str, object]:
    if source == "codex":
        return {
            "timestamp": timestamp,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Please finish this task."}],
            },
        }
    return {
        "timestamp": timestamp,
        "type": "user",
        "message": {"role": "user", "content": "Please finish this task."},
    }


def _set_first_source(project_dir: Path, source: str) -> None:
    index_path = project_dir / "sessions.index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["source"] = source
    index_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_source(project_dir: Path, records: dict[int, object]) -> Path:
    source_path = project_dir / "sessions" / "codex" / "session-001.jsonl"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "\n".join(json.dumps(records.get(line, {})) for line in range(1, 17)) + "\n",
        encoding="utf-8",
    )
    return source_path
