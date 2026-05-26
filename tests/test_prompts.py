"""Tests for generation prompt templates."""

from __future__ import annotations

from typer.testing import CliRunner

from prompt_diary.cli import app
from prompt_diary.prompts import (
    daily_synthesizer_prompt,
    evidence_extractor_prompt,
    project_synthesizer_prompt,
)


def test_evidence_extractor_prompt() -> None:
    result = evidence_extractor_prompt(
        working_dir="projects/ReportGenerator-abc123",
        session_ref="S0001",
    )

    assert isinstance(result, str)
    assert len(result) > 0


def test_project_synthesizer_prompt() -> None:
    result = project_synthesizer_prompt()

    assert isinstance(result, str)
    assert len(result) > 0


def test_daily_synthesizer_prompt() -> None:
    result = daily_synthesizer_prompt()

    assert isinstance(result, str)
    assert len(result) > 0


def test_cli_prompts_evidence_extractor() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "evidence-extractor"])

    assert result.exit_code == 0


def test_cli_prompts_project_synthesizer() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "project-synthesizer"])

    assert result.exit_code == 0


def test_cli_prompts_daily_synthesizer() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "daily-synthesizer"])

    assert result.exit_code == 0
