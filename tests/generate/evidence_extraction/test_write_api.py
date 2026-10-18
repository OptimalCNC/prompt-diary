from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    assert_appended_result,
    assert_invalid_result,
    call_write_evidence_api,
    chain_with_value,
    copy_basic_evidence_workspace,
    deep_copy_json,
    evidence_card_text,
    load_evidence_card,
    material_result_without_outcomes_chain,
    valid_material_doc_chain,
    valid_no_material_chain,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_write_evidence_creates_session_card_and_appends_chain(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)

    result = call_write_evidence_api(workspace_path=workspace)

    assert_appended_result(result, turn_ref="T0001")
    card = load_evidence_card(workspace)
    assert card["schema_version"] == 2
    assert card["project_key"] == PROJECT_KEY
    assert card["session_ref"] == SESSION_REF
    assert "session_path" not in card
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001"]
    assert card["evidence_chains"][0]["trigger"] == {
        "citations": [{"lines": "2-2"}],
        "quoted_messages": [
            {"text": "Please update the evidence contract docs for the MCP write surface."}
        ],
    }


def test_write_evidence_appends_second_turn_without_modifying_first(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    call_write_evidence_api(workspace_path=workspace)
    first_committed_chain = deep_copy_json(load_evidence_card(workspace)["evidence_chains"][0])

    result = call_write_evidence_api(
        workspace_path=workspace,
        evidence_chain=valid_no_material_chain(),
    )

    assert_appended_result(result, turn_ref="T0002")
    chains = load_evidence_card(workspace)["evidence_chains"]
    assert [chain["turn_ref"] for chain in chains] == ["T0001", "T0002"]
    assert chains[0] == first_committed_chain


def test_write_evidence_rejects_duplicate_turn_ref_without_changing_card(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    call_write_evidence_api(workspace_path=workspace)
    before = evidence_card_text(workspace)

    result = call_write_evidence_api(workspace_path=workspace)

    assert_invalid_result(
        result,
        path="evidence_chain.turn_ref",
        message_contains="duplicate",
        hint_contains="one evidence chain",
    )
    assert evidence_card_text(workspace) == before


@pytest.mark.parametrize(
    ("case_name", "project_key", "session_ref", "evidence_chain", "error_path"),
    [
        ("project", "Missing-000000000000", SESSION_REF, valid_material_doc_chain(), "project_key"),
        ("session", PROJECT_KEY, "S9999", valid_material_doc_chain(), "session_ref"),
        (
            "turn",
            PROJECT_KEY,
            SESSION_REF,
            chain_with_value(("turn_ref",), "T9999"),
            "evidence_chain.turn_ref",
        ),
    ],
)
def test_write_evidence_rejects_unknown_project_session_or_turn(
    tmp_path: Path,
    case_name: str,
    project_key: str,
    session_ref: str,
    evidence_chain: dict[str, Any],
    error_path: str,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path / case_name)

    result = call_write_evidence_api(
        workspace_path=workspace,
        project_key=project_key,
        session_ref=session_ref,
        evidence_chain=evidence_chain,
    )

    assert_invalid_result(result, path=error_path)


def test_write_evidence_rejects_citations_outside_indexed_turn(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = chain_with_value(("outcomes", 0, "citations", 0, "lines"), "9-10")

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result,
        path="evidence_chain.outcomes[0].citations[0].lines",
        message_contains="outside",
        hint_contains="inside",
    )


@pytest.mark.parametrize("lines", ["2", 2])
def test_write_evidence_rejects_malformed_citation_spans(tmp_path: Path, lines: object) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = chain_with_value(("trigger", "citations", 0, "lines"), lines)

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result,
        path="evidence_chain.trigger.citations[0].lines",
        message_contains="span",
        hint_contains="start-end",
    )


def test_write_evidence_rejects_invalid_controlled_values(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = chain_with_value(("outcomes", 0, "category"), "documentation")

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result,
        path="evidence_chain.outcomes[0].category",
        message_contains="controlled",
        hint_contains="document_outcome",
    )


def test_write_evidence_rejects_empty_required_summaries(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = chain_with_value(("agent_reactions", 0, "summary"), "  ")

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result,
        path="evidence_chain.agent_reactions[0].summary",
        message_contains="non-empty",
        hint_contains="summary",
    )


def test_write_evidence_rejects_material_result_without_outcomes(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = material_result_without_outcomes_chain()

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result,
        path="evidence_chain.outcomes",
        message_contains="material_result",
        hint_contains="non-success ending",
    )


def test_write_evidence_rejects_material_outcome_citing_only_trigger_evidence(
    tmp_path: Path,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = chain_with_value(("outcomes", 0, "citations", 0, "lines"), "2-2")

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result,
        path="evidence_chain.outcomes[0].citations[0].lines",
        message_contains="trigger",
        hint_contains="agent reaction",
    )


def test_write_evidence_leaves_existing_card_unchanged_after_rejected_write(
    tmp_path: Path,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    call_write_evidence_api(workspace_path=workspace)
    before = evidence_card_text(workspace)
    invalid_second_chain = chain_with_value(("turn_ref",), "T0002")
    invalid_second_chain["trigger"] = valid_no_material_chain()["trigger"]
    invalid_second_chain["outcomes"][0]["citations"][0]["lines"] = "2-2"

    result = call_write_evidence_api(
        workspace_path=workspace,
        evidence_chain=invalid_second_chain,
    )

    assert_invalid_result(result, path="evidence_chain.outcomes[0].citations[0].lines")
    assert evidence_card_text(workspace) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quoted_messages", [{"text": "Agent-invented quotation"}]),
        ("type", "explicit_user_message"),
        ("summary", "Agent interpretation"),
        ("other", "unsupported"),
    ],
)
def test_write_evidence_rejects_agent_owned_trigger_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = valid_material_doc_chain()
    chain["trigger"][field] = value

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result, path=f"evidence_chain.trigger.{field}", hint_contains="only trigger.citations"
    )
    assert not _card_path(workspace).exists()


