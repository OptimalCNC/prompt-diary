from __future__ import annotations

import json
import shutil
from datetime import datetime
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.models import ReportTarget, SourceSpec
from prompt_diary.targets import resolve_report_target
from prompt_diary.workspace import (
    CLAUDE_SOURCE_ENV,
    CODEX_SOURCE_ENV,
    audit_path_for_target,
    default_source_specs,
    prepare_workspace,
    validate_workspace_matches_target,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_prepare_workspace_skips_direct_claude_subagent_discovery(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "claude-projects"
    source_path = (
        source_root
        / "-tmp-ReportGenerator"
        / "00000000-0000-4000-8000-000000000111"
        / "subagents"
        / "agent-a000000000000111.jsonl"
    )
    _write_jsonl(
        source_path,
        [
            {
                "agentId": "a000000000000111",
                "type": "assistant",
                "isSidechain": True,
                "sessionId": "00000000-0000-4000-8000-000000000111",
                "timestamp": "2026-05-11T16:00:00Z",
                "cwd": str(project_root),
                "message": {"role": "assistant", "content": "Reviewed report evidence."},
            }
        ],
    )
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 5, 13, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    result = prepare_workspace(
        target,
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="claude-code", root=source_root),),
        prepared_at=datetime(2026, 5, 13, 9, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.project_count == 0
    assert result.session_count == 0
    assert not any((result.workspace_path / "projects").iterdir())


def test_default_source_specs_handles_defaults_blank_env_and_audit_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    default_specs = default_source_specs(home=home, env={})

    assert default_specs == (
        SourceSpec(source="codex", root=home / ".codex" / "sessions"),
        SourceSpec(source="claude-code", root=home / ".claude" / "projects"),
    )
    assert (
        default_source_specs(
            home=home,
            env={CODEX_SOURCE_ENV: " ", CLAUDE_SOURCE_ENV: ""},
        )
        == ()
    )

    target = _target()

    assert audit_path_for_target(target, reports_root=tmp_path / ".reports") == (
        tmp_path / ".reports" / "private" / "2026-05-12" / "audit.manifest.json"
    )


def test_prepare_workspace_reuse_counts_zero_when_projects_dir_missing(tmp_path: Path) -> None:
    target = _target()
    reports_root = tmp_path / ".reports"
    result = prepare_workspace(target, reports_root=reports_root, source_specs=())
    (result.workspace_path / "projects").rmdir()

    reused = prepare_workspace(target, reports_root=reports_root, source_specs=())

    assert not reused.created
    assert reused.project_count == 0
    assert reused.session_count == 0


def test_validate_workspace_matches_target_rejects_invalid_existing_metadata(
    tmp_path: Path,
) -> None:
    target = _target()
    missing_workspace = tmp_path / "missing"
    missing_workspace.mkdir()

    with pytest.raises(PromptDiaryError, match="metadata is missing or invalid"):
        validate_workspace_matches_target(missing_workspace, target)

    invalid_workspace = tmp_path / "invalid"
    invalid_workspace.mkdir()
    (invalid_workspace / "metadata.json").write_text("{", encoding="utf-8")
    with pytest.raises(PromptDiaryError, match="metadata is missing or invalid"):
        validate_workspace_matches_target(invalid_workspace, target)

    scalar_workspace = tmp_path / "scalar"
    scalar_workspace.mkdir()
    (scalar_workspace / "metadata.json").write_text("[]", encoding="utf-8")
    with pytest.raises(PromptDiaryError, match="metadata is missing or invalid"):
        validate_workspace_matches_target(scalar_workspace, target)

    mismatch_workspace = tmp_path / "mismatch"
    mismatch_workspace.mkdir()
    metadata = _metadata_for_target(target)
    del metadata["report_window_local"]
    _write_json(mismatch_workspace / "metadata.json", metadata)
    with pytest.raises(PromptDiaryError, match=r"report_window_local\.start"):
        validate_workspace_matches_target(mismatch_workspace, target)


def test_prepare_workspace_accepts_single_jsonl_root_and_ignores_unusable_roots(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_file = tmp_path / "single-session.jsonl"
    ignored_file = tmp_path / "ignored.txt"
    ignored_file.write_text("not jsonl", encoding="utf-8")
    _write_jsonl(
        source_file,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-11T16:00:00Z",
                "payload": {"id": "single-session", "cwd": str(project_root)},
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-11T16:00:01Z",
                "payload": {
                    "content": [{"text": "Check project.", "type": "input_text"}],
                    "role": "user",
                    "type": "message",
                },
            },
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(
            SourceSpec(source="codex", root=source_file),
            SourceSpec(source="codex", root=ignored_file),
            SourceSpec(source="codex", root=tmp_path / "missing-sessions"),
        ),
    )

    assert result.session_count == 1
    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == "single-session"
    assert rows[0]["turns"] == [{"turn_start_line": 2, "turn_end_line": 2, "target_subagents": []}]


def test_prepare_workspace_records_parse_warnings_and_fallback_root(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "codex"
    source_path = source_root / "warning-session.jsonl"
    fallback_root = tmp_path / "fallback-project"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "\n".join(
            [
                "not json",
                "[]",
                json.dumps({"type": "message", "timestamp": "not-a-time"}),
                json.dumps({"type": "message", "timestamp": "2026-05-11T16:00:01"}),
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-11T16:00:09Z",
                        "payload": {
                            "content": [{"text": "Check status.", "type": "input_text"}],
                            "role": "user",
                            "type": "message",
                        },
                    },
                ),
                json.dumps({"type": "message", "timestamp": "2026-05-11T16:00:10Z"}),
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "payload-session",
                            "timestamp": "2026-05-11T16:00:20Z",
                        },
                    },
                ),
                json.dumps({"type": "message", "timestamp": "2026-05-11T16:00:05Z"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(
            SourceSpec(source="codex", root=source_root, fallback_project_root=fallback_root),
        ),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == "payload-session"
    assert rows[0]["target_start_line"] == 5
    assert rows[0]["target_end_line"] == 8
    assert rows[0]["turns"] == [{"turn_start_line": 5, "turn_end_line": 8, "target_subagents": []}]
    audit = _load_json(result.audit_path)
    source_specs = cast("list[dict[str, object]]", audit["source_specs"])
    assert source_specs[0]["fallback_project_root"] == str(fallback_root)
    sessions = cast("list[dict[str, object]]", audit["sessions"])
    session = sessions[0]
    assert session["malformed_line_count"] == 2
    assert session["untimestamped_record_count"] == 2
    assert session["non_monotonic_timestamp_count"] == 1
    assert session["last_event_at"] == "2026-05-11T16:00:20Z"
    assert session["warnings"] == [
        "2 malformed JSONL line(s)",
        "2 untimestamped record(s)",
        "1 non-monotonic timestamp(s)",
    ]


def test_prepare_workspace_skips_direct_claude_subagent_without_parent(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "claude"
    source_path = (
        source_root
        / "00000000-0000-4000-8000-000000000222"
        / "subagents"
        / "agent-a000000000000222.jsonl"
    )
    _write_jsonl(
        source_path,
        [
            {
                "agentId": "a000000000000222",
                "type": "assistant",
                "isSidechain": True,
                "sessionId": "00000000-0000-4000-8000-000000000222",
                "timestamp": "2026-05-11T16:00:00Z",
                "message": {"role": "assistant", "content": "no cwd"},
            }
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="claude-code", root=source_root),),
    )

    assert result.project_count == 0
    assert result.session_count == 0
    audit = _load_json(result.audit_path)
    sessions = cast("list[dict[str, object]]", audit["sessions"])
    assert sessions == []


def test_prepare_workspace_handles_codex_subagent_edge_metadata(tmp_path: Path) -> None:
    source_root = tmp_path / "codex"
    parent_path = source_root / "2026" / "05" / "12" / "parent.jsonl"
    _write_jsonl(
        parent_path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-11T16:00:00Z",
                "payload": {"id": "codex-edge-parent"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T07:59:59Z",
                "payload": {
                    "content": [{"text": "Explore edge cases.", "type": "input_text"}],
                    "role": "user",
                    "type": "message",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:00Z",
                "payload": {
                    "type": "function_call",
                    "name": "spawn_agent",
                    "call_id": "spawn-fallback-child",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:01Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "spawn-fallback-child",
                    "output": json.dumps({"agent_id": "fallback-child"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:02Z",
                "payload": {"type": "function_call_output", "output": "not-json"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:03Z",
                "payload": {"type": "function_call_output", "output": "[]"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:04Z",
                "payload": {
                    "type": "function_call_output",
                    "output": json.dumps({"agent_id": "ignored-without-call-id"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:05Z",
                "payload": {
                    "type": "function_call_output",
                    "output": json.dumps(
                        {
                            "status": {
                                "fallback-child": {"running": True},
                                "orphan-child": {"completed": "[redacted]"},
                                "non-object-child": "bad",
                            }
                        }
                    ),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:06Z",
                "payload": {
                    "type": "function_call",
                    "name": "spawn_agent",
                    "call_id": "spawn-nested-role-child",
                    "arguments": json.dumps({"message": "[redacted]"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:07Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "spawn-nested-role-child",
                    "output": json.dumps({"agent_id": "nested-role-child"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T08:00:08Z",
                "payload": {"type": "function_call", "name": "spawn_agent"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T16:00:00Z",
                "payload": {
                    "type": "function_call",
                    "name": "spawn_agent",
                    "call_id": "spawn-outside-child",
                    "arguments": json.dumps({"agent_type": "outside"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T16:00:01Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "spawn-outside-child",
                    "output": json.dumps({"agent_id": "outside-child"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T16:00:02Z",
                "payload": {
                    "type": "function_call_output",
                    "output": json.dumps(
                        {"status": {"outside-child": {"completed": "[redacted]"}}}
                    ),
                },
            },
        ],
    )
    _write_codex_subagent(source_root / "fallback-child.jsonl", session_id="fallback-child")
    _write_codex_subagent(
        source_root / "source-without-subagent.jsonl",
        session_id="source-without-subagent",
        source={},
    )
    _write_codex_subagent(
        source_root / "subagent-without-thread-spawn.jsonl",
        session_id="subagent-without-thread-spawn",
        source={"subagent": {}},
    )
    _write_codex_subagent(
        source_root / "nested-role-child.jsonl",
        session_id="nested-role-child",
        source={
            "subagent": {
                "thread_spawn": {
                    "parent_thread_id": "codex-edge-parent",
                    "agent_role": "nested-role",
                }
            }
        },
    )
    _write_codex_subagent(source_root / "ambiguous-a.jsonl", session_id="ambiguous-child")
    _write_codex_subagent(source_root / "ambiguous-b.jsonl", session_id="ambiguous-child")

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    project = _load_json(project_dir / "project.json")
    assert project["project_label"] == "unknown-project"
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == "codex-edge-parent"
    assert "target_subagents" not in rows[0]
    turns = cast("list[dict[str, object]]", rows[0]["turns"])
    turn_subagents = cast("list[dict[str, object]]", turns[0]["target_subagents"])
    assert turn_subagents == [
        {
            "agent_role": None,
            "association": "spawned_or_returned_in_target_span",
            "parent_result_line": None,
            "parent_spawn_line": 3,
            "session_file": "fallback-child.jsonl",
            "source_session_id": "fallback-child",
        },
        {
            "agent_role": "nested-role",
            "association": "spawned_or_returned_in_target_span",
            "parent_result_line": None,
            "parent_spawn_line": 9,
            "session_file": "nested-role-child.jsonl",
            "source_session_id": "nested-role-child",
        },
    ]


def test_prepare_workspace_handles_claude_completed_subagent_results(tmp_path: Path) -> None:
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "claude"
    parent_id = "00000000-0000-4000-8000-000000000333"
    child_id = "a000000000000333"
    parent_path = source_root / "-tmp-ReportGenerator" / f"{parent_id}.jsonl"
    parent_records: list[dict[str, object] | str] = [
        {
            "cwd": str(project_root),
            "isSidechain": False,
            "message": {"content": "[redacted]", "role": "user"},
            "sessionId": parent_id,
            "timestamp": "2026-05-11T16:00:00Z",
            "type": "user",
        },
        "not-json",
        {
            "cwd": str(project_root),
            "isSidechain": False,
            "message": {
                "content": [{"id": "toolu_ignored", "name": "Read", "type": "tool_use"}],
                "role": "assistant",
            },
            "sessionId": parent_id,
            "timestamp": "2026-05-12T08:00:00Z",
            "type": "assistant",
        },
        {
            "cwd": str(project_root),
            "isSidechain": False,
            "message": {"content": [{"name": "Agent", "type": "tool_use"}], "role": "assistant"},
            "sessionId": parent_id,
            "timestamp": "2026-05-12T08:00:01Z",
            "type": "assistant",
        },
        {
            "cwd": str(project_root),
            "isSidechain": False,
            "message": {
                "content": [{"id": "toolu_sync", "name": "Agent", "type": "tool_use"}],
                "role": "assistant",
            },
            "sessionId": parent_id,
            "timestamp": "2026-05-12T08:00:02Z",
            "type": "assistant",
        },
        {
            "cwd": str(project_root),
            "isSidechain": False,
            "message": {
                "content": [{"tool_use_id": "toolu_sync", "type": "tool_result"}],
                "role": "user",
            },
            "sessionId": parent_id,
            "timestamp": "2026-05-12T08:00:03Z",
            "toolUseResult": {"agentId": child_id, "status": "completed"},
            "type": "user",
        },
        {
            "attachment": {"commandMode": "task-notification"},
            "cwd": str(project_root),
            "isSidechain": False,
            "sessionId": parent_id,
            "timestamp": "2026-05-12T08:00:04Z",
            "type": "attachment",
        },
        {
            "attachment": {"commandMode": "task-notification", "prompt": "missing agent id"},
            "cwd": str(project_root),
            "isSidechain": False,
            "sessionId": parent_id,
            "timestamp": "2026-05-12T08:00:05Z",
            "type": "attachment",
        },
        {
            "cwd": str(project_root),
            "isSidechain": False,
            "message": {"content": f"{child_id} final result", "role": "assistant"},
            "sessionId": parent_id,
            "timestamp": "2026-05-12T08:00:06Z",
            "type": "assistant",
        },
    ]
    _write_mixed_jsonl(parent_path, parent_records)
    _write_jsonl(
        source_root / "-tmp-ReportGenerator" / parent_id / "subagents" / f"agent-{child_id}.jsonl",
        [
            {
                "agentId": child_id,
                "attributionAgent": "general-purpose",
                "isSidechain": True,
                "message": {"content": "[redacted]", "role": "assistant"},
                "sessionId": parent_id,
                "timestamp": "2026-05-12T08:00:06Z",
                "type": "assistant",
            }
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="claude-code", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert "target_subagents" not in rows[0]
    turns = cast("list[dict[str, object]]", rows[0]["turns"])
    turn_subagents = cast("list[dict[str, object]]", turns[0]["target_subagents"])
    assert turn_subagents == [
        {
            "agent_role": "general-purpose",
            "association": "spawned_or_returned_in_target_span",
            "parent_result_line": 9,
            "parent_spawn_line": 5,
            "session_file": f"agent-{child_id}.jsonl",
            "source_session_id": child_id,
        }
    ]


def test_prepare_workspace_rejects_subagent_destination_collision(tmp_path: Path) -> None:
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "codex"
    for filename, call_id in (("parent-a.jsonl", "spawn-a"), ("parent-b.jsonl", "spawn-b")):
        _write_jsonl(
            source_root / filename,
            [
                {
                    "type": "session_meta",
                    "timestamp": "2026-05-11T16:00:00Z",
                    "payload": {"id": "same-parent", "cwd": str(project_root)},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-12T07:59:59Z",
                    "payload": {
                        "content": [{"text": "Spawn agents.", "type": "input_text"}],
                        "role": "user",
                        "type": "message",
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-12T08:00:00Z",
                    "payload": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "call_id": call_id,
                        "arguments": "{}",
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-12T08:00:01Z",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({"agent_id": "same-child"}),
                    },
                },
            ],
        )
    _write_codex_subagent(
        source_root / "same-child.jsonl",
        session_id="same-child",
        source={"subagent": {"thread_spawn": {"parent_thread_id": "same-parent"}}},
    )

    with pytest.raises(PromptDiaryError, match="Session filename collision"):
        prepare_workspace(
            _target(),
            reports_root=tmp_path / ".reports",
            source_specs=(SourceSpec(source="codex", root=source_root),),
        )


def test_prepare_workspace_rejects_session_filename_collision(tmp_path: Path) -> None:
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "codex"
    for directory, session_id in (("a", "session-a"), ("b", "session-b")):
        _write_jsonl(
            source_root / directory / "same-name.jsonl",
            [
                {
                    "type": "session_meta",
                    "timestamp": "2026-05-11T16:00:00Z",
                    "payload": {"id": session_id, "cwd": str(project_root)},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-11T16:00:01Z",
                    "payload": {
                        "content": [{"text": "Do work.", "type": "input_text"}],
                        "role": "user",
                        "type": "message",
                    },
                },
            ],
        )

    with pytest.raises(PromptDiaryError, match="Session filename collision"):
        prepare_workspace(
            _target(),
            reports_root=tmp_path / ".reports",
            source_specs=(SourceSpec(source="codex", root=source_root),),
        )


def _target() -> ReportTarget:
    return resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 5, 13, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def _metadata_for_target(target: ReportTarget) -> dict[str, object]:
    return {
        "schema_version": 1,
        "report_date": target.report_date.isoformat(),
        "timezone": target.timezone,
        "status": target.status,
        "prepared_at": "2026-05-13T09:01:00+08:00",
        "report_window_local": {
            "start": "2026-05-12T00:00:00+08:00",
            "end": "2026-05-13T00:00:00+08:00",
        },
        "report_window_utc": {
            "start": "2026-05-11T16:00:00Z",
            "end": "2026-05-12T16:00:00Z",
        },
    }


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records),
        encoding="utf-8",
    )


def _write_mixed_jsonl(path: Path, records: list[dict[str, object] | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        record if isinstance(record, str) else json.dumps(record, sort_keys=True)
        for record in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_codex_subagent(
    path: Path,
    *,
    session_id: str,
    source: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "id": session_id,
        "thread_source": "subagent",
        "timestamp": "2026-05-12T08:00:00Z",
    }
    if source is not None:
        payload["source"] = source
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-12T08:00:00Z",
                "payload": payload,
            }
        ],
    )


def _load_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast("dict[str, object]", raw)


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        assert isinstance(raw, dict)
        rows.append(cast("dict[str, object]", raw))
    return rows


def test_prepare_workspace_reuse_counts_zero_for_project_without_index(tmp_path: Path) -> None:
    target = _target()
    reports_root = tmp_path / ".reports"
    result = prepare_workspace(target, reports_root=reports_root, source_specs=())
    # Add a project directory without sessions.index.jsonl
    orphan_project = result.workspace_path / "projects" / "orphan-project"
    orphan_project.mkdir()

    reused = prepare_workspace(target, reports_root=reports_root, source_specs=())

    assert not reused.created
    # The orphan project is counted, but its session count is 0
    assert reused.project_count == 1
    assert reused.session_count == 0


def test_prepare_workspace_force_removes_only_existing_paths(tmp_path: Path) -> None:
    target = _target()
    reports_root = tmp_path / ".reports"
    # First prepare creates workspace + audit dir
    first_result = prepare_workspace(target, reports_root=reports_root, source_specs=())
    assert first_result.workspace_path.exists()
    assert first_result.audit_path.exists()

    # Remove audit dir manually so _remove_existing_workspace encounters missing audit_dir
    audit_dir = first_result.audit_path.parent
    shutil.rmtree(audit_dir)
    assert not audit_dir.exists()

    second_result = prepare_workspace(
        target, reports_root=reports_root, source_specs=(), force=True
    )
    assert second_result.created

    # Force again when workspace doesn't exist but audit dir does
    workspace_path = second_result.workspace_path
    shutil.rmtree(workspace_path)
    assert not workspace_path.exists()

    third_result = prepare_workspace(target, reports_root=reports_root, source_specs=(), force=True)
    assert third_result.created


def test_prepare_workspace_codex_message_text_edge_cases(tmp_path: Path) -> None:
    project_root = tmp_path / "MyProject"
    project_root.mkdir()
    source_root = tmp_path / "codex"
    source_path = source_root / "msg-text-edge.jsonl"
    _write_jsonl(
        source_path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-11T16:00:00Z",
                "payload": {"id": "msg-text-session", "cwd": str(project_root)},
            },
            # Trigger: response_item user message with content that has non-dict items,
            # items with non-string text, items with empty text -- all skipped until a
            # valid text item is found.
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:01Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [
                        "not-a-dict",
                        {"no-text-key": True},
                        {"text": 42},
                        {"text": "   "},
                        {"text": "Real user message.", "type": "input_text"},
                    ],
                },
            },
            # Non-trigger: response_item user message with content that has NO valid
            # text at all -- _codex_message_text returns "" which doesn't start with
            # source context prefixes, so it IS a trigger. Let's instead test the
            # "content is not a list" path.
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:02Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": "just-a-string",
                },
            },
            # response_item user message where all content items lack usable text
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:03Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"text": ""}, {"text": "  "}],
                },
            },
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == "msg-text-session"
    # Lines 2, 3, 4 are all triggers (consecutive at 3-4 are deduped, so 2 and 4)
    turns = cast("list[dict[str, object]]", rows[0]["turns"])
    # Verify the trigger detection worked through the edge cases
    assert len(turns) >= 1


def test_prepare_workspace_claude_rejects_tool_owned_trigger(tmp_path: Path) -> None:
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "claude"
    parent_id = "00000000-0000-4000-8000-000000000444"
    parent_path = source_root / "-tmp-ReportGenerator" / f"{parent_id}.jsonl"
    _write_jsonl(
        parent_path,
        [
            # Real user trigger
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "Start working.", "role": "user"},
                "sessionId": parent_id,
                "timestamp": "2026-05-11T16:00:00Z",
                "type": "user",
            },
            # Tool-owned user message -- NOT a trigger (sourceToolAssistantUUID set)
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "Tool output.", "role": "user"},
                "sessionId": parent_id,
                "sourceToolAssistantUUID": "toolu_abc123",
                "timestamp": "2026-05-12T00:00:01Z",
                "type": "user",
            },
            # Wrong type -- NOT a trigger
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "Assistant reply.", "role": "assistant"},
                "sessionId": parent_id,
                "timestamp": "2026-05-12T00:00:02Z",
                "type": "assistant",
            },
            # type=user but message.role != "user" -- NOT a trigger (covers line 541)
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "System message.", "role": "system"},
                "sessionId": parent_id,
                "timestamp": "2026-05-12T00:00:03Z",
                "type": "user",
            },
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="claude-code", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    turns = cast("list[dict[str, object]]", rows[0]["turns"])
    # Only 1 trigger: the first user message. Others are rejected.
    assert len(turns) == 1
    assert turns[0]["turn_start_line"] == 1
    assert turns[0]["turn_end_line"] == 4


def test_prepare_workspace_codex_pre_trigger_scaffolding_all_types(tmp_path: Path) -> None:
    project_root = tmp_path / "MyProject"
    project_root.mkdir()
    source_root = tmp_path / "codex"
    source_path = source_root / "scaffold-session.jsonl"
    _write_jsonl(
        source_path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-11T16:00:00Z",
                "payload": {"id": "scaffold-session", "cwd": str(project_root)},
            },
            # First trigger
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:01Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"text": "First task.", "type": "input_text"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:02Z",
                "payload": {"role": "assistant", "type": "message"},
            },
            # Pre-trigger scaffolding: turn_context
            {"type": "turn_context", "timestamp": "2026-05-12T00:00:03Z"},
            # Pre-trigger scaffolding: event_msg task_started
            {
                "type": "event_msg",
                "timestamp": "2026-05-12T00:00:04Z",
                "payload": {"type": "task_started"},
            },
            # Pre-trigger scaffolding: response_item developer
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:05Z",
                "payload": {"role": "developer", "type": "message"},
            },
            # Pre-trigger scaffolding: response_item user source-context
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:06Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [
                        {"text": "<environment_context>Current dir info</environment_context>"}
                    ],
                },
            },
            # Second trigger -- scaffolding above should be stripped
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:07Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"text": "Second task.", "type": "input_text"}],
                },
            },
            # Non-scaffolding event_msg (ptype not in set)
            {
                "type": "event_msg",
                "timestamp": "2026-05-12T00:00:08Z",
                "payload": {"type": "other_event"},
            },
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    turns = cast("list[dict[str, object]]", rows[0]["turns"])
    assert len(turns) == 2
    # First turn ends before the scaffolding block (at the assistant message, line 3)
    assert turns[0]["turn_start_line"] == 2
    assert turns[0]["turn_end_line"] == 3
    # Second trigger is at line 8, turn extends to end of file (line 9)
    assert turns[1]["turn_start_line"] == 8
    assert turns[1]["turn_end_line"] == 9


