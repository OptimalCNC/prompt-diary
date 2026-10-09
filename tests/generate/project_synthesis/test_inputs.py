from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.project_synthesis.cards import load_committed_chains
from prompt_diary.generate.project_synthesis.inputs import (
    build_project_synthesis_inputs,
    render_evidence_chains,
)
from tests.support.project_synthesis import PROJECT_KEY, copy_basic_project_workspace

if TYPE_CHECKING:
    from pathlib import Path


def test_inputs_expose_project_key_and_normalized_project_json(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    inputs = build_project_synthesis_inputs(workspace_path=workspace, project_key=PROJECT_KEY)

    assert inputs.project_key == PROJECT_KEY
    assert '"project_label": "ReportGenerator"' in inputs.project_json


def test_paste_groups_by_session_with_labelled_turns(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    inputs = build_project_synthesis_inputs(workspace_path=workspace, project_key=PROJECT_KEY)
    paste = inputs.evidence_chains

    assert "#### Session S0001 (2 chains)" in paste
    assert "#### Session S0002 (1 chain)" in paste
    assert "**S0001/T0001** [material]" in paste
    assert "**S0001/T0002** [minor]" in paste
    assert "**S0002/T0001** [material]" in paste
    # The gap turn S0001/T0003 has no committed chain, so it never appears in the paste.
    assert "T0003" not in paste


def test_paste_includes_redacted_messages_and_observed_checks(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    card_path = workspace / "projects" / PROJECT_KEY / "evidence" / "S0001.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["evidence_chains"][0]["observed_checks"] = [
        {"summary": "The updated contract was inspected.", "citations": [{"lines": "7-7"}]}
    ]
    card["evidence_chains"][0]["trigger"]["quoted_messages"][0]["text"] += "\nToken: [REDACTED]"
    card_path.write_text(json.dumps(card), encoding="utf-8")

    paste = build_project_synthesis_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY
    ).evidence_chains

    assert "trigger: User asked to simplify the MCP evidence tools" in paste
    assert "reaction: Updated the MCP tools page" in paste
    assert "- document_outcome: Top-level turn_ref adopted" in paste
    assert "extracted_terminal_label: material_result" in paste
    assert "segment_end_evidence: Extraction surface updated" in paste
    assert 'user_messages:\n- "Please simplify the MCP evidence tools and drop chain_ref.' in paste
    assert "\\nToken: [REDACTED]" in paste
    assert "observed_checks:\n- The updated contract was inspected." in paste
    assert "trigger_at: unknown" in paste
    assert "evidence_end_at: unknown" in paste
    # The synthesizer cites turns; physical line citations remain in the evidence card.
    assert "lines" not in paste


def test_paste_omits_empty_reaction_and_outcomes(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    chains = load_committed_chains(workspace, PROJECT_KEY)
    minor = next(chain for chain in chains if chain.turn_ref == "T0002")

    block = render_evidence_chains((minor,))

    assert "**S0001/T0002** [minor]" in block
    assert "extracted_terminal_label: clarification_only" in block
    assert "outcomes:" not in block


def test_paste_distinguishes_an_extracted_interruption_label_from_stop_evidence(
    tmp_path: Path,
) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    chain = replace(
        load_committed_chains(workspace, PROJECT_KEY)[0],
        terminal_type="interrupted",
        terminal_summary="The segment ended pending the user's document structure confirmation.",
    )

    paste = render_evidence_chains((chain,))

    assert "Terminal labels and segment-end summaries are AI extraction judgments." in paste
    assert "A label alone is not evidence that work actually stopped" in paste
    assert "extracted_terminal_label: interrupted\nsegment_end_evidence:" in paste
    assert "pending the user's document structure confirmation" in paste
    assert "terminal: interrupted:" not in paste


def test_paste_carries_source_timing_with_the_quoted_scope_evidence(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    source_path = workspace / "projects" / PROJECT_KEY / "sessions" / "codex" / "session-001.jsonl"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = [{} for _ in range(8)]
    records[1] = {
        "timestamp": "2026-05-28T02:00:00Z",
        "type": "event_msg",
        "payload": {"type": "user_message", "message": "Please simplify the MCP evidence tools."},
    }
    records[7] = {"timestamp": "2026-05-28T02:30:00Z"}
    source_path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    paste = build_project_synthesis_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY
    ).evidence_chains

    assert "trigger_at: 2026-05-28T02:00:00+00:00" in paste
    assert "evidence_end_at: 2026-05-28T02:30:00+00:00" in paste
    assert 'user_messages:\n- "Please simplify the MCP evidence tools and drop chain_ref."' in paste


def test_render_empty_when_no_committed_chains() -> None:
    assert render_evidence_chains(()) == "(No extracted evidence chains for this project.)"


def test_build_inputs_rejects_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    with pytest.raises(PromptDiaryError, match="unknown project_key"):
        build_project_synthesis_inputs(workspace_path=workspace, project_key="Missing-000000000000")
