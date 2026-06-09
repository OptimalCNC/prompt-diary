"""Tests for the deterministic daily-report Build step.

Build assembles every deterministic field of ``daily-report.json`` from the prepared workspace and
the per-project ``project-synthesis.json`` envelopes — the header and all of Work by Project except
the per-project ``summary`` — and seeds the three synthesize slots (per-project ``summary``,
``engagement_assessment``, ``team_learning``) as ``null``. It writes the skeleton to the workspace
root and returns it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from prompt_diary.errors import PromptDiaryError
from tests.support.daily_synthesis import (
    PROJECT_KEY,
    PROJECT_LABEL,
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    copy_corrupt_daily_workspace,
    copy_dispositions_daily_workspace,
    copy_exec_uncited_daily_workspace,
    empty_daily_workspace,
    load_daily_report,
)

if TYPE_CHECKING:
    from pathlib import Path

_W0001_OUTCOME = "Top-level turn_ref adopted; chain_ref removed from the evidence surface."


def _build(tmp_path: Path) -> dict[str, Any]:
    workspace = copy_basic_daily_workspace(tmp_path)
    return build_daily_report_via_api(workspace)


def _project(report: dict[str, Any]) -> dict[str, Any]:
    projects = report["projects"]
    assert len(projects) == 1
    return projects[0]


def _work_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    return _project(report)["work_items"]


def _by_ref(report: dict[str, Any], ref: str) -> dict[str, Any]:
    for item in _work_items(report):
        if item["work_item_ref"] == ref:
            return item
    pytest.fail(f"no work item {ref!r}")


def _envelope_path(workspace: Path) -> Path:
    return workspace / "projects" / PROJECT_KEY / "project-synthesis.json"


def _load_envelope(workspace: Path) -> dict[str, Any]:
    return json.loads(_envelope_path(workspace).read_text(encoding="utf-8"))


def _write_envelope(workspace: Path, envelope: dict[str, Any]) -> None:
    _envelope_path(workspace).write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")


# --- header -----------------------------------------------------------------------------------


def test_build_header_lifts_metadata(tmp_path: Path) -> None:
    report = _build(tmp_path)

    assert report["schema_version"] == 1
    assert report["report_date"] == "2026-05-28"
    assert report["status"] == "final"
    assert report["window"] == {
        "start": "2026-05-28T00:00:00+08:00",
        "end": "2026-05-29T00:00:00+08:00",
        "timezone": "Asia/Shanghai",
    }
    assert report["overall_confidence"] is None


def test_build_seeds_synthesize_slots_null(tmp_path: Path) -> None:
    report = _build(tmp_path)

    assert _project(report)["summary"] is None
    assert report["engagement_assessment"] is None
    assert report["team_learning"] is None


def test_build_writes_rereadable_report(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    returned = build_daily_report_via_api(workspace)

    assert load_daily_report(workspace) == returned


# --- project + work-item shape ----------------------------------------------------------------


def test_build_project_label_and_key(tmp_path: Path) -> None:
    project = _project(_build(tmp_path))

    assert project["project_key"] == PROJECT_KEY
    assert project["project_label"] == PROJECT_LABEL


def test_build_work_item_order(tmp_path: Path) -> None:
    refs = [item["work_item_ref"] for item in _work_items(_build(tmp_path))]

    assert refs == ["W0001", "W0002", "W0003", "W0004"]


def test_build_w0001_full_view(tmp_path: Path) -> None:
    item = _by_ref(_build(tmp_path), "W0001")

    assert item["kind"] == "material_work_item"
    assert item["disposition"] == "completed"
    assert item["confidence"] == "high"
    assert item["title"] == "Simplify the MCP evidence tools and drop chain_ref"
    assert item["covered_turns"] == [{"session_ref": "S0001", "turn_ref": "T0001"}]
    assert item["trigger_summary"] == (
        "User asked to simplify the MCP evidence tools and remove chain_ref."
    )
    assert item["agent_reaction_summary"] == (
        "Updated the MCP tools page, evidence contract, and extractor prompt to a "
        "top-level turn_ref identity."
    )
    assert item["outcomes"] == [
        {
            "what_changed": _W0001_OUTCOME,
            "confidence": "high",
            "citations": [
                {
                    "project_key": PROJECT_KEY,
                    "session_ref": "S0001",
                    "turn_ref": "T0001",
                    "lines": "2-8",
                }
            ],
        }
    ]
    # A no-outcome material item shows its terminal summary as the visible claim, so each terminal
    # state carries its own resolved citations (like an outcome) rather than rendering uncited.
    assert item["terminal_states"] == [
        {
            "summary": "Extraction surface updated to turn_ref identity.",
            "citations": [
                {
                    "project_key": PROJECT_KEY,
                    "session_ref": "S0001",
                    "turn_ref": "T0001",
                    "lines": "2-8",
                }
            ],
        }
    ]
    assert item["limits"] == ["Prompt-test suite not confirmed green within these turns."]


def test_build_w0002_outcome_citation_lines(tmp_path: Path) -> None:
    item = _by_ref(_build(tmp_path), "W0002")

    assert item["kind"] == "material_work_item"
    assert item["disposition"] == "completed"
    assert item["confidence"] == "medium"
    assert item["outcomes"][0]["citations"] == [
        {"project_key": PROJECT_KEY, "session_ref": "S0002", "turn_ref": "T0001", "lines": "2-6"}
    ]


def test_build_w0003_minor_item(tmp_path: Path) -> None:
    item = _by_ref(_build(tmp_path), "W0003")

    assert item["kind"] == "no_material_work_item"
    assert item["disposition"] is None
    assert item["confidence"] == "low"
    assert item["trigger_summary"] is None
    assert item["agent_reaction_summary"] is None
    assert item["outcomes"] == []
    assert item["terminal_states"] == []


def test_build_w0004_evidence_gap_item(tmp_path: Path) -> None:
    item = _by_ref(_build(tmp_path), "W0004")

    assert item["kind"] == "evidence_gap_item"
    assert item["disposition"] is None


def test_build_source_user_messages_lifted_verbatim(tmp_path: Path) -> None:
    project = _project(_build(tmp_path))

    assert project["source_user_messages"] == [
        {
            "session_ref": "S0001",
            "turn_ref": "T0001",
            "messages": ["Please simplify the MCP evidence tools and drop chain_ref."],
        },
        {
            "session_ref": "S0001",
            "turn_ref": "T0002",
            "messages": ["Is that placeholder misleading?"],
        },
        {
            "session_ref": "S0002",
            "turn_ref": "T0001",
            "messages": ["Design the QA approach for evidence extraction."],
        },
    ]


def test_build_omits_executive_summary(tmp_path: Path) -> None:
    assert "executive_summary" not in _build(tmp_path)


def test_build_keeps_all_work_item_outcomes(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    envelope = _load_envelope(workspace)
    first_item = envelope["work_items"][0]
    first_item["outcomes"] = [
        {
            "category": "document_outcome",
            "summary": f"Headline outcome {index}.",
            "evidence_refs": [{"session_ref": "S0001", "turn_ref": "T0001"}],
            "confidence": "high",
        }
        for index in range(1, 8)
    ]
    _write_envelope(workspace, envelope)

    report = build_daily_report_via_api(workspace)

    assert "executive_summary" not in report
    assert [entry["what_changed"] for entry in _by_ref(report, "W0001")["outcomes"]] == [
        "Headline outcome 1.",
        "Headline outcome 2.",
        "Headline outcome 3.",
        "Headline outcome 4.",
        "Headline outcome 5.",
        "Headline outcome 6.",
        "Headline outcome 7.",
    ]


# --- disposition derivation -------------------------------------------------------------------


def _disposition_report(tmp_path: Path) -> dict[str, Any]:
    workspace = copy_dispositions_daily_workspace(tmp_path)
    return build_daily_report_via_api(workspace)


def test_build_disposition_per_branch(tmp_path: Path) -> None:
    report = _disposition_report(tmp_path)
    by_ref = {item["work_item_ref"]: item["disposition"] for item in _work_items(report)}

    assert by_ref == {
        "W0001": "failed",
        "W0002": "blocked",
        "W0003": "interrupted",
        "W0004": "clarification",
        "W0005": "completed",
        "W0006": "completed",
        "W0007": "clarification",
        "W0008": "failed",
    }


def test_build_work_item_significance_order(tmp_path: Path) -> None:
    # Material items sort by confidence desc then work_item_ref asc.
    refs = [item["work_item_ref"] for item in _work_items(_disposition_report(tmp_path))]

    assert refs == ["W0002", "W0005", "W0008", "W0001", "W0004", "W0007", "W0003", "W0006"]


def test_build_interrupted_terminal_still_stays_with_work_item(tmp_path: Path) -> None:
    report = _disposition_report(tmp_path)

    assert "executive_summary" not in report
    assert _by_ref(report, "W0003")["terminal_states"][0]["summary"] == (
        "W0003 interrupted terminal."
    )


# --- uncited entries --------------------------------------------------------------------------


def test_build_keeps_uncited_work_item_entries(tmp_path: Path) -> None:
    # The lone work item's outcome and failed terminal carry no evidence_refs, so their resolved
    # citations are empty. They still remain in Work by Project.
    workspace = copy_exec_uncited_daily_workspace(tmp_path)

    report = build_daily_report_via_api(workspace)

    assert "executive_summary" not in report
    item = _by_ref(report, "W0001")
    assert item["disposition"] == "failed"
    assert item["outcomes"] == [
        {"what_changed": "Uncited outcome.", "confidence": "high", "citations": []}
    ]


# --- empty workspace --------------------------------------------------------------------------


def test_build_empty_workspace_has_no_projects(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)

    report = build_daily_report_via_api(workspace)

    assert report["projects"] == []
    assert "executive_summary" not in report
    assert report["engagement_assessment"] is None
    assert report["team_learning"] is None
    assert report["overall_confidence"] is None
    assert load_daily_report(workspace) == report


# --- corrupt envelope -------------------------------------------------------------------------


def test_build_raises_on_structurally_invalid_work_item(tmp_path: Path) -> None:
    # The envelope is written by the validated write tool; a work item that no longer parses is
    # post-synthesis corruption. Build fails loudly (naming the project and ref) rather than
    # silently dropping the work item from the deterministic report.
    workspace = copy_corrupt_daily_workspace(tmp_path)

    with pytest.raises(PromptDiaryError) as excinfo:
        build_daily_report_via_api(workspace)

    message = str(excinfo.value)
    assert PROJECT_KEY in message
    assert "W0001" in message