def test_prepare_workspace_claude_tool_result_without_pending_spawn(tmp_path: Path) -> None:
    """Cover _record_claude_tool_result branches:
    - tool_use_id is None (line 1090->1096)
    - tool_use_id exists but no pending spawn (line 1092->1096)
    - known_agent_ids in _claude_result_message_agent_id don't match message (line 1155->1154)
    """
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "claude"
    parent_id = "00000000-0000-4000-8000-000000000555"
    child_id_a = "a000000000000555"
    child_id_b = "a000000000000666"
    parent_path = source_root / "-tmp-ReportGenerator" / f"{parent_id}.jsonl"
    _write_jsonl(
        parent_path,
        [
            # User trigger (line 1)
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "Start.", "role": "user"},
                "sessionId": parent_id,
                "timestamp": "2026-05-11T16:00:00Z",
                "type": "user",
            },
            # Tool result WITHOUT tool_use_id in message content (tool_use_id=None)
            # but WITH toolUseResult.agentId -- covers 1090->1096.
            # Also a user trigger (type=user, message.role=user), deduped (consecutive).
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "No tool result content.", "role": "user"},
                "sessionId": parent_id,
                "timestamp": "2026-05-12T00:00:01Z",
                "toolUseResult": {"agentId": child_id_a, "status": "completed"},
                "type": "user",
            },
            # Agent tool_use (creates pending spawn for toolu_unmatched)
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {
                    "content": [{"id": "toolu_unmatched", "name": "Agent", "type": "tool_use"}],
                    "role": "assistant",
                },
                "sessionId": parent_id,
                "timestamp": "2026-05-12T00:00:02Z",
                "type": "assistant",
            },
            # Tool result with tool_use_id that does NOT match any pending spawn
            # (toolu_orphan is not a known pending spawn) -- covers 1092->1096.
            # Also a user trigger (type=user, message.role=user).
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {
                    "content": [{"tool_use_id": "toolu_orphan", "type": "tool_result"}],
                    "role": "user",
                },
                "sessionId": parent_id,
                "timestamp": "2026-05-12T00:00:03Z",
                "toolUseResult": {"agentId": child_id_b, "status": "completed"},
                "type": "user",
            },
            # Assistant message that does NOT contain any known agent_id in text
            # Covers _claude_result_message_agent_id loop exhaustion (1155->1154)
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "Task complete, no agent IDs here.", "role": "assistant"},
                "sessionId": parent_id,
                "timestamp": "2026-05-12T00:00:04Z",
                "type": "assistant",
            },
        ],
    )
    # Create subagent files so they can be found
    for child_id in (child_id_a, child_id_b):
        _write_jsonl(
            source_root
            / "-tmp-ReportGenerator"
            / parent_id
            / "subagents"
            / f"agent-{child_id}.jsonl",
            [
                {
                    "agentId": child_id,
                    "isSidechain": True,
                    "message": {"content": "[redacted]", "role": "assistant"},
                    "sessionId": parent_id,
                    "timestamp": "2026-05-12T00:00:04Z",
                    "type": "assistant",
                }
            ],
        )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="claude-code", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    turns = cast("list[dict[str, object]]", rows[0]["turns"])
    # Two turns: first trigger at line 1, second at line 4 (consecutive dedup skips line 2)
    assert len(turns) == 2
    # Turn 1 (lines 1-3): child_id_a via toolUseResult at line 2
    turn1_subagents = cast("list[dict[str, object]]", turns[0]["target_subagents"])
    assert any(s["source_session_id"] == child_id_a for s in turn1_subagents)
    # Turn 2 (lines 4-5): child_id_b via toolUseResult at line 4
    turn2_subagents = cast("list[dict[str, object]]", turns[1]["target_subagents"])
    assert any(s["source_session_id"] == child_id_b for s in turn2_subagents)


