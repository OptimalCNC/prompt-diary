from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

import prompt_diary.cli as cli_module
from prompt_diary.api import prepare_prompt_diary
from prompt_diary.cli import app
from prompt_diary.models import JsonObject, PrepareResult, ReportTarget, SourceSpec, TimeWindow
from prompt_diary.workspace import CLAUDE_SOURCE_ENV, CODEX_SOURCE_ENV

if TYPE_CHECKING:
    import pytest


TARGET_DATE = "2026-05-12"
TARGET_TIMEZONE = "Asia/Shanghai"
TARGET_NOW = datetime(2026, 5, 13, 9, 2, tzinfo=ZoneInfo(TARGET_TIMEZONE))
FIXTURES_ROOT = Path(__file__).parent / "fixtures"


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


def test_prepare_api_uses_redacted_realistic_session_shapes(tmp_path: Path) -> None:
    fixture = _prepare_fixture("prepare-realistic")

    result = prepare_prompt_diary(
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


def test_prepare_api_reuses_existing_workspace_counts_projects_and_sessions(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-realistic")
    reports_root = tmp_path / ".reports"
    prepare_prompt_diary(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        force=False,
        reports_root=reports_root,
        source_specs=fixture.source_specs,
        now=TARGET_NOW,
    )

    reused = prepare_prompt_diary(
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


def test_prepare_api_force_recreates_invalid_existing_workspace_and_uses_now(
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

    result = prepare_prompt_diary(
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


def test_prepare_api_handles_payload_timestamp_turn_context_cwd_and_end_boundary(
    tmp_path: Path,
) -> None:
    fixture = _prepare_fixture("prepare-edge-cases")

    result = prepare_prompt_diary(
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
    assert rows[0]["target_start_line"] == 1
    assert rows[0]["target_end_line"] == 2
    assert not (project_dir / "sessions" / "codex" / "end-boundary-only.jsonl").exists()


def test_cli_prepare_forwards_today_timezone_and_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_prepare_prompt_diary(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
        force: bool,
    ) -> PrepareResult:
        captured.append(
            {
                "date": date,
                "today": today,
                "timezone_name": timezone_name,
                "force": force,
            }
        )
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
        return PrepareResult(
            target=target,
            workspace_path=tmp_path / ".reports" / "work" / "2026-05-23",
            audit_path=tmp_path / ".reports" / "private" / "2026-05-23" / "audit.manifest.json",
            created=True,
            project_count=0,
            session_count=0,
            messages=("prepared today",),
        )

    monkeypatch.setattr(cli_module, "prepare_prompt_diary", fake_prepare_prompt_diary)
    runner = CliRunner()

    result = runner.invoke(app, ["prepare", "--today", "--timezone", "UTC", "--force"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "prepared today\n"
    assert captured == [
        {
            "date": None,
            "today": True,
            "timezone_name": "UTC",
            "force": True,
        }
    ]


def test_cli_prepare_force_refreshes_workspace_from_env_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _prepare_fixture("prepare-realistic")
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    first = runner.invoke(
        app,
        ["prepare", "--date", TARGET_DATE, "--timezone", TARGET_TIMEZONE],
        env=_source_env(fixture),
    )
    assert first.exit_code == 0, first.output

    workspace = tmp_path / ".reports" / "work" / TARGET_DATE
    stale_path = workspace / "stale.txt"
    stale_path.write_text("old", encoding="utf-8")

    refreshed = runner.invoke(
        app,
        ["prepare", "--date", TARGET_DATE, "--timezone", TARGET_TIMEZONE, "--force"],
        env=_source_env(fixture),
    )

    assert refreshed.exit_code == 0, refreshed.output
    assert "Prepared workspace .reports/work/2026-05-12" in refreshed.stdout
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
    assert metadata["report_date"] == TARGET_DATE
    assert metadata["timezone"] == TARGET_TIMEZONE
    assert metadata["report_window_utc"] == {
        "start": "2026-05-11T16:00:00Z",
        "end": "2026-05-12T16:00:00Z",
    }

    project_dir = _single_directory(workspace_path / "projects")
    project_json = _load_json(project_dir / "project.json")
    assert project_json["project_label"] == "ReportGenerator"

    rows_by_source = _rows_by_source(_load_jsonl(project_dir / "sessions.index.jsonl"))
    assert set(rows_by_source) == {"claude-code", "codex"}
    assert rows_by_source["codex"]["source_session_id"] == "019e1bb6-620a-7462-9fb0-d28c3acef59d"
    assert rows_by_source["codex"]["target_start_line"] == 2
    assert rows_by_source["codex"]["target_end_line"] == 4
    assert rows_by_source["claude-code"]["source_session_id"] == (
        "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3"
    )
    assert rows_by_source["claude-code"]["target_start_line"] == 3
    assert rows_by_source["claude-code"]["target_end_line"] == 4

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
