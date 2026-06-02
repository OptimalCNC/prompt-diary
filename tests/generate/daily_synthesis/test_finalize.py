"""Tests for the deterministic daily-report Finalize step.

Finalize reads the post-pass ``daily-report.json``, rolls the per-claim confidences of the material
work items and the two judgment sections into ``overall_confidence``, validates that a report with
work items has its synthesize slots filled and that every stored citation is well shaped, and on
success writes the finalized report. A missing required slot or a malformed citation is rejected
without writing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tests.support.daily_synthesis import (
    PROJECT_KEY,
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    daily_report_path,
    empty_daily_workspace,
    fill_synthesize_slots,
    finalize_daily_report_via_api,
    finalize_result_to_dict,
    load_daily_report,
    rewrite_envelope_gap_only,
)

if TYPE_CHECKING:
    from pathlib import Path


def _built_and_filled(tmp_path: Path) -> Path:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    return workspace


def _write_report(workspace_path: Path, report: dict[str, Any]) -> None:
    daily_report_path(workspace_path).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


def _citation(turn: str = "T0001", session: str = "S0001") -> dict[str, str]:
    return {"project_key": PROJECT_KEY, "session_ref": session, "turn_ref": turn, "lines": "2-8"}


def _material_item(*, confidence: str, outcome_confidences: list[str]) -> dict[str, Any]:
    return {
        "work_item_ref": "W0001",
        "title": "x",
        "kind": "material_work_item",
        "disposition": "completed",
        "confidence": confidence,
        "covered_turns": [{"session_ref": "S0001", "turn_ref": "T0001"}],
        "trigger_summary": "t",
        "agent_reaction_summary": "r",
        "outcomes": [
            {"what_changed": "o", "confidence": conf, "citations": [_citation()]}
            for conf in outcome_confidences
        ],
        "terminal_states": [{"summary": "s"}],
        "limits": [],
    }


def _report_with_items(work_items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_date": "2026-05-28",
        "status": "final",
        "window": {"start": "a", "end": "b", "timezone": "Asia/Shanghai"},
        "overall_confidence": None,
        "executive_summary": {"top_outcomes": [], "open_items": []},
        "projects": [
            {
                "project_key": PROJECT_KEY,
                "project_label": "ReportGenerator",
                "summary": {"text": "s", "citations": [_citation()]},
                "work_items": work_items,
                "source_user_messages": [],
            }
        ],
        "engagement_assessment": {
            "overall_reading": {"text": "r", "citations": [_citation()], "confidence": "high"},
            "observations": [],
            "limits": [],
        },
        "team_learning": {
            "takeaways": {"text": "t", "citations": [_citation()], "confidence": "high"},
            "patterns": [],
            "limits": [],
        },
    }


# --- overall_confidence roll-up ---------------------------------------------------------------


def test_finalize_fixture_overall_confidence_medium(tmp_path: Path) -> None:
    workspace = _built_and_filled(tmp_path)

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result) == {
        "status": "finalized",
        "overall_confidence": "medium",
    }
    # The finalized confidence is persisted, not just returned.
    assert load_daily_report(workspace)["overall_confidence"] == "medium"


def test_finalize_rounds_to_high(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    # Material high (3) + outcome high (3); engagement/team high (3,3) -> mean 3.0 -> high.
    _write_report(
        workspace,
        _report_with_items([_material_item(confidence="high", outcome_confidences=["high"])]),
    )

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result)["overall_confidence"] == "high"


def test_finalize_high_boundary_at_two_point_five(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    report = _report_with_items([_material_item(confidence="high", outcome_confidences=["high"])])
    report["team_learning"]["takeaways"]["confidence"] = "medium"
    report["engagement_assessment"]["overall_reading"]["confidence"] = "medium"
    # values: wi 3, outcome 3, reading 2, takeaways 2 -> 10/4 = 2.5 -> high (>= 2.5 boundary)
    _write_report(workspace, report)
    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result)["overall_confidence"] == "high"


def test_finalize_rounds_to_low(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    report = _report_with_items([_material_item(confidence="low", outcome_confidences=["low"])])
    report["engagement_assessment"]["overall_reading"]["confidence"] = "low"
    report["team_learning"]["takeaways"]["confidence"] = "low"
    _write_report(workspace, report)
    # values: [1, 1, 1, 1] -> mean 1.0 -> low
    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result)["overall_confidence"] == "low"


def test_finalize_medium_boundary_at_one_point_five(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    report = _report_with_items([_material_item(confidence="medium", outcome_confidences=["low"])])
    report["engagement_assessment"]["overall_reading"]["confidence"] = "low"
    report["team_learning"]["takeaways"]["confidence"] = "medium"
    # values: [2, 1, 1, 2] -> mean 1.5 -> medium (>= 1.5)
    _write_report(workspace, report)
    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result)["overall_confidence"] == "medium"


def test_finalize_ignores_non_material_confidences(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    non_material: dict[str, Any] = {
        "work_item_ref": "W0002",
        "title": "minor",
        "kind": "no_material_work_item",
        "disposition": None,
        "confidence": "high",
        "covered_turns": [],
        "trigger_summary": None,
        "agent_reaction_summary": None,
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
    }
    report = _report_with_items(
        [_material_item(confidence="low", outcome_confidences=["low"]), non_material]
    )
    report["engagement_assessment"]["overall_reading"]["confidence"] = "low"
    report["team_learning"]["takeaways"]["confidence"] = "low"
    # If the non-material high counted, the mean would rise; material-only keeps it low.
    _write_report(workspace, report)

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result)["overall_confidence"] == "low"


def test_finalize_collects_observation_and_pattern_confidences(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    report = _report_with_items([_material_item(confidence="high", outcome_confidences=["high"])])
    report["engagement_assessment"]["observations"] = [
        {
            "dimension": "direction",
            "statement": "x",
            "citations": [_citation()],
            "confidence": "low",
        }
    ]
    report["team_learning"]["patterns"] = [
        {
            "kind": "reuse",
            "statement": "x",
            "rationale": "y",
            "recurrence": "z",
            "citations": [_citation()],
            "confidence": "low",
        }
    ]
    # values: wi 3, outcome 3, reading 3, obs 1, takeaways 3, pattern 1 -> 14/6 = 2.33 -> medium
    _write_report(workspace, report)

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result)["overall_confidence"] == "medium"


# --- validation -------------------------------------------------------------------------------


def test_finalize_rejects_unfilled_slots(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)  # slots still null
    before = daily_report_path(workspace).read_text(encoding="utf-8")

    result = finalize_daily_report_via_api(workspace)

    payload = finalize_result_to_dict(result)
    assert payload["status"] == "invalid"
    paths = [error["path"] for error in payload["errors"]]
    # Finalize uses the project list index in projects[...] paths, not the project_key.
    assert "projects[0].summary" in paths
    assert "engagement_assessment" in paths
    assert "team_learning" in paths
    # A rejected finalize leaves the report unchanged (overall_confidence stays null on disk).
    assert daily_report_path(workspace).read_text(encoding="utf-8") == before


def test_finalize_rejects_missing_engagement_only(tmp_path: Path) -> None:
    workspace = _built_and_filled(tmp_path)
    report = load_daily_report(workspace)
    report["engagement_assessment"] = None

    _write_report(workspace, report)
    result = finalize_daily_report_via_api(workspace)

    payload = finalize_result_to_dict(result)
    assert payload["status"] == "invalid"
    paths = [error["path"] for error in payload["errors"]]
    assert paths == ["engagement_assessment"]


def test_finalize_rejects_missing_team_learning_only(tmp_path: Path) -> None:
    workspace = _built_and_filled(tmp_path)
    report = load_daily_report(workspace)
    report["team_learning"] = None

    _write_report(workspace, report)
    result = finalize_daily_report_via_api(workspace)

    payload = finalize_result_to_dict(result)
    assert payload["status"] == "invalid"
    assert [error["path"] for error in payload["errors"]] == ["team_learning"]


def test_finalize_rejects_malformed_citation(tmp_path: Path) -> None:
    workspace = _built_and_filled(tmp_path)
    report = load_daily_report(workspace)
    # Drop a required key from a stored citation: it is no longer a well-formed resolved citation.
    report["projects"][0]["summary"]["citations"][0].pop("lines")

    _write_report(workspace, report)
    result = finalize_daily_report_via_api(workspace)

    payload = finalize_result_to_dict(result)
    assert payload["status"] == "invalid"
    assert any("citations" in error["path"] for error in payload["errors"])


def test_finalize_accepts_well_formed_report(tmp_path: Path) -> None:
    workspace = _built_and_filled(tmp_path)

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result)["status"] == "finalized"


# --- validation: incomplete synthesized claims ------------------------------------------------


def _invalid_paths(tmp_path: Path, mutate: Any) -> list[str]:
    """Build a valid filled report, apply ``mutate`` to it, finalize, and return the error paths."""
    workspace = empty_daily_workspace(tmp_path)
    report = _report_with_items([_material_item(confidence="high", outcome_confidences=["high"])])
    mutate(report)
    _write_report(workspace, report)

    payload = finalize_result_to_dict(finalize_daily_report_via_api(workspace))
    assert payload["status"] == "invalid"
    return [error["path"] for error in payload["errors"]]


def test_finalize_rejects_summary_with_empty_text(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["projects"][0]["summary"]["text"] = "  "

    assert "projects[0].summary" in _invalid_paths(tmp_path, mutate)


def test_finalize_rejects_summary_with_empty_citations(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["projects"][0]["summary"]["citations"] = []

    assert "projects[0].summary.citations" in _invalid_paths(tmp_path, mutate)


def test_finalize_rejects_overall_reading_with_empty_text(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["engagement_assessment"]["overall_reading"]["text"] = ""

    assert "engagement_assessment.overall_reading" in _invalid_paths(tmp_path, mutate)


def test_finalize_rejects_overall_reading_with_empty_citations(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["engagement_assessment"]["overall_reading"]["citations"] = []

    assert "engagement_assessment.overall_reading.citations" in _invalid_paths(tmp_path, mutate)


def test_finalize_rejects_observation_with_empty_citations(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["engagement_assessment"]["observations"] = [
            {"dimension": "direction", "statement": "x", "citations": [], "confidence": "high"}
        ]

    assert "engagement_assessment.observations[0].citations" in _invalid_paths(tmp_path, mutate)


def test_finalize_rejects_takeaways_with_empty_text(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["team_learning"]["takeaways"]["text"] = ""

    assert "team_learning.takeaways" in _invalid_paths(tmp_path, mutate)


def test_finalize_rejects_pattern_with_empty_citations(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["team_learning"]["patterns"] = [
            {
                "kind": "reuse",
                "statement": "x",
                "rationale": "y",
                "recurrence": "z",
                "citations": [],
                "confidence": "high",
            }
        ]

    assert "team_learning.patterns[0].citations" in _invalid_paths(tmp_path, mutate)


def test_finalize_rejects_exec_top_outcome_without_text_or_citations(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["executive_summary"]["top_outcomes"] = [{"text": "", "citations": []}]

    paths = _invalid_paths(tmp_path, mutate)
    assert "executive_summary.top_outcomes[0]" in paths
    assert "executive_summary.top_outcomes[0].citations" in paths


def test_finalize_rejects_exec_open_item_with_empty_citations(tmp_path: Path) -> None:
    def mutate(report: dict[str, Any]) -> None:
        report["executive_summary"]["open_items"] = [{"text": "still open", "citations": []}]

    assert "executive_summary.open_items[0].citations" in _invalid_paths(tmp_path, mutate)


# --- empty report -----------------------------------------------------------------------------


def test_finalize_empty_report_is_valid_with_null_confidence(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result) == {"status": "finalized", "overall_confidence": None}
    report = load_daily_report(workspace)
    assert report["overall_confidence"] is None
    assert report["engagement_assessment"] is None
    assert report["team_learning"] is None


def test_finalize_empty_report_does_not_require_judgment_slots(tmp_path: Path) -> None:
    # A project with zero work items in an otherwise empty report is not required to have a summary.
    workspace = empty_daily_workspace(tmp_path)
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_date": "2026-05-28",
        "status": "final",
        "window": {"start": "a", "end": "b", "timezone": "Asia/Shanghai"},
        "overall_confidence": None,
        "executive_summary": {"top_outcomes": [], "open_items": []},
        "projects": [
            {
                "project_key": PROJECT_KEY,
                "project_label": "ReportGenerator",
                "summary": None,
                "work_items": [],
                "source_user_messages": [],
            }
        ],
        "engagement_assessment": None,
        "team_learning": None,
    }
    _write_report(workspace, report)

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result) == {"status": "finalized", "overall_confidence": None}


def test_finalize_gap_only_project_does_not_require_summary(tmp_path: Path) -> None:
    # A project covered entirely by an evidence_gap_item has no committed turn to summarize, so
    # Finalize must not require its (null) summary; the gap-only report is a valid, null-judgment
    # finalized state — exactly like an empty report.
    workspace = copy_basic_daily_workspace(tmp_path)
    rewrite_envelope_gap_only(workspace)
    build_daily_report_via_api(workspace)

    result = finalize_daily_report_via_api(workspace)

    assert finalize_result_to_dict(result) == {"status": "finalized", "overall_confidence": None}
    report = load_daily_report(workspace)
    assert report["projects"][0]["summary"] is None
    assert report["engagement_assessment"] is None
    assert report["team_learning"] is None


def test_finalize_recomputes_on_rerun(tmp_path: Path) -> None:
    workspace = _built_and_filled(tmp_path)
    finalize_daily_report_via_api(workspace)

    # Re-running after a slot change recomputes overall_confidence rather than leaving it stale.
    report = load_daily_report(workspace)
    report["engagement_assessment"]["overall_reading"]["confidence"] = "high"
    report["engagement_assessment"]["observations"][0]["confidence"] = "high"
    _write_report(workspace, report)

    result = finalize_daily_report_via_api(workspace)

    # values: wi [3,2], outcomes [3,2], engagement [3,3], team [1,1] -> 18/8 = 2.25 -> medium
    assert finalize_result_to_dict(result)["overall_confidence"] == "medium"