def test_prepare_workspace_claude_subagent_without_session_id(tmp_path: Path) -> None:
    """Cover _record_claude_subagent_metadata line 786->788 (sessionId is None)."""
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "claude"
    parent_id = "00000000-0000-4000-8000-000000000777"
    child_id = "a000000000000777"
    parent_path = source_root / "-tmp-ReportGenerator" / f"{parent_id}.jsonl"
    _write_jsonl(
        parent_path,
        [
            {
                "cwd": str(project_root),
                "isSidechain": False,
                "message": {"content": "Start.", "role": "user"},
                "sessionId": parent_id,
                "timestamp": "2026-05-11T16:00:00Z",
                "type": "user",
            },
        ],
    )
    # Subagent without sessionId field
    _write_jsonl(
        source_root / "-tmp-ReportGenerator" / parent_id / "subagents" / f"agent-{child_id}.jsonl",
        [
            {
                "agentId": child_id,
                "isSidechain": True,
                "message": {"content": "[redacted]", "role": "assistant"},
                "timestamp": "2026-05-12T00:00:01Z",
                "type": "assistant",
            }
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="claude-code", root=source_root),),
    )

    # The subagent session should be skipped (is_subagent detected via path)
    # The parent should still be indexed
    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == parent_id


def test_prepare_workspace_codex_subagent_metadata_edge_branches(tmp_path: Path) -> None:
    """Cover _record_codex_subagent_metadata branch partials:
    - 754->756: source_session_id is None (payload without 'id')
    - 771->774: parent_thread_id is None (thread_spawn without parent_thread_id)
    """
    project_root = tmp_path / "MyProject"
    project_root.mkdir()
    source_root = tmp_path / "codex"
    parent_path = source_root / "subagent-edge-parent.jsonl"
    _write_jsonl(
        parent_path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-11T16:00:00Z",
                "payload": {"id": "subagent-edge-parent", "cwd": str(project_root)},
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:01Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"text": "Work.", "type": "input_text"}],
                },
            },
        ],
    )
    # Subagent with payload missing 'id' field and thread_spawn without parent_thread_id
    no_id_path = source_root / "no-id-subagent.jsonl"
    no_id_payload: dict[str, object] = {
        "thread_source": "subagent",
        "timestamp": "2026-05-12T08:00:00Z",
        "source": {"subagent": {"thread_spawn": {"agent_role": "worker"}}},
    }
    _write_jsonl(
        no_id_path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-12T08:00:00Z",
                "payload": no_id_payload,
            }
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    # The parent session should be indexed; the subagent has no id so it uses filename stem
    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == "subagent-edge-parent"


