from __future__ import annotations

from typing import TYPE_CHECKING

from tests.support.evidence_extraction import (
    SESSION_REF,
    assert_appended_result,
    build_evidence_chain,
    call_write_evidence_api,
    copy_basic_evidence_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_build_material_chain_is_accepted_for_full_turn_span(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = build_evidence_chain(turn_ref="T0001", span=(2, 8))
    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)
    assert_appended_result(result, turn_ref="T0001")


def test_build_material_chain_is_accepted_for_single_line_turn(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = build_evidence_chain(turn_ref="T0002", span=(10, 10))
    result = call_write_evidence_api(
        workspace_path=workspace, session_ref=SESSION_REF, evidence_chain=chain
    )
    assert_appended_result(result, turn_ref="T0002")


def test_build_no_material_chain_has_empty_outcomes() -> None:
    chain = build_evidence_chain(turn_ref="T0002", span=(9, 10), kind="no_material")
    assert chain["outcomes"] == []
    assert chain["terminal_state"]["type"] == "no_material"
    assert chain["materiality"] == "none"
