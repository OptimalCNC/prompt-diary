from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from typing import TYPE_CHECKING, TypedDict, cast

import pytest
from typer.testing import CliRunner

import prompt_diary.cmds.generate as generate_cmd
from prompt_diary.cli import app
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from pathlib import Path

REPORT_DATE = "2026-05-12"
PROJECT_KEY = "ReportGenerator-e6ff7eeda632"
COLLECT_PREPARE_FAILED = "collect must not prepare workspaces"
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class _ManifestTarget(TypedDict):
    mode: str
    report_date: str
    workspace_path: str
    workspace_archive_root: str


class _CollectionManifest(TypedDict):
    target: _ManifestTarget
    include_raw_sessions: bool
    included_paths: list[str]
    excluded_paths: list[str]
    file_checksums_sha256: dict[str, str]


def test_collect_help_lists_command_and_shared_target_flags() -> None:
    runner = CliRunner()

    top_level = runner.invoke(app, ["--help"])
    collect_help = runner.invoke(app, ["collect", "--help"], terminal_width=180)

    help_text = _one_line(collect_help.stdout)
    assert top_level.exit_code == 0
    assert "collect" in top_level.stdout
    assert collect_help.exit_code == 0
    assert "--date" in help_text
    assert "--today" in help_text
    assert "--timezone" in help_text
    assert "--reports-root" in help_text
    assert "--workspace" in help_text
    assert "--output" in help_text
    assert "--include-raw-sessions" in help_text


