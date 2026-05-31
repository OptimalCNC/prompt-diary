from __future__ import annotations

from typing import TYPE_CHECKING

from tests.support.project_synthesis import (
    PROJECT_KEY,
    assert_appended_result,
    assert_invalid_result,
    call_write_work_item_api,
    copy_basic_project_workspace,
    deep_copy_json,
    load_project_synthesis,
    project_synthesis_text,
    turn_ref,
    valid_evidence_gap_work_item,
    valid_material_work_item,
    valid_no_material_work_item,
    work_item_with_value,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_first_write_creates_envelope_and_populates_source_user_messages(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=valid_material_work_item()
    )

    assert_appended_result(
        result, work_item_ref="W0001", uncovered=[("S0001", "T0003"), ("S0002", "T0001")]
    )
    envelope = load_project_synthesis(workspace)
    assert envelope["schema_version"] == 1
    assert envelope["project_key"] == PROJECT_KEY
    assert envelope["project_label"] == "ReportGenerator"
    assert [item["work_item_ref"] for item in envelope["work_items"]] == ["W0001"]
    messages = envelope["source_user_messages"]
    assert [(entry["session_ref"], entry["turn_ref"]) for entry in messages] == [
        ("S0001", "T0001"),
        ("S0001", "T0002"),
        ("S0002", "T0001"),
    ]
    assert messages[0]["quoted_messages"][0]["text"].startswith("Please simplify")
    assert messages[0]["quoted_messages"][0]["citations"] == [{"lines": "2-2"}]


def test_appends_second_work_item_without_modifying_first(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    first = deep_copy_json(load_project_synthesis(workspace)["work_items"][0])
    messages_before = deep_copy_json(
        {"m": load_project_synthesis(workspace)["source_user_messages"]}
    )

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=valid_no_material_work_item()
    )

    assert_appended_result(result, work_item_ref="W0002", uncovered=[("S0001", "T0003")])
    envelope = load_project_synthesis(workspace)
    assert [item["work_item_ref"] for item in envelope["work_items"]] == ["W0001", "W0002"]
    assert envelope["work_items"][0] == first
    assert {"m": envelope["source_user_messages"]} == messages_before


def test_full_coverage_returns_empty_uncovered(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    call_write_work_item_api(workspace_path=workspace, work_item=valid_no_material_work_item())

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=valid_evidence_gap_work_item()
    )

    assert_appended_result(result, work_item_ref="W0003", uncovered=[])


def test_rejects_duplicate_work_item_ref(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    before = project_synthesis_text(workspace)
    duplicate = valid_no_material_work_item()
    duplicate["work_item_ref"] = "W0001"

    result = call_write_work_item_api(workspace_path=workspace, work_item=duplicate)

    assert_invalid_result(
        result, path="work_item.work_item_ref", message_contains="W0001", hint_contains="unique"
    )
    assert project_synthesis_text(workspace) == before


def test_rejects_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = call_write_work_item_api(
        workspace_path=workspace,
        project_key="Missing-000000000000",
        work_item=valid_material_work_item(),
    )

    assert_invalid_result(result, path="project_key")


def test_rejects_unknown_covered_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    item = valid_no_material_work_item()
    item["covered_turns"] = [turn_ref("S0009", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=item)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="indexed",
        hint_contains="sessions.index",
    )


def test_rejects_coverage_exclusivity_violation(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    before = project_synthesis_text(workspace)
    clash = valid_no_material_work_item()
    clash["covered_turns"] = [turn_ref("S0001", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=clash)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="already",
        hint_contains="exactly one",
    )
    assert project_synthesis_text(workspace) == before


def test_rejects_evidence_gap_item_covering_committed_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    gap = valid_evidence_gap_work_item()
    gap["covered_turns"] = [turn_ref("S0001", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=gap)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="evidence chain",
        hint_contains="evidence_gap_item",
    )


def test_rejects_non_gap_item_covering_gap_turn(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    item = valid_no_material_work_item()
    item["covered_turns"] = [turn_ref("S0001", "T0003")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=item)

    assert_invalid_result(
        result,
        path="work_item.covered_turns[0]",
        message_contains="no committed evidence chain",
        hint_contains="evidence_gap_item",
    )


def test_rejects_evidence_ref_not_in_covered_turns(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    item = valid_material_work_item()
    item["outcomes"][0]["evidence_refs"] = [turn_ref("S0002", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=item)

    assert_invalid_result(
        result,
        path="work_item.outcomes[0].evidence_refs[0]",
        message_contains="covered",
        hint_contains="covered_turns",
    )


def test_rejects_citing_a_turn_with_no_committed_chain(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    # A non-gap kind that covers the gap turn (itself rejected) and also cites it; the
    # evidence-ref check reports the chain-less turn as un-citable. (An evidence_gap_item
    # cannot reach this path because its narrative fields must be empty.)
    item = valid_no_material_work_item()
    item["covered_turns"] = [turn_ref("S0001", "T0003")]
    item["terminal_states"] = [
        {
            "type": "evidence_gap",
            "summary": "No content was extractable.",
            "evidence_refs": [turn_ref("S0001", "T0003")],
        }
    ]

    result = call_write_work_item_api(workspace_path=workspace, work_item=item)

    assert_invalid_result(
        result,
        path="work_item.terminal_states[0].evidence_refs[0]",
        message_contains="no committed evidence chain",
        hint_contains="cannot be cited",
    )


def test_rejected_write_leaves_envelope_unchanged(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    call_write_work_item_api(workspace_path=workspace, work_item=valid_material_work_item())
    before = project_synthesis_text(workspace)
    bad = valid_no_material_work_item()
    bad["covered_turns"] = [turn_ref("S0001", "T0001")]

    result = call_write_work_item_api(workspace_path=workspace, work_item=bad)

    assert_invalid_result(result, path="work_item.covered_turns[0]")
    assert project_synthesis_text(workspace) == before


def test_rejects_structurally_invalid_without_workspace_checks(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=work_item_with_value(("kind",), "material")
    )

    assert_invalid_result(result, path="work_item.kind")


def test_first_write_regenerates_a_nondict_envelope(tmp_path: Path) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    path = workspace / "projects" / PROJECT_KEY / "project-synthesis.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")  # corrupt, non-object envelope

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=valid_material_work_item()
    )

    assert_appended_result(
        result, work_item_ref="W0001", uncovered=[("S0001", "T0003"), ("S0002", "T0001")]
    )
    envelope = load_project_synthesis(workspace)
    assert envelope["schema_version"] == 1
    assert envelope["project_key"] == PROJECT_KEY
    assert envelope["project_label"] == "ReportGenerator"
    assert [item["work_item_ref"] for item in envelope["work_items"]] == ["W0001"]
    assert len(envelope["source_user_messages"]) == 3
