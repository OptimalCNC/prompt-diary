from __future__ import annotations

import json
from datetime import date, timedelta
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import prompt_diary.cmds.reports as reports_cmd
from prompt_diary.cli import app
from prompt_diary.errors import PromptDiaryError

LIST_FAILED = "list failed"

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_list_reports_defaults_to_ten_newest_date_workspaces(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    for day in range(1, 12):
        _write_workspace(reports_root, f"2026-05-{day:02d}")

    result = CliRunner().invoke(app, ["list", "--reports-root", str(reports_root)])

    assert result.exit_code == 0, result.output
    assert "Local reports" in result.stdout
    assert "showing 10 of 11" in result.stdout
    assert "2026-05-11" in result.stdout
    assert "2026-05-02" in result.stdout
    assert "2026-05-01" not in result.stdout
    assert str(reports_root / "work" / "2026-05-11") in result.stdout


def test_list_verbose_defaults_to_five_and_includes_inspection(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    for day in range(1, 7):
        _write_workspace(reports_root, f"2026-05-{day:02d}")

    result = CliRunner().invoke(app, ["list", "--verbose", "--reports-root", str(reports_root)])

    assert result.exit_code == 0, result.output
    assert "showing 5 of 6" in result.stdout
    assert "2026-05-06" in result.stdout
    assert "2026-05-02" in result.stdout
    assert "2026-05-01" not in result.stdout
    assert "Status: final" in result.stdout
    assert "Evidence chains: 0/0" in result.stdout


def test_list_reports_accepts_limit_option(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    for day in range(1, 5):
        _write_workspace(reports_root, f"2026-05-{day:02d}")

    result = CliRunner().invoke(app, ["list", "--limit", "2", "--reports-root", str(reports_root)])

    assert result.exit_code == 0, result.output
    assert "showing 2 of 4" in result.stdout
    assert "2026-05-04" in result.stdout
    assert "2026-05-03" in result.stdout
    assert "2026-05-02" not in result.stdout


def test_list_reports_prints_empty_state(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"

    result = CliRunner().invoke(app, ["list", "--reports-root", str(reports_root)])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"No local reports found in {reports_root / 'work'}.\n"


def test_list_reports_error_exits_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(_explicit: Path | None) -> Path:
        raise PromptDiaryError(LIST_FAILED)

    monkeypatch.setattr(reports_cmd, "resolve_reports_root", raise_error)

    result = CliRunner().invoke(app, ["list"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {LIST_FAILED}\n"


def test_inspect_report_summarizes_progress_by_project(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    workspace = _write_workspace(reports_root, "2026-06-05", timezone="UTC")
    project_dir = workspace / "projects" / "work-data-111111111111"
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 2,
            "project_key": "work-data-111111111111",
            "project_label": "work-data",
        },
    )
    _write_jsonl(
        project_dir / "sessions.index.jsonl",
        [
            {
                "session_ref": "S0001",
                "source": "codex",
                "source_session_id": "session-1",
                "session_path": "sessions/codex/session-1.jsonl",
                "target_start_line": 1,
                "target_end_line": 20,
                "turns": [
                    {"turn_ref": "T0001", "turn_start_line": 1, "turn_end_line": 10},
                    {"turn_ref": "T0002", "turn_start_line": 11, "turn_end_line": 20},
                ],
            },
            {
                "session_ref": "S0002",
                "source": "claude-code",
                "source_session_id": "session-2",
                "session_path": "sessions/claude-code/session-2.jsonl",
                "target_start_line": 1,
                "target_end_line": 5,
                "turns": [{"turn_ref": "T0001", "turn_start_line": 1, "turn_end_line": 5}],
            },
        ],
    )
    evidence_dir = project_dir / "evidence"
    evidence_dir.mkdir()
    _write_json(
        evidence_dir / "S0001.json",
        {
            "schema_version": 1,
            "project_key": "work-data-111111111111",
            "session_ref": "S0001",
            "evidence_chains": [{"turn_ref": "T0001"}],
        },
    )
    _write_json(
        project_dir / "project-synthesis.json",
        {
            "schema_version": 1,
            "project_key": "work-data-111111111111",
            "project_label": "work-data",
            "work_items": [{"work_item_ref": "W0001"}, {"work_item_ref": "W0002"}],
        },
    )
    _write_json(workspace / "daily-report.json", {"schema_version": 1})
    (workspace / "report.md").write_text("# report\n", encoding="utf-8")
    _write_json(
        reports_root / "private" / "2026-06-05" / "audit.manifest.json",
        {
            "schema_version": 2,
            "sessions": [
                {
                    "workspace_project_key": "work-data-111111111111",
                    "canonical_project_root": "/src/work-data",
                    "project_root_is_unknown": False,
                }
            ],
        },
    )

    result = CliRunner().invoke(
        app,
        [
            "inspect",
            "--date",
            "2026-06-05",
            "--timezone",
            "UTC",
            "--reports-root",
            str(reports_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Report 2026-06-05 (final)" in result.stdout
    assert f"Work path: {workspace}" in result.stdout
    assert "Evidence chains: 1/3" in result.stdout
    assert "Project synthesis: present (2 work items)" in result.stdout
    assert "Daily model: present" in result.stdout
    assert "Markdown view: present" in result.stdout
    assert "Notion payload: missing" in result.stdout
    assert "work-data" in result.stdout
    assert "Directory: /src/work-data" in result.stdout
    assert "Sessions: 2" in result.stdout
    assert "Turns: 3" in result.stdout


def test_inspect_report_requires_existing_workspace(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "inspect",
            "--date",
            "2026-06-05",
            "--timezone",
            "UTC",
            "--reports-root",
            str(tmp_path / "reports"),
        ],
    )

    assert result.exit_code == 2
    assert "prepared workspace is missing" in result.stderr


def test_inspect_report_reports_malformed_progress_artifact(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    workspace = _write_workspace(reports_root, "2026-06-05", timezone="UTC")
    project_dir = workspace / "projects" / "work-data-111111111111"
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 2,
            "project_key": "work-data-111111111111",
            "project_label": "work-data",
        },
    )
    _write_jsonl(
        project_dir / "sessions.index.jsonl",
        [
            {
                "session_ref": "S0001",
                "source": "codex",
                "source_session_id": "session-1",
                "session_path": "sessions/codex/session-1.jsonl",
                "target_start_line": 1,
                "target_end_line": 10,
                "turns": [{"turn_ref": "T0001", "turn_start_line": 1, "turn_end_line": 10}],
            },
        ],
    )
    evidence_path = project_dir / "evidence" / "S0001.json"
    evidence_path.parent.mkdir()
    evidence_path.write_text("{", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "inspect",
            "--date",
            "2026-06-05",
            "--timezone",
            "UTC",
            "--reports-root",
            str(reports_root),
        ],
    )

    assert result.exit_code == 2
    assert str(evidence_path) in result.stderr
    assert "invalid JSON" in result.stderr


def _write_workspace(
    reports_root: Path,
    report_date: str,
    *,
    timezone: str = "Asia/Shanghai",
) -> Path:
    next_date = date.fromisoformat(report_date) + timedelta(days=1)
    suffix = "Z" if timezone == "UTC" else "+00:00"
    workspace = reports_root / "work" / report_date
    workspace.mkdir(parents=True)
    (workspace / "projects").mkdir()
    _write_json(
        workspace / "metadata.json",
        {
            "schema_version": 2,
            "report_date": report_date,
            "timezone": timezone,
            "status": "final",
            "prepared_at": f"{report_date}T12:00:00+00:00",
            "report_window_local": {
                "start": f"{report_date}T00:00:00{suffix}",
                "end": f"{next_date.isoformat()}T00:00:00{suffix}",
            },
            "report_window_utc": {
                "start": f"{report_date}T00:00:00Z",
                "end": f"{next_date.isoformat()}T00:00:00Z",
            },
        },
    )
    return workspace


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
