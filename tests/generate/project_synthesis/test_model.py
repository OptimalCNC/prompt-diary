from __future__ import annotations

from typing import Any

import pytest

from prompt_diary.generate.project_synthesis.model import (
    InvalidWorkItem,
    ParsedWorkItem,
    TurnReference,
    WorkItemDisposition,
    new_project_synthesis_envelope,
    parse_work_item,
    work_item_to_json,
)
from tests.support.project_synthesis import (
    PROJECT_KEY,
    PROJECT_LABEL,
    valid_evidence_gap_work_item,
    valid_excluded_work_item,
    valid_material_work_item,
    valid_no_material_work_item,
    work_item_with_value,
)


def _errors(result: object) -> list[Any]:
    assert isinstance(result, InvalidWorkItem)
    return [error.path for error in result.errors]


@pytest.mark.parametrize(
    "factory",
    [
        valid_material_work_item,
        valid_no_material_work_item,
        valid_evidence_gap_work_item,
        valid_excluded_work_item,
    ],
)
def test_parse_accepts_every_valid_kind(factory: Any) -> None:
    result = parse_work_item(factory())
    assert isinstance(result, ParsedWorkItem)


def test_parse_typed_fields_round_trip() -> None:
    result = parse_work_item(valid_material_work_item())
    assert isinstance(result, ParsedWorkItem)
    item = result.work_item
    assert item.work_item_ref == "W0001"
    assert item.kind == "material_work_item"
    assert item.covered_turns == (
        TurnReference("S0001", "T0001"),
        TurnReference("S0001", "T0002"),
    )
    assert item.trigger is not None
    assert item.trigger.evidence_refs == (TurnReference("S0001", "T0001"),)
    assert item.outcomes[0].category == "document_outcome"
    assert item.terminal_states[0].type == "material_result"
    assert item.disposition == WorkItemDisposition(
        type="completed",
        scope="Finalize and commit the evidence-extraction contract.",
        summary="The requested contract revision was frozen in a checkpoint commit.",
        evidence_refs=(TurnReference("S0001", "T0001"), TurnReference("S0001", "T0002")),
    )
    assert work_item_to_json(item) == valid_material_work_item()


def test_work_item_to_json_is_canonical_and_omits_absent_blocks() -> None:
    parsed = parse_work_item(valid_no_material_work_item())
    assert isinstance(parsed, ParsedWorkItem)
    payload = work_item_to_json(parsed.work_item)
    assert payload["work_item_ref"] == "W0002"
    assert payload["covered_turns"] == [{"session_ref": "S0002", "turn_ref": "T0001"}]
    assert "trigger" not in payload
    assert "agent_reaction" not in payload
    assert "reason" not in payload
    assert "disposition" not in payload
    assert payload["outcomes"] == []


def test_excluded_to_json_includes_reason() -> None:
    parsed = parse_work_item(valid_excluded_work_item())
    assert isinstance(parsed, ParsedWorkItem)
    payload = work_item_to_json(parsed.work_item)
    assert payload["reason"].startswith("Duplicate")


@pytest.mark.parametrize(
    ("path", "value", "error_path"),
    [
        (("work_item_ref",), "WX", "work_item.work_item_ref"),
        (("work_item_ref",), "1", "work_item.work_item_ref"),
        (("kind",), "material", "work_item.kind"),
        (("title",), "  ", "work_item.title"),
        (("confidence",), "definitely", "work_item.confidence"),
        (("outcomes", 0, "category"), "documentation", "work_item.outcomes[0].category"),
        (("outcomes", 0, "confidence"), "huge", "work_item.outcomes[0].confidence"),
        (("terminal_states", 0, "type"), "done", "work_item.terminal_states[0].type"),
        (("disposition", "type"), "material_result", "work_item.disposition.type"),
        (("disposition", "scope"), "  ", "work_item.disposition.scope"),
        (("disposition", "summary"), "  ", "work_item.disposition.summary"),
        (("disposition", "evidence_refs"), [], "work_item.disposition.evidence_refs"),
        (("disposition",), "completed", "work_item.disposition.type"),
        (("covered_turns", 0, "turn_ref"), "  ", "work_item.covered_turns[0].turn_ref"),
        (("covered_turns", 0, "session_ref"), "  ", "work_item.covered_turns[0].session_ref"),
        (("covered_turns",), [], "work_item.covered_turns"),
        (("agent_reaction", "main_actions"), ["  "], "work_item.agent_reaction.main_actions[0]"),
    ],
)
def test_parse_rejects_structural_violations(
    path: tuple[Any, ...], value: Any, error_path: str
) -> None:
    assert error_path in _errors(parse_work_item(work_item_with_value(path, value)))


def test_material_requires_trigger_reaction_result_and_disposition() -> None:
    item = valid_material_work_item()
    del item["trigger"]
    del item["agent_reaction"]
    del item["disposition"]
    item["outcomes"] = []
    item["terminal_states"] = []
    paths = _errors(parse_work_item(item))
    assert "work_item.trigger" in paths
    assert "work_item.agent_reaction" in paths
    assert "work_item.outcomes" in paths
    assert "work_item.disposition" in paths


@pytest.mark.parametrize(
    "disposition",
    ["completed", "in_progress", "blocked", "interrupted", "failed", "cancelled", "unknown"],
)
def test_parse_accepts_task_dispositions_independently_of_historical_states(
    disposition: str,
) -> None:
    item = work_item_with_value(("disposition", "type"), disposition)
    item["terminal_states"][0]["type"] = "interrupted"

    parsed = parse_work_item(item)

    assert isinstance(parsed, ParsedWorkItem)
    assert parsed.work_item.disposition is not None
    assert parsed.work_item.disposition.type == disposition


@pytest.mark.parametrize(
    "factory",
    [valid_no_material_work_item, valid_evidence_gap_work_item, valid_excluded_work_item],
)
def test_non_material_items_allow_null_disposition_and_reject_task_judgment(factory: Any) -> None:
    item = factory()
    item["disposition"] = None
    parsed = parse_work_item(item)
    assert isinstance(parsed, ParsedWorkItem)
    assert parsed.work_item.disposition is None

    item["disposition"] = valid_material_work_item()["disposition"]
    assert "work_item.disposition" in _errors(parse_work_item(item))


def test_excluded_requires_reason() -> None:
    item = valid_excluded_work_item()
    del item["reason"]
    assert "work_item.reason" in _errors(parse_work_item(item))


def test_new_envelope_skeleton() -> None:
    envelope = new_project_synthesis_envelope(PROJECT_KEY, PROJECT_LABEL)
    assert envelope == {
        "schema_version": 2,
        "project_key": PROJECT_KEY,
        "project_label": PROJECT_LABEL,
        "work_items": [],
        "source_user_messages": [],
    }


@pytest.mark.parametrize("kind", ["evidence_gap_item", "excluded_with_reason"])
def test_gap_and_excluded_forbid_narrative_fields(kind: str) -> None:
    item = valid_material_work_item()
    item["work_item_ref"] = "W0007"
    item["kind"] = kind
    if kind == "excluded_with_reason":
        item["reason"] = "Duplicate of W0001."

    paths = _errors(parse_work_item(item))

    assert "work_item.trigger" in paths
    assert "work_item.agent_reaction" in paths
    assert "work_item.outcomes" in paths
    assert "work_item.terminal_states" in paths
