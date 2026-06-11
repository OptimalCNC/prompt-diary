from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from prompt_diary.generate.evidence_extraction.completeness import inspect_evidence_card
from prompt_diary.generate.pipeline import evidence_card_artifact
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    build_evidence_chain,
    copy_basic_evidence_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_inspector_reports_unknown_scope_and_missing_card(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)

    unknown_project = inspect_evidence_card(
        workspace_path=workspace,
        project_key="Missing-000000000000",
        session_ref=SESSION_REF,
    )
    unknown_session = inspect_evidence_card(
        workspace_path=workspace,
        project_key=PROJECT_KEY,
        session_ref="S9999",
    )
    missing_card = inspect_evidence_card(
        workspace_path=workspace,
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
    )

    assert not unknown_project.complete
    assert "unknown project_key" in unknown_project.errors[0]
    assert not unknown_session.complete
    assert "unknown session_ref" in unknown_session.errors[0]
    assert not missing_card.complete
    assert "missing evidence card" in missing_card.errors[0]


def test_inspector_reports_malformed_json_card(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text("{", encoding="utf-8")

    inspection = inspect_evidence_card(
        workspace_path=workspace,
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
    )

    assert not inspection.complete
    assert "schema_version must be 1" in inspection.errors
    assert "evidence_chains must be a list" in inspection.errors


def test_inspector_reports_envelope_and_chain_shape_errors(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    _write_card(
        workspace,
        {
            "schema_version": 2,
            "project_key": PROJECT_KEY,
            "session_ref": "S9999",
            "evidence_chains": "not-a-list",
        },
    )

    inspection = inspect_evidence_card(
        workspace_path=workspace,
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
    )

    assert not inspection.complete
    assert "schema_version must be 1" in inspection.errors
    assert "session_ref must be 'S0001'" in inspection.errors
    assert "evidence_chains must be a list" in inspection.errors


def test_inspector_reports_non_object_missing_turn_and_duplicate_chains(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    _write_card(
        workspace,
        {
            "schema_version": 1,
            "project_key": PROJECT_KEY,
            "session_ref": SESSION_REF,
            "evidence_chains": [
                "not-an-object",
                {"trigger": {}},
                build_evidence_chain(turn_ref="T0001", span=(2, 8)),
                build_evidence_chain(turn_ref="T0001", span=(2, 8)),
            ],
        },
    )

    inspection = inspect_evidence_card(
        workspace_path=workspace,
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
    )

    assert not inspection.complete
    assert "evidence_chains[0] must be a JSON object" in inspection.errors
    assert "duplicate turn_ref 'T0001'" in inspection.errors


def _write_card(workspace: Path, card: dict[str, Any]) -> None:
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
