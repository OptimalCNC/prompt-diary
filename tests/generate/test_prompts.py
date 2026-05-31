"""Tests for generation prompt templates."""

from __future__ import annotations

from typer.testing import CliRunner

from prompt_diary.cli import app
from prompt_diary.generate.prompts import (
    daily_synthesizer_prompt,
    evidence_extractor_next_turn_prompt,
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
        target_turn=(
            '{"turn_ref":"T0001","turn_start_line":1,"turn_end_line":10,"target_subagents":[]}'
        ),
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Project key: ReportGenerator-abc123" in result
    assert "Assigned turn to extract now" in result
    assert "write_evidence" in result
    assert "Do not read existing evidence files" in result
    assert "reading evidence files provides no value" in result


def test_evidence_extractor_next_turn_prompt() -> None:
    result = evidence_extractor_next_turn_prompt(
        write_evidence_result='{"status":"appended","turn_ref":"T0001"}',
        target_turn='{"turn_ref":"T0002","turn_start_line":11,"turn_end_line":20}',
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "The previous turn was written successfully" in result
    assert "T0002" in result
    assert "write_evidence" in result


def test_project_synthesizer_prompt() -> None:
    result = project_synthesizer_prompt(
        project_key="ReportGenerator-abc123",
        project_json='{"project_key":"ReportGenerator-abc123","project_label":"ReportGenerator"}',
        evidence_chains=(
            "#### Session S0001 (2 chains)\n"
            "\n"
            "**S0001/T0001** [material]\n"
            "trigger: User asked to simplify the MCP evidence tools and remove chain_ref.\n"
            "reaction: Updated the MCP tools page, evidence contract, and extractor prompt.\n"
            "outcomes:\n"
            "- document_outcome: top-level turn_ref; chain_ref removed.\n"
            "terminal: material_result: extraction surface updated to turn_ref identity.\n"
            "\n"
            "**S0001/T0002** [minor]\n"
            "trigger: User asked whether the placeholder was misleading.\n"
            "terminal: clarification_only: wording direction chosen.\n"
            "\n"
            "#### Session S0002 (1 chain)\n"
            "\n"
            "**S0002/T0001** [material]\n"
            "trigger: User asked to design the evidence-extraction QA approach.\n"
            "terminal: material_result: QA design delivered.\n"
        ),
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Project key: ReportGenerator-abc123" in result
    assert "write_work_item" in result
    assert "#### Session S0001" in result
    assert "#### Session S0002" in result
    assert "S0001/T0001" in result


def test_daily_synthesizer_prompt() -> None:
    result = daily_synthesizer_prompt()

    assert isinstance(result, str)
    assert len(result) > 0


def test_cli_prompts_evidence_extractor() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "evidence-extractor"])

    assert result.exit_code == 0


def test_cli_prompts_evidence_extractor_next_turn() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "evidence-extractor-next-turn"])

    assert result.exit_code == 0


def test_cli_prompts_project_synthesizer() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "project-synthesizer"])

    assert result.exit_code == 0


def test_cli_prompts_daily_synthesizer() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "daily-synthesizer"])

    assert result.exit_code == 0
