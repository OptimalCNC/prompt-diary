"""Tests for generation prompt templates."""

from __future__ import annotations

from typer.testing import CliRunner

from prompt_diary.cli import app
from prompt_diary.generate.prompts import (
    engagement_prompt,
    evidence_extractor_next_turn_prompt,
    evidence_extractor_prompt,
    project_summary_prompt,
    project_synthesizer_next_prompt,
    project_synthesizer_prompt,
    report_title_prompt,
    team_learning_prompt,
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
    # The agent must not narrate; the orchestrator reads the committed card, not assistant prose.
    assert "Work silently" in result
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
    # The next-turn prompt is a fresh agent turn, so it must re-state the MCP-only read rule and
    # the raw-session-file prohibition rather than relying on the initial prompt's context.
    assert "read_session_lines" in result
    assert "not even a single line" in result
    assert "Work silently" in result


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


def test_project_summary_prompt() -> None:
    result = project_summary_prompt(
        project_key="ReportGenerator-abc123",
        project_json='{"project_key":"ReportGenerator-abc123","project_label":"ReportGenerator"}',
        work_items="**W0001** material_work_item: simplified the MCP evidence tools.",
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Project key: ReportGenerator-abc123" in result
    assert "write_project_summary" in result
    assert "W0001" in result


def test_report_title_prompt() -> None:
    result = report_title_prompt(
        context=(
            "report_date: 2026-05-28\n"
            "status: final\n"
            "**ReportGenerator-e6ff7eeda632 · W0001**\n"
            "summary: Simplified the evidence tools and designed the QA approach.\n"
            "title: Simplify the MCP evidence tools and drop chain_ref\n"
            "cite: ReportGenerator-e6ff7eeda632/S0001/T0001"
        ),
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "write_report_title" in result
    assert "must not include the report date" in result
    assert "Prompt Diary Report" in result
    assert "chain_ref" in result


def test_engagement_prompt() -> None:
    result = engagement_prompt(
        work_items="**W0001** material_work_item: simplified the MCP evidence tools.",
        source_user_messages="S0001/T0001: simplify the evidence tools; drop chain_ref.",
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "write_engagement" in result
    assert "direction" in result
    assert "score or grade" in result
    assert "W0001" in result
    assert "chain_ref" in result


def test_team_learning_prompt() -> None:
    result = team_learning_prompt(
        work_items="**W0001** material_work_item: simplified the MCP evidence tools.",
        source_user_messages="S0001/T0001: simplify the evidence tools; drop chain_ref.",
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "write_team_learning" in result
    assert "promote" in result
    assert "productivity" in result
    assert "W0001" in result
    assert "acceptance criteria" in result
    assert "examples or counterexamples" in result
    assert "verification or tests" in result
    assert "pattern -> evidence -> why it mattered -> how teammates can reuse or avoid it" in result


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


def test_cli_prompts_project_summary() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "project-summary"])

    assert result.exit_code == 0


def test_cli_prompts_report_title() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "report-title"])

    assert result.exit_code == 0


def test_cli_prompts_engagement() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "engagement"])

    assert result.exit_code == 0


def test_cli_prompts_team_learning() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prompts", "team-learning"])

    assert result.exit_code == 0
