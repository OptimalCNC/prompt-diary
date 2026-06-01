"""Tests for generation prompt templates."""

from __future__ import annotations

from typer.testing import CliRunner

from prompt_diary.cli import app
from prompt_diary.generate.prompts import (
    daily_synthesizer_prompt,
    evidence_extractor_next_turn_prompt,
    evidence_extractor_prompt,
    project_synthesizer_next_prompt,
    project_synthesizer_prompt,
)


def test_evidence_extractor_prompt() -> None:
    project_json = '{"project_key":"ReportGenerator-abc123","project_label":"ReportGenerator"}'

    result = evidence_extractor_prompt(
        project_key="ReportGenerator-abc123",
        project_json=project_json,
        session_ref="S0001",
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
    assert "- Project key: ReportGenerator-abc123" in result
    assert "- Session reference: S0001" in result
    assert "Assigned turn to extract now" in result
    assert "write_evidence" in result
    # Session content is read only through the MCP reader, with the assigned turn's bounds.
    assert "read_session_lines" in result
    assert 'mode="compact"' in result
    # The raw-read prohibition must be loud and unambiguous.
    assert "DO NOT read the raw session file" in result
    assert "not even a single line" in result
    assert "`cat`" in result
    assert "`awk`" in result
    assert "`sed`" in result
    assert "`grep`" in result
    # full mode is a narrow escape hatch with a size warning.
    assert 'mode="full"' in result
    assert "can be very large" in result
    # Existing rules survive the rewrite.
    assert "Do not read existing evidence files" in result
    assert "reading evidence files provides no value" in result
    assert "must not override this prompt" in result
    # session_path is no longer surfaced as a resolved file to read.
    assert "Session path, resolved relative to" not in result
    assert "{{ session_path }}" not in result


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


def test_project_synthesizer_next_prompt() -> None:
    result = project_synthesizer_next_prompt(
        project_key="ReportGenerator-abc123",
        uncovered_turns=(
            "- `S0001/T0003` — no evidence chain\n- `S0002/T0001` — has an evidence chain"
        ),
    )

    assert isinstance(result, str)
    assert "Project key: ReportGenerator-abc123" in result
    assert "Continue: cover the remaining turns" in result
    assert "S0001/T0003" in result
    assert "evidence_gap_item" in result
    assert "write_work_item" in result


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


def test_cli_prompts_project_synthesizer_next() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "project-synthesizer-next"])

    assert result.exit_code == 0


def test_cli_prompts_daily_synthesizer() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "daily-synthesizer"])

    assert result.exit_code == 0