@pytest.mark.parametrize(
    "citations",
    [[], [{"lines": "3-3"}], [{"lines": "2-3"}], [{"lines": "2-2"}, {"lines": "2-2"}]],
)
def test_write_evidence_requires_exactly_the_indexed_trigger_line(
    tmp_path: Path, citations: list[dict[str, str]]
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = chain_with_value(("trigger", "citations"), citations)

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)

    assert_invalid_result(
        result,
        path="evidence_chain.trigger.citations",
        message_contains="indexed human trigger line 2-2",
    )
    assert not _card_path(workspace).exists()


def test_write_evidence_preserves_source_text_and_message_boundaries(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    messages = ("  用户方向\n\nkeep whitespace\t", "synthetic-token=not-a-real-credential", "")
    _replace_source_line(
        workspace,
        line=2,
        record={
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text} for text in messages],
            },
        },
    )
    draft = valid_material_doc_chain()
    submitted = deep_copy_json(draft)

    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=draft)

    assert_appended_result(result, turn_ref="T0001")
    assert load_evidence_card(workspace)["evidence_chains"][0]["trigger"]["quoted_messages"] == [
        {"text": text} for text in messages
    ]
    assert draft == submitted


@pytest.mark.parametrize("source_failure", ["missing", "malformed", "assistant"])
def test_source_failure_leaves_existing_card_unchanged(tmp_path: Path, source_failure: str) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    assert_appended_result(call_write_evidence_api(workspace_path=workspace), turn_ref="T0001")
    before = evidence_card_text(workspace)
    if source_failure == "missing":
        _source_path(workspace).unlink()
    elif source_failure == "malformed":
        lines = _source_path(workspace).read_text(encoding="utf-8").splitlines()
        lines[8] = "{"
        _source_path(workspace).write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        _replace_source_line(
            workspace,
            line=9,
            record={"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        )

    result = call_write_evidence_api(
        workspace_path=workspace, evidence_chain=valid_no_material_chain()
    )

    assert_invalid_result(
        result, path="evidence_chain.trigger.citations", hint_contains="re-prepare"
    )
    assert evidence_card_text(workspace) == before


def test_write_evidence_rejects_legacy_card_without_modifying_it(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    assert_appended_result(call_write_evidence_api(workspace_path=workspace), turn_ref="T0001")
    card = load_evidence_card(workspace)
    card["schema_version"] = 1
    _card_path(workspace).write_text(json.dumps(card), encoding="utf-8")
    before = evidence_card_text(workspace)

    result = call_write_evidence_api(
        workspace_path=workspace, evidence_chain=valid_no_material_chain()
    )

    assert_invalid_result(
        result,
        path="evidence_card",
        message_contains="schema_version 2",
        hint_contains="regenerate",
    )
    assert evidence_card_text(workspace) == before


def test_write_evidence_rejects_malformed_existing_card_without_overwriting_it(
    tmp_path: Path,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    card_path = _card_path(workspace)
    card_path.parent.mkdir(parents=True)
    card_path.write_text("{", encoding="utf-8")

    result = call_write_evidence_api(workspace_path=workspace)

    assert_invalid_result(result, path="evidence_card", hint_contains="regenerate")
    assert evidence_card_text(workspace) == "{"


def test_write_evidence_rejects_unsupported_source_format(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    row = json.loads(index_path.read_text(encoding="utf-8"))
    row["source"] = "unsupported-client"
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    result = call_write_evidence_api(workspace_path=workspace)

    assert_invalid_result(result, path="session_ref", message_contains="source is unsupported")
    assert not _card_path(workspace).exists()


def _source_path(workspace: Path) -> Path:
    return workspace / "projects" / PROJECT_KEY / "sessions" / "codex" / "session-001.jsonl"


def _card_path(workspace: Path) -> Path:
    return workspace / "projects" / PROJECT_KEY / "evidence" / f"{SESSION_REF}.json"


def _replace_source_line(workspace: Path, *, line: int, record: dict[str, Any]) -> None:
    source = _source_path(workspace)
    lines = source.read_text(encoding="utf-8").splitlines()
    lines[line - 1] = json.dumps(record, ensure_ascii=False)
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
