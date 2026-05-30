"""prepare command progress wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from prompt_diary.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_prepare_accepts_quiet_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROMPT_DIARY_CODEX_SESSIONS", "")
    monkeypatch.setenv("PROMPT_DIARY_CLAUDE_PROJECTS", "")
    result = CliRunner().invoke(
        app, ["prepare", "--date", "2026-05-30", "--timezone", "UTC", "--quiet"]
    )
    assert result.exit_code == 0
    assert "Prepared workspace" in result.stdout
