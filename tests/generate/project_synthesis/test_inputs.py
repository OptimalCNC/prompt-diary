from __future__ import annotations

from typing import TYPE_CHECKING

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


def test_paste_is_trimmed_to_summaries(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    paste = build_project_synthesis_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY
    ).evidence_chains

    assert "trigger: User asked to simplify the MCP evidence tools" in paste
    assert "reaction: Updated the MCP tools page" in paste
    assert "- document_outcome: Top-level turn_ref adopted" in paste
    assert "terminal: material_result: Extraction surface updated" in paste
    # No citations or quoted message text leak into the paste.
    assert "lines" not in paste
    assert "Please simplify the MCP evidence tools and drop chain_ref." not in paste


def test_paste_omits_empty_reaction_and_outcomes(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    chains = load_committed_chains(workspace, PROJECT_KEY)
    minor = next(chain for chain in chains if chain.turn_ref == "T0002")

    block = render_evidence_chains((minor,))

    assert "**S0001/T0002** [minor]" in block
    assert "terminal: clarification_only:" in block
    assert "outcomes:" not in block


def test_render_empty_when_no_committed_chains() -> None:
    assert render_evidence_chains(()) == "(No extracted evidence chains for this project.)"
