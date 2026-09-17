from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

import prompt_diary.cmds.common as common_cmd
import prompt_diary.cmds.prepare as prepare_cmd
from prompt_diary.cli import app
from prompt_diary.models import JsonObject, PrepareResult, ReportTarget, SourceSpec, TimeWindow
from prompt_diary.paths import REPORTS_HOME_ENV
from prompt_diary.prepare.workspace import CLAUDE_SOURCE_ENV, CODEX_SOURCE_ENV, prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    import pytest

TARGET_DATE = "2026-05-12"
TARGET_TIMEZONE = "Asia/Shanghai"
TARGET_NOW = datetime(2026, 5, 13, 9, 2, tzinfo=ZoneInfo(TARGET_TIMEZONE))
FIXTURES_ROOT = Path(__file__).parents[1] / "fixtures"


@dataclass(frozen=True)
class PrepareFixture:
    root: Path
    codex_root: Path
    claude_root: Path

    @property
    def source_specs(self) -> tuple[SourceSpec, ...]:
        return (
            SourceSpec(source="codex", root=self.codex_root),
            SourceSpec(source="claude-code", root=self.claude_root),
        )


def _prepare_workflow(
    *,
    date: str | None,
    today: bool,
    timezone_name: str | None,
    force: bool,
    reports_root: Path,
    source_specs: tuple[SourceSpec, ...] | None = None,
    now: datetime | None = None,
) -> PrepareResult:
    target = resolve_report_target(date=date, today=today, timezone_name=timezone_name, now=now)
    return prepare_workspace(
        target,
        reports_root=reports_root,
        source_specs=source_specs,
        force=force,
        prepared_at=now,
    )


def test_prepare_workflow_uses_redacted_realistic_session_shapes(tmp_path: Path) -> None:
    fixture = _prepare_fixture("prepare-realistic")

    result = _prepare_workflow(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=tmp_path / ".reports",
        source_specs=fixture.source_specs,
        now=TARGET_NOW,
    )

    assert result.created
    assert result.project_count == 1
    assert result.session_count == 2
    _assert_realistic_workspace(result.workspace_path, fixture)


