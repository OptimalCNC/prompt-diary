from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from prompt_diary.generate.evidence_extraction.mcp import validate_evidence_chain_against_turn
from prompt_diary.generate.evidence_extraction.model import (
    InvalidEvidenceChain,
    ParsedEvidenceChain,
    ParsedEvidenceChainDraft,
    evidence_chain_to_json,
    parse_evidence_chain,
    parse_evidence_chain_draft,
)
from prompt_diary.generate.workspace import LineSpan
from tests.support.evidence_extraction import (
    SESSION_REF,
    assert_appended_result,
    build_committed_evidence_chain,
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


def test_build_material_chain_is_accepted_for_single_line_turn() -> None:
    chain = build_evidence_chain(turn_ref="T0002", span=(10, 10))
    parsed = parse_evidence_chain_draft(chain)
    assert isinstance(parsed, ParsedEvidenceChainDraft)
    assert validate_evidence_chain_against_turn(parsed.chain, LineSpan(10, 10)) == ()


def test_build_no_material_chain_has_empty_outcomes() -> None:
    chain = build_evidence_chain(turn_ref="T0002", span=(9, 10), kind="no_material")
    assert chain["outcomes"] == []
    assert chain["terminal_state"]["type"] == "no_material"
    assert chain["materiality"] == "none"


def test_build_no_material_chain_is_accepted_by_validator(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = build_evidence_chain(turn_ref="T0002", span=(9, 10), kind="no_material")
    result = call_write_evidence_api(
        workspace_path=workspace, session_ref=SESSION_REF, evidence_chain=chain
    )
    assert_appended_result(result, turn_ref="T0002")


def test_committed_parser_requires_tool_owned_quoted_messages() -> None:
    draft = build_evidence_chain(turn_ref="T0001", span=(2, 8))

    parsed = parse_evidence_chain(draft)

    assert isinstance(parsed, InvalidEvidenceChain)
    assert any(error.path == "evidence_chain.trigger.quoted_messages" for error in parsed.errors)


@pytest.mark.parametrize("text", ["", " \n\t", "原文\nsecond line"])
def test_committed_chain_round_trips_verbatim_quote_text(text: str) -> None:
    chain = build_committed_evidence_chain(turn_ref="T0001", span=(2, 8), messages=(text,))

    parsed = parse_evidence_chain(chain)

    assert isinstance(parsed, ParsedEvidenceChain)
    assert evidence_chain_to_json(parsed.chain) == chain


def test_committed_parser_rejects_legacy_per_quote_citations() -> None:
    chain = build_committed_evidence_chain(turn_ref="T0001", span=(2, 8))
    chain["trigger"]["quoted_messages"][0]["citations"] = [{"lines": "2-2"}]

    parsed = parse_evidence_chain(chain)

    assert isinstance(parsed, InvalidEvidenceChain)
    assert any(
        error.path == "evidence_chain.trigger.quoted_messages[0].citations"
        for error in parsed.errors
    )


@pytest.mark.parametrize(
    ("quoted_messages", "error_path"),
    [
        ("not-a-list", "evidence_chain.trigger.quoted_messages"),
        ([{"text": 42}], "evidence_chain.trigger.quoted_messages[0].text"),
    ],
)
def test_committed_parser_rejects_malformed_tool_owned_messages(
    quoted_messages: object, error_path: str
) -> None:
    chain = build_committed_evidence_chain(turn_ref="T0001", span=(2, 8))
    chain["trigger"]["quoted_messages"] = quoted_messages

    parsed = parse_evidence_chain(chain)

    assert isinstance(parsed, InvalidEvidenceChain)
    assert any(error.path == error_path for error in parsed.errors)
