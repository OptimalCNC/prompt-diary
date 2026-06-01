from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from tests.support.project_synthesis import (
    COMMITTED_TURNS,
    PROJECT_KEY,
    copy_basic_project_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_load_returns_committed_chains_in_index_then_card_order(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    chains = load_committed_chains(workspace, PROJECT_KEY)

    assert [(chain.session_ref, chain.turn_ref) for chain in chains] == [
        ("S0001", "T0001"),
        ("S0001", "T0002"),
        ("S0002", "T0001"),
    ]


def test_load_skips_the_gap_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    keys = committed_turn_keys(load_committed_chains(workspace, PROJECT_KEY))

    assert keys == set(COMMITTED_TURNS)
    assert ("S0001", "T0003") not in keys


def test_committed_chain_carries_trimmed_fields_and_verbatim_quotes(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    chains = load_committed_chains(workspace, PROJECT_KEY)
    first = chains[0]

    assert first.materiality == "material"
    assert "simplify" in first.trigger_summary
    assert first.reaction_summaries == (
        "Updated the MCP tools page, evidence contract, and extractor prompt.",
    )
    assert first.outcomes[0].category == "document_outcome"
    assert first.terminal_type == "material_result"
    assert first.messages == ("Please simplify the MCP evidence tools and drop chain_ref.",)


def test_load_tolerates_a_missing_card(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    (workspace / "projects" / PROJECT_KEY / "evidence" / "S0002.json").unlink()

    keys = committed_turn_keys(load_committed_chains(workspace, PROJECT_KEY))

    assert keys == {("S0001", "T0001"), ("S0001", "T0002")}


def test_load_returns_empty_for_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    assert load_committed_chains(workspace, "Missing-000000000000") == ()