def test_prepare_workflow_assigns_row_local_turn_refs_for_multi_turn_sessions(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-multi-turns")

    result = _prepare_workflow(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=tmp_path / ".reports",
        source_specs=fixture.source_specs,
        now=TARGET_NOW,
    )

    assert result.created
    assert result.project_count == 1
    assert result.session_count == 2
    project_dir = _single_directory(result.workspace_path / "projects")
    rows_by_source = _rows_by_source(_load_jsonl(project_dir / "sessions.index.jsonl"))

    codex_turns = cast("list[JsonObject]", rows_by_source["codex"]["turns"])
    assert [
        (turn["turn_ref"], turn["turn_start_line"], turn["turn_end_line"]) for turn in codex_turns
    ] == [("T0001", 4, 6), ("T0002", 10, 12)]

    claude_turns = cast("list[JsonObject]", rows_by_source["claude-code"]["turns"])
    assert [
        (turn["turn_ref"], turn["turn_start_line"], turn["turn_end_line"]) for turn in claude_turns
    ] == [("T0001", 2, 5), ("T0002", 6, 8), ("T0003", 9, 10)]


def test_prepare_workflow_keeps_parent_results_without_copying_children(tmp_path: Path) -> None:
    fixture = _prepare_fixture("prepare-subagents")
    result = _prepare_workflow(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=tmp_path / ".reports",
        source_specs=fixture.source_specs,
        now=TARGET_NOW,
    )

    assert result.session_count == 2
    project_dir = _single_directory(result.workspace_path / "projects")
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    sources = {spec.source: spec.root for spec in fixture.source_specs}
    assert len(list((project_dir / "sessions").rglob("*.jsonl"))) == 2
    assert not list((project_dir / "sessions").rglob("subagents"))
    for row in rows:
        assert "subagent_path" not in row
        turns = cast("list[JsonObject]", row["turns"])
        assert all("target_subagents" not in turn for turn in turns)
        copied = project_dir / str(row["session_path"])
        original = next(sources[str(row["source"])].rglob(copied.name))
        # Parent-visible tool results and completion notifications retain their original lines.
        assert copied.read_bytes() == original.read_bytes()


def test_prepare_workflow_reuses_existing_workspace_counts_projects_and_sessions(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-realistic")
    reports_root = tmp_path / ".reports"
    _prepare_workflow(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=reports_root,
        source_specs=fixture.source_specs,
        now=TARGET_NOW,
    )

    reused = _prepare_workflow(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=reports_root,
        source_specs=fixture.source_specs,
        now=TARGET_NOW,
    )

    assert not reused.created
    assert reused.project_count == 1
    assert reused.session_count == 2
    assert reused.messages == (
        f"Workspace already exists at {reports_root / 'work' / TARGET_DATE}; "
        "use prepare --force to refresh it.",
    )


def test_prepare_workflow_force_recreates_invalid_existing_workspace_and_uses_now(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-realistic")
    reports_root = tmp_path / ".reports"
    workspace_path = reports_root / "work" / TARGET_DATE
    audit_dir = reports_root / "private" / TARGET_DATE
    workspace_path.mkdir(parents=True)
    (workspace_path / "metadata.json").write_text("{", encoding="utf-8")
    (workspace_path / "stale.txt").write_text("old", encoding="utf-8")
    audit_dir.mkdir(parents=True)
    (audit_dir / "stale.txt").write_text("old", encoding="utf-8")

    result = _prepare_workflow(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=True,
        reports_root=reports_root,
        source_specs=fixture.source_specs,
        now=datetime.fromisoformat("2026-05-13T09:01:02"),
    )

    assert result.created
    assert result.session_count == 2
    assert not (workspace_path / "stale.txt").exists()
    assert not (audit_dir / "stale.txt").exists()
    assert result.audit_path.exists()
    metadata = _load_json(result.workspace_path / "metadata.json")
    assert metadata["prepared_at"] == "2026-05-13T09:01:02+08:00"


def test_prepare_workflow_handles_payload_timestamp_turn_context_cwd_and_end_boundary(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-edge-cases")

    result = _prepare_workflow(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=tmp_path / ".reports",
        source_specs=(SourceSpec(source="codex", root=fixture.codex_root),),
        now=TARGET_NOW,
    )

    assert result.session_count == 1
    project_dir = _single_directory(result.workspace_path / "projects")
    project_json = _load_json(project_dir / "project.json")
    assert project_json["project_label"] == "turn-context-project"
    rows = _load_jsonl(project_dir / "sessions.index.jsonl")
    assert rows[0]["source_session_id"] == "payload-timestamp-session"
    assert rows[0]["target_start_line"] == 3
    assert rows[0]["target_end_line"] == 3
    assert rows[0]["turns"] == [
        {
            "turn_ref": "T0001",
            "turn_start_line": 3,
            "turn_end_line": 3,
        }
    ]
    assert not (project_dir / "sessions" / "codex" / "end-boundary-only.jsonl").exists()


def test_prepare_workflow_indexes_cross_day_agent_reactions_by_human_trigger(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-cross-day-reactions")

    may18 = _prepare_workflow(
        date="2026-05-18",
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=tmp_path / ".reports-may18",
        source_specs=(SourceSpec(source="codex", root=fixture.codex_root),),
        now=datetime(2026, 5, 19, 9, 2, tzinfo=ZoneInfo(TARGET_TIMEZONE)),
    )

    assert may18.session_count == 1
    may18_project = _single_directory(may18.workspace_path / "projects")
    may18_project_json = _load_json(may18_project / "project.json")
    assert may18_project_json["project_label"] == "git-outpost"
    may18_row = _load_jsonl(may18_project / "sessions.index.jsonl")[0]
    assert may18_row["source_session_id"] == "git-outpost-cross-day"
    assert may18_row["target_start_line"] == 4
    assert may18_row["target_end_line"] == 13
    assert may18_row["turns"] == [
        {
            "turn_ref": "T0001",
            "turn_start_line": 4,
            "turn_end_line": 13,
        }
    ]
    copied_session = may18_project / str(may18_row["session_path"])
    fixture_session = (
        fixture.codex_root
        / "2026"
        / "05"
        / "18"
        / "rollout-2026-05-18T23-59-29-git-outpost-cross-day.jsonl"
    )
    assert copied_session.read_text(encoding="utf-8") == fixture_session.read_text(encoding="utf-8")

    may19 = _prepare_workflow(
        date="2026-05-19",
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=tmp_path / ".reports-may19",
        source_specs=(SourceSpec(source="codex", root=fixture.codex_root),),
        now=datetime(2026, 5, 20, 9, 2, tzinfo=ZoneInfo(TARGET_TIMEZONE)),
    )

    assert may19.session_count == 1
    may19_project = _single_directory(may19.workspace_path / "projects")
    may19_row = _load_jsonl(may19_project / "sessions.index.jsonl")[0]
    assert may19_row["source_session_id"] == "git-outpost-cross-day"
    assert may19_row["target_start_line"] == 16
    assert may19_row["target_end_line"] == 18
    assert may19_row["turns"] == [
        {
            "turn_ref": "T0001",
            "turn_start_line": 16,
            "turn_end_line": 18,
        }
    ]


def test_cli_prepare_forwards_today_timezone_and_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    target = ReportTarget(
        report_date=date_type(2026, 5, 23),
        timezone="UTC",
        status="partial",
        report_window_local=TimeWindow(
            start=datetime(2026, 5, 23, tzinfo=timezone.utc),
            end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        ),
        report_window_utc=TimeWindow(
            start=datetime(2026, 5, 23, tzinfo=timezone.utc),
            end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        ),
    )

    def fake_resolve(
        self: common_cmd.CliWorkspaceTargetOptions,
        *,
        now: datetime | None = None,
    ) -> common_cmd.ResolvedCliWorkspaceTarget:
        del now
        captured.append(
            {
                "date": self.report_target.date,
                "today": self.report_target.today,
                "timezone_name": self.report_target.timezone,
            }
        )
        workspace_path = tmp_path / ".reports" / "work" / "2026-05-23"
        return common_cmd.ResolvedCliWorkspaceTarget(
            target=target,
            reports_root=tmp_path / ".reports",
            workspace_path=workspace_path,
        )

    def fake_prepare_workspace(
        target: ReportTarget, *, force: bool, **_kwargs: object
    ) -> PrepareResult:
        captured.append({"target": target, "force": force})
        return PrepareResult(
            target=target,
            workspace_path=tmp_path / ".reports" / "work" / "2026-05-23",
            audit_path=tmp_path / ".reports" / "private" / "2026-05-23" / "audit.manifest.json",
            created=True,
            project_count=0,
            session_count=0,
            messages=("prepared today",),
        )

    monkeypatch.setattr(common_cmd.CliWorkspaceTargetOptions, "resolve", fake_resolve)
    monkeypatch.setattr(prepare_cmd, "prepare_workspace", fake_prepare_workspace)
    runner = CliRunner()

    result = runner.invoke(app, ["prepare", "--today", "--timezone", "UTC", "--force"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "prepared today\n"
    assert captured == [
        {
            "date": None,
            "today": True,
            "timezone_name": "UTC",
        },
        {"target": target, "force": True},
    ]


def test_cli_prepare_force_refreshes_workspace_from_env_roots(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-realistic")
    runner = CliRunner()
    reports_root = tmp_path / ".reports"
    env = {**_source_env(fixture), REPORTS_HOME_ENV: str(reports_root)}

    first = runner.invoke(
        app,
        ["prepare", "--date", TARGET_DATE, "--timezone", TARGET_TIMEZONE],
        env=env,
    )
    assert first.exit_code == 0, first.output

    workspace = reports_root / "work" / TARGET_DATE
    stale_path = workspace / "stale.txt"
    stale_path.write_text("old", encoding="utf-8")

    refreshed = runner.invoke(
        app,
        ["prepare", "--date", TARGET_DATE, "--timezone", TARGET_TIMEZONE, "--force"],
        env=env,
    )

    assert refreshed.exit_code == 0, refreshed.output
    assert f"Prepared workspace {workspace}" in refreshed.stdout
    assert not stale_path.exists()
    _assert_realistic_workspace(workspace, fixture)


def _prepare_fixture(name: str) -> PrepareFixture:
    root = FIXTURES_ROOT / name
    return PrepareFixture(
        root=root,
        codex_root=root / "codex",
        claude_root=root / "claude",
    )


def _assert_realistic_workspace(
    workspace_path: Path,
    fixture: PrepareFixture,
) -> None:
    metadata = _load_json(workspace_path / "metadata.json")
    assert metadata["schema_version"] == 3
    assert metadata["report_date"] == TARGET_DATE
    assert metadata["timezone"] == TARGET_TIMEZONE
    assert metadata["report_window_utc"] == {
        "start": "2026-05-11T16:00:00Z",
        "end": "2026-05-12T16:00:00Z",
    }

    project_dir = _single_directory(workspace_path / "projects")
    project_json = _load_json(project_dir / "project.json")
    assert project_json["schema_version"] == 3
    assert project_json["project_label"] == "ReportGenerator"

    rows_by_source = _rows_by_source(_load_jsonl(project_dir / "sessions.index.jsonl"))
    assert set(rows_by_source) == {"claude-code", "codex"}
    assert rows_by_source["codex"]["source_session_id"] == "019e1bb6-620a-7462-9fb0-d28c3acef59d"
    assert rows_by_source["codex"]["target_start_line"] == 4
    assert rows_by_source["codex"]["target_end_line"] == 6
    assert rows_by_source["codex"]["turns"] == [
        {
            "turn_ref": "T0001",
            "turn_start_line": 4,
            "turn_end_line": 6,
        }
    ]
    assert rows_by_source["claude-code"]["source_session_id"] == (
        "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3"
    )
    assert rows_by_source["claude-code"]["target_start_line"] == 3
    assert rows_by_source["claude-code"]["target_end_line"] == 5
    assert rows_by_source["claude-code"]["turns"] == [
        {
            "turn_ref": "T0001",
            "turn_start_line": 3,
            "turn_end_line": 5,
        }
    ]

    copied_codex = project_dir / str(rows_by_source["codex"]["session_path"])
    copied_claude = project_dir / str(rows_by_source["claude-code"]["session_path"])
    codex_fixture = (
        fixture.codex_root
        / "2026"
        / "05"
        / "12"
        / "rollout-2026-05-12T00-00-00-019e1bb6-620a-7462-9fb0-d28c3acef59d.jsonl"
    )
    claude_fixture = (
        fixture.claude_root / "-tmp-ReportGenerator" / "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3.jsonl"
    )
    assert copied_codex.read_text(encoding="utf-8") == codex_fixture.read_text(encoding="utf-8")
    assert copied_claude.read_text(encoding="utf-8") == claude_fixture.read_text(encoding="utf-8")


def _source_env(fixture: PrepareFixture) -> dict[str, str]:
    return {
        CODEX_SOURCE_ENV: str(fixture.codex_root),
        CLAUDE_SOURCE_ENV: str(fixture.claude_root),
    }


def _rows_by_source(rows: list[JsonObject]) -> dict[str, JsonObject]:
    return {str(row["source"]): row for row in rows}


def _load_json(path: Path) -> JsonObject:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast("JsonObject", raw)


def _load_jsonl(path: Path) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        assert isinstance(raw, dict)
        rows.append(cast("JsonObject", raw))
    return rows


def _single_directory(path: Path) -> Path:
    directories = [candidate for candidate in path.iterdir() if candidate.is_dir()]
    assert len(directories) == 1
    return directories[0]
