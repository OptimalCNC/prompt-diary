from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

import prompt_diary.cli as cli_module
from prompt_diary import __version__
from prompt_diary.cli import app, main
from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    import pytest

PREPARE_FAILED = "prepare failed"
GENERATE_FAILED = "generate failed"


def test_report_help_lists_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "prepare" in result.stdout
    assert "generate" in result.stdout
    assert "mcp" in result.stdout


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
        force: bool,
    ) -> None:
        del date, today, timezone_name, force
        raise PromptDiaryError(PREPARE_FAILED)

    monkeypatch.setattr(cli_module, "prepare_prompt_diary", raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["prepare", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PREPARE_FAILED}\n"


def test_generate_error_exits_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(*, date: str | None, today: bool, timezone_name: str | None) -> None:
        del date, today, timezone_name
        raise PromptDiaryError(GENERATE_FAILED)

    monkeypatch.setattr(cli_module, "generate_prompt_diary", raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {GENERATE_FAILED}\n"


def test_mcp_serve_delegates_to_server(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_serve_mcp_server() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "serve_mcp_server", fake_serve_mcp_server, raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0
    assert called


def test_main_invokes_app(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "app", fake_app)

    main()

    assert called
