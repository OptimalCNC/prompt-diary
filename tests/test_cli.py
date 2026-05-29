from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import prompt_diary.cli as cli_module
import prompt_diary.cmds.codex as codex_cmd
import prompt_diary.cmds.generate as generate_cmd
import prompt_diary.cmds.mcp as mcp_cmd
import prompt_diary.cmds.prepare as prepare_cmd
from prompt_diary import __version__
from prompt_diary.cli import app, main
from prompt_diary.errors import PromptDiaryError
from prompt_diary.integrations.codex_bootstrap import CodexBootstrapResult, CodexBootstrapTarget
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from pathlib import Path

PREPARE_FAILED = "prepare failed"
GENERATE_FAILED = "generate failed"
BOOTSTRAP_FAILED = "bootstrap failed"
PHASE_FAILED = "phase failed"


def test_report_help_lists_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "prepare" in result.stdout
    assert "generate" in result.stdout
    assert "codex" in result.stdout
    assert "mcp" in result.stdout


def test_generate_help_lists_phase_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "--help"])

    assert result.exit_code == 0
    assert "evidence" in result.stdout
    assert "project" in result.stdout
    assert "daily" in result.stdout


def test_report_version() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_prepare_error_exits_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> None:
        del date, today, timezone_name
        raise PromptDiaryError(PREPARE_FAILED)

    monkeypatch.setattr(prepare_cmd, "resolve_report_target", raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["prepare", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PREPARE_FAILED}\n"


def test_generate_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_generate_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> tuple[Path, tuple[str, ...]]:
        del date, today, timezone_name
        return tmp_path, ()

    def raise_error(*, workspace_path: Path, messages: tuple[str, ...]) -> None:
        del workspace_path, messages
        raise PromptDiaryError(GENERATE_FAILED)

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_generate_target",
        workspace_for_generate_target,
    )
    monkeypatch.setattr(generate_cmd, "run_generate_pipeline", raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {GENERATE_FAILED}\n"


def test_generate_prints_pipeline_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Result:
        messages = ("prepared", "generated")

    def workspace_for_generate_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> tuple[Path, tuple[str, ...]]:
        del date, today, timezone_name
        return tmp_path, ("prepared",)

    def fake_run_generate_pipeline(*, workspace_path: Path, messages: tuple[str, ...]) -> Result:
        del workspace_path, messages
        return Result()

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_generate_target",
        workspace_for_generate_target,
    )
    monkeypatch.setattr(generate_cmd, "run_generate_pipeline", fake_run_generate_pipeline)
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "--date", "2026-05-12"])

    assert result.exit_code == 0
    assert result.stdout == "prepared\ngenerated\n"


def test_generate_phase_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    def raise_error(
        *,
        workspace_path: Path,
        phase: str,
        project_key: str | None = None,
        session_ref: str | None = None,
    ) -> None:
        del workspace_path, phase, project_key, session_ref
        raise PromptDiaryError(PHASE_FAILED)

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(generate_cmd, "run_generate_phase", raise_error)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "generate",
            "evidence",
            "--date",
            "2026-05-12",
            "--project-key",
            "Project-123",
            "--session-ref",
            "S0001",
        ],
    )

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PHASE_FAILED}\n"


def test_generate_project_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    def raise_error(
        *,
        workspace_path: Path,
        phase: str,
        project_key: str | None = None,
        session_ref: str | None = None,
    ) -> None:
        del workspace_path, phase, project_key, session_ref
        raise PromptDiaryError(PHASE_FAILED)

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(generate_cmd, "run_generate_phase", raise_error)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["generate", "project", "--date", "2026-05-12", "--project-key", "Project-123"],
    )

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PHASE_FAILED}\n"


def test_generate_daily_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    def raise_error(
        *,
        workspace_path: Path,
        phase: str,
        project_key: str | None = None,
        session_ref: str | None = None,
    ) -> None:
        del workspace_path, phase, project_key, session_ref
        raise PromptDiaryError(PHASE_FAILED)

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(generate_cmd, "run_generate_phase", raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "daily", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PHASE_FAILED}\n"


def test_generate_phase_commands_delegate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str | None, str | None]] = []

    class Result:
        messages = ("completed",)

    def fake_run_generate_phase(
        *,
        workspace_path: Path,
        phase: str,
        project_key: str | None = None,
        session_ref: str | None = None,
    ) -> Result:
        del workspace_path
        calls.append((phase, project_key, session_ref))
        return Result()

    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(generate_cmd, "run_generate_phase", fake_run_generate_phase)
    runner = CliRunner()

    evidence = runner.invoke(
        app,
        [
            "generate",
            "evidence",
            "--date",
            "2026-05-12",
            "--project-key",
            "Project-123",
            "--session-ref",
            "S0001",
        ],
    )
    project = runner.invoke(
        app,
        ["generate", "project", "--date", "2026-05-12", "--project-key", "Project-123"],
    )
    daily = runner.invoke(app, ["generate", "daily", "--date", "2026-05-12"])

    assert evidence.exit_code == 0
    assert project.exit_code == 0
    assert daily.exit_code == 0
    assert calls == [
        ("evidence", "Project-123", "S0001"),
        ("project", "Project-123", None),
        ("daily", None, None),
    ]


def test_generate_existing_workspace_resolution(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
    )
    prepared = prepare_workspace(target, reports_root=reports_root, source_specs=())

    workspace_path = generate_cmd.workspace_for_existing_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        reports_root=reports_root,
    )

    assert workspace_path == prepared.workspace_path


def test_generate_existing_workspace_resolution_requires_workspace(tmp_path: Path) -> None:
    with pytest.raises(PromptDiaryError, match="run prepare first"):
        generate_cmd.workspace_for_existing_target(
            date="2026-05-12",
            today=False,
            timezone_name="Asia/Shanghai",
            reports_root=tmp_path / ".reports",
        )


def test_mcp_serve_delegates_to_server(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_serve_mcp_server() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(mcp_cmd, "serve_mcp_server", fake_serve_mcp_server)
    runner = CliRunner()

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0
    assert called


def test_codex_bootstrap_prints_bootstrap_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_bootstrap_codex_sdk() -> CodexBootstrapResult:
        target = CodexBootstrapTarget(
            python_executable=str(tmp_path / "bin" / "python"),
            environment_root=tmp_path,
            site_packages=tmp_path / "site-packages",
            uv_marker=None,
            is_system_python=False,
        )
        return CodexBootstrapResult(
            target=target,
            package_spec="openai-codex",
            import_path="openai_codex",
            messages=("installed", "verified"),
        )

    monkeypatch.setattr(codex_cmd, "bootstrap_codex_sdk", fake_bootstrap_codex_sdk)
    runner = CliRunner()

    result = runner.invoke(app, ["codex", "bootstrap"])

    assert result.exit_code == 0
    assert result.stdout == "installed\nverified\n"


def test_codex_bootstrap_error_exits_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error() -> None:
        raise PromptDiaryError(BOOTSTRAP_FAILED)

    monkeypatch.setattr(codex_cmd, "bootstrap_codex_sdk", raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["codex", "bootstrap"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {BOOTSTRAP_FAILED}\n"


def test_main_invokes_app(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "app", fake_app)

    main()

    assert called
