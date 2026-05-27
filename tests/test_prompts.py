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
    project_json = '{"project_key":"ReportGenerator-abc123","project_label":"ReportGenerator"}'

    result = evidence_extractor_prompt(
        project_key="ReportGenerator-abc123",
        project_json=project_json,
        session_ref="S0001",
        session_path="projects/ReportGenerator-abc123/sessions/codex/session.jsonl",
        session_index_record=(
            '{"session_ref":"S0001","session_path":"sessions/codex/session.jsonl",'
            '"target_start_line":1,"target_end_line":10}'
        ),
        target_turn='{"turn_start_line":1,"turn_end_line":10,"target_subagents":[]}',
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Project key: ReportGenerator-abc123" in result
    assert "Target turn to extract now" in result
    assert "write_evidence" in result


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