def test_collect_date_root_writes_default_bundle_without_raw_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports_root, workspace = _write_prepared_workspace(tmp_path)

    def prepare_must_not_run(**_kwargs: object) -> object:
        raise AssertionError(COLLECT_PREPARE_FAILED)

    monkeypatch.setattr(generate_cmd, "prepare_workspace", prepare_must_not_run)

    result = CliRunner().invoke(
        app,
        [
            "collect",
            "--date",
            REPORT_DATE,
            "--timezone",
            "UTC",
            "--reports-root",
            str(reports_root),
        ],
    )

    archive_path = reports_root / "collections" / f"{REPORT_DATE}.zip"
    names = _archive_names(archive_path)
    manifest = _read_collection_manifest(archive_path)
    metadata_archive_path = f"work/{REPORT_DATE}/metadata.json"
    raw_session_archive_path = (
        f"work/{REPORT_DATE}/projects/{PROJECT_KEY}/sessions/codex/session-001.jsonl"
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == f"Wrote collection bundle {archive_path}\n"
    assert archive_path.exists()
    assert names >= {
        "collection.manifest.json",
        metadata_archive_path,
        f"work/{REPORT_DATE}/AGENTS.md",
        f"work/{REPORT_DATE}/daily-report.json",
        f"work/{REPORT_DATE}/report.md",
        f"work/{REPORT_DATE}/report.notion.json",
        f"work/{REPORT_DATE}/projects/{PROJECT_KEY}/project.json",
        f"work/{REPORT_DATE}/projects/{PROJECT_KEY}/sessions.index.jsonl",
        f"work/{REPORT_DATE}/projects/{PROJECT_KEY}/evidence/S0001.json",
        f"work/{REPORT_DATE}/projects/{PROJECT_KEY}/project-synthesis.json",
        f"private/{REPORT_DATE}/audit.manifest.json",
    }
    assert raw_session_archive_path not in names
    assert manifest["target"]["mode"] == "date-root"
    assert manifest["target"]["report_date"] == REPORT_DATE
    assert manifest["target"]["workspace_path"] == str(workspace)
    assert manifest["include_raw_sessions"] is False
    assert metadata_archive_path in manifest["included_paths"]
    assert raw_session_archive_path in manifest["excluded_paths"]
    assert (
        manifest["file_checksums_sha256"][metadata_archive_path]
        == hashlib.sha256((workspace / "metadata.json").read_bytes()).hexdigest()
    )


def test_collect_missing_date_root_workspace_fails_without_preparing(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"

    result = CliRunner().invoke(
        app,
        [
            "collect",
            "--date",
            REPORT_DATE,
            "--timezone",
            "UTC",
            "--reports-root",
            str(reports_root),
        ],
    )

    assert result.exit_code == 2
    assert "prepared workspace is missing" in result.stderr
    assert not (reports_root / "work" / REPORT_DATE).exists()


def test_collect_date_root_omits_missing_audit_manifest(tmp_path: Path) -> None:
    reports_root, _workspace = _write_prepared_workspace(tmp_path)
    (reports_root / "private" / REPORT_DATE / "audit.manifest.json").unlink()

    result = CliRunner().invoke(
        app,
        [
            "collect",
            "--date",
            REPORT_DATE,
            "--timezone",
            "UTC",
            "--reports-root",
            str(reports_root),
        ],
    )

    archive_path = reports_root / "collections" / f"{REPORT_DATE}.zip"
    assert result.exit_code == 0, result.output
    assert f"private/{REPORT_DATE}/audit.manifest.json" not in _archive_names(archive_path)


def test_collect_workspace_mode_preserves_direct_workspace_name(tmp_path: Path) -> None:
    _, prepared_workspace = _write_prepared_workspace(tmp_path)
    direct_workspace = tmp_path / "support-workspace"
    shutil.copytree(prepared_workspace, direct_workspace)

    result = CliRunner().invoke(app, ["collect", "--workspace", str(direct_workspace)])

    archive_path = tmp_path / "collections" / "support-workspace.zip"
    names = _archive_names(archive_path)
    manifest = _read_collection_manifest(archive_path)
    assert result.exit_code == 0, result.output
    assert result.stdout == f"Wrote collection bundle {archive_path}\n"
    assert f"support-workspace/projects/{PROJECT_KEY}/project.json" in names
    assert f"work/{REPORT_DATE}/projects/{PROJECT_KEY}/project.json" not in names
    assert manifest["target"]["mode"] == "workspace"
    assert manifest["target"]["workspace_archive_root"] == "support-workspace"


def test_collect_workspace_mode_includes_reports_root_audit_when_locatable(
    tmp_path: Path,
) -> None:
    reports_root, prepared_workspace = _write_prepared_workspace(tmp_path)

    result = CliRunner().invoke(app, ["collect", "--workspace", str(prepared_workspace)])

    archive_path = reports_root / "work" / "collections" / f"{REPORT_DATE}.zip"
    manifest = _read_collection_manifest(archive_path)
    audit_archive_path = f"private/{REPORT_DATE}/audit.manifest.json"
    assert result.exit_code == 0, result.output
    assert audit_archive_path in _archive_names(archive_path)
    assert audit_archive_path in manifest["included_paths"]


def test_collect_rejects_output_inside_workspace(tmp_path: Path) -> None:
    _reports_root, workspace = _write_prepared_workspace(tmp_path)
    output_path = workspace / "support-bundle.zip"

    result = CliRunner().invoke(
        app,
        ["collect", "--workspace", str(workspace), "--output", str(output_path)],
    )

    assert result.exit_code == 2
    assert "--output must be outside the prepared workspace" in result.stderr
    assert not output_path.exists()


@pytest.mark.parametrize(
    "conflicting_args",
    [
        ["--date", REPORT_DATE],
        ["--today"],
        ["--timezone", "UTC"],
        ["--reports-root", "reports"],
    ],
)
def test_collect_workspace_mode_rejects_date_root_conflicts(
    tmp_path: Path,
    conflicting_args: list[str],
) -> None:
    result = CliRunner().invoke(
        app,
        ["collect", "--workspace", str(tmp_path / "workspace"), *conflicting_args],
    )

    assert result.exit_code == 2
    assert "--workspace cannot be combined with" in result.stderr


def test_collect_include_raw_sessions_writes_requested_output_and_warns(tmp_path: Path) -> None:
    reports_root, _workspace = _write_prepared_workspace(tmp_path)
    archive_path = tmp_path / "support-bundle.zip"
    raw_session_archive_path = (
        f"work/{REPORT_DATE}/projects/{PROJECT_KEY}/sessions/codex/session-001.jsonl"
    )

    result = CliRunner().invoke(
        app,
        [
            "collect",
            "--date",
            REPORT_DATE,
            "--timezone",
            "UTC",
            "--reports-root",
            str(reports_root),
            "--include-raw-sessions",
            "--output",
            str(archive_path),
        ],
    )

    names = _archive_names(archive_path)
    manifest = _read_collection_manifest(archive_path)
    assert result.exit_code == 0, result.output
    assert result.stdout == f"Wrote collection bundle {archive_path}\n"
    assert "raw assistant transcript content" in result.stderr
    assert archive_path.exists()
    assert not (reports_root / "collections" / f"{REPORT_DATE}.zip").exists()
    assert raw_session_archive_path in names
    assert raw_session_archive_path in manifest["included_paths"]
    assert raw_session_archive_path not in manifest["excluded_paths"]
    assert manifest["include_raw_sessions"] is True


def _write_prepared_workspace(tmp_path: Path) -> tuple[Path, Path]:
    reports_root = tmp_path / "reports"
    target = resolve_report_target(date=REPORT_DATE, today=False, timezone_name="UTC")
    prepared = prepare_workspace(target, reports_root=reports_root, source_specs=())
    workspace = prepared.workspace_path
    _write_generated_workspace_artifacts(workspace)
    return reports_root, workspace


def _write_generated_workspace_artifacts(workspace: Path) -> None:
    project_dir = workspace / "projects" / PROJECT_KEY
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 2,
            "project_key": PROJECT_KEY,
            "project_label": "ReportGenerator",
        },
    )
    _write_jsonl(
        project_dir / "sessions.index.jsonl",
        [
            {
                "session_ref": "S0001",
                "source": "codex",
                "source_session_id": "session-001",
                "session_path": "sessions/codex/session-001.jsonl",
                "target_start_line": 1,
                "target_end_line": 2,
                "turns": [{"turn_ref": "T0001", "turn_start_line": 1, "turn_end_line": 2}],
            }
        ],
    )
    _write_json(project_dir / "evidence" / "S0001.json", {"schema_version": 1})
    _write_json(project_dir / "project-synthesis.json", {"schema_version": 1})
    _write_json(workspace / "daily-report.json", {"schema_version": 1})
    _write_json(workspace / "report.notion.json", {"schema_version": 1})
    (workspace / "report.md").write_text("# Report\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("Generated instructions\n", encoding="utf-8")
    (project_dir / "sessions" / "codex").mkdir(parents=True)
    (project_dir / "sessions" / "codex" / "session-001.jsonl").write_text(
        '{"type":"session_metadata"}\n',
        encoding="utf-8",
    )


def _archive_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _read_collection_manifest(path: Path) -> _CollectionManifest:
    with zipfile.ZipFile(path) as archive:
        raw = json.loads(archive.read("collection.manifest.json"))
    assert isinstance(raw, dict)
    return cast("_CollectionManifest", raw)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _one_line(text: str) -> str:
    return " ".join(ANSI_ESCAPE_PATTERN.sub("", text).split())
