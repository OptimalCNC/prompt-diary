from __future__ import annotations

from prompt_diary.generate.evidence_extraction.model import new_session_card


def test_new_session_card_skeleton() -> None:
    card = new_session_card("Proj-1", "S0001")
    assert card == {
        "schema_version": 1,
        "project_key": "Proj-1",
        "session_ref": "S0001",
        "evidence_chains": [],
    }
