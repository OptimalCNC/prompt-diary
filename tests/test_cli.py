from __future__ import annotations

from typer.testing import CliRunner

from prompt_diary import __version__
from prompt_diary.cli import app


def test_report_help_lists_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "prepare" in result.stdout
    assert "generate" in result.stdout


def test_report_version() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
