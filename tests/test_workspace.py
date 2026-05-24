from __future__ import annotations

import json
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
            }
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
    assert rows[0]["target_end_line"] == 7
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
    target_subagents = cast("list[dict[str, object]]", rows[0]["target_subagents"])
    assert target_subagents == [
        {
            "agent_role": None,
            "association": "spawned_or_returned_in_target_span",
            "parent_result_line": None,
            "parent_spawn_line": 2,
            "session_file": "fallback-child.jsonl",
            "source_session_id": "fallback-child",
        },
        {
            "agent_role": "nested-role",
            "association": "spawned_or_returned_in_target_span",
            "parent_result_line": None,
            "parent_spawn_line": 8,
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
    target_subagents = cast("list[dict[str, object]]", rows[0]["target_subagents"])
    assert target_subagents == [
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
                }
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


def _single_directory(path: Path) -> Path:
    directories = [candidate for candidate in path.iterdir() if candidate.is_dir()]
    assert len(directories) == 1
    return directories[0]