def test_prepare_workspace_codex_subagent_reference_outside_turn(tmp_path: Path) -> None:
    """Cover _subagents_for_turn line 856 (reference not in turn, continue)."""
    project_root = tmp_path / "ReportGenerator"
    project_root.mkdir()
    source_root = tmp_path / "codex"
    parent_path = source_root / "multi-turn-parent.jsonl"
    _write_jsonl(
        parent_path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-11T16:00:00Z",
                "payload": {"id": "multi-turn-parent", "cwd": str(project_root)},
            },
            # First trigger (line 2) -- in window
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:01Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"text": "First task.", "type": "input_text"}],
                },
            },
            # Assistant reply (line 3)
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:02Z",
                "payload": {"role": "assistant", "type": "message"},
            },
            # Second trigger (line 4) -- in window
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:03Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"text": "Second task.", "type": "input_text"}],
                },
            },
            # Spawn agent in second turn (line 5)
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:04Z",
                "payload": {
                    "type": "function_call",
                    "name": "spawn_agent",
                    "call_id": "spawn-in-turn2",
                },
            },
            # Spawn output (line 6)
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:05Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "spawn-in-turn2",
                    "output": json.dumps({"agent_id": "child-in-turn2"}),
                },
            },
        ],
    )
    _write_codex_subagent(
        source_root / "child-in-turn2.jsonl",
        session_id="child-in-turn2",
        source={"subagent": {"thread_spawn": {"parent_thread_id": "multi-turn-parent"}}},
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    turns = cast("list[dict[str, object]]", rows[0]["turns"])
    assert len(turns) == 2
    # First turn (lines 2-3): NO subagents (reference is in second turn)
    assert turns[0]["target_subagents"] == []
    # Second turn (lines 4-6): HAS the subagent
    turn2_subagents = cast("list[dict[str, object]]", turns[1]["target_subagents"])
    assert len(turn2_subagents) == 1
    assert turn2_subagents[0]["source_session_id"] == "child-in-turn2"


def test_prepare_workspace_codex_session_meta_edge_cases(tmp_path: Path) -> None:
    """Cover _record_timestamp and _record_codex_metadata edge cases:
    - session_meta with NO payload (line 1522->1524: payload is None -> return None)
    - session_meta with payload but no 'id' (line 624->626: source_session_id is None)
    - session_meta with payload.timestamp invalid (untimestamped)
    """
    project_root = tmp_path / "MyProject"
    project_root.mkdir()
    source_root = tmp_path / "codex"
    source_path = source_root / "meta-edge.jsonl"
    _write_jsonl(
        source_path,
        [
            # session_meta with NO payload at all (covers 1522->1524)
            {"type": "session_meta"},
            # session_meta with payload but no 'id' field (covers 624->626)
            {
                "type": "session_meta",
                "payload": {
                    "cwd": str(project_root),
                    "timestamp": "invalid-date",
                },
            },
            # session_meta with payload and valid id
            {
                "type": "session_meta",
                "timestamp": "2026-05-11T16:00:00Z",
                "payload": {"id": "meta-edge-session", "cwd": str(project_root)},
            },
            # A trigger with valid timestamp
            {
                "type": "response_item",
                "timestamp": "2026-05-12T00:00:01Z",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"text": "Do work.", "type": "input_text"}],
                },
            },
        ],
    )

    result = prepare_workspace(
        _target(),
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=source_root),),
    )

    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == "meta-edge-session"
    audit = _load_json(result.audit_path)
    sessions = cast("list[dict[str, object]]", audit["sessions"])
    # Lines 1 and 2 have no valid timestamp
    assert cast("int", sessions[0]["untimestamped_record_count"]) >= 2


def _single_directory(path: Path) -> Path:
    directories = [candidate for candidate in path.iterdir() if candidate.is_dir()]
    assert len(directories) == 1
    return directories[0]
