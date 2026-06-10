"""Core-API tests for the daily-synthesis write tools.

The write tools patch a single ``daily-report.json`` at the workspace root, replacing one of three
synthesize slots (``projects[p].summary``, ``engagement_assessment``, ``team_learning``). They
require the skeleton to already exist, parse the submission with the model's chain-only parsers,
resolve every citation to its indexed-turn line range, and atomic-write the patched report. A
rejected write leaves the file byte-for-byte unchanged.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tests.support.daily_synthesis import (
    PROJECT_KEY,
    assert_engagement_written,
    assert_invalid_result,
    assert_project_summary_written,
    assert_report_title_written,
    assert_team_learning_written,
    call_write_engagement_api,
    call_write_project_summary_api,
    call_write_report_title_api,
    call_write_team_learning_api,
    copy_basic_daily_workspace,
    cross_citation,
    daily_report_path,
    daily_report_text,
    load_daily_report,
    project_citation,
    project_slot,
    seed_daily_report_skeleton,
    valid_engagement,
    valid_project_summary,
    valid_report_title,
    valid_team_learning,
)

if TYPE_CHECKING:
    from pathlib import Path


def _seeded_workspace(tmp_path: Path) -> Path:
    workspace = copy_basic_daily_workspace(tmp_path)
    seed_daily_report_skeleton(workspace)
    return workspace


# --- write_project_summary -------------------------------------------------------------------


def test_write_project_summary_patches_slot_with_resolved_citations(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)

    result = call_write_project_summary_api(workspace_path=workspace)

    assert_project_summary_written(result)
    summary = project_slot(workspace)["summary"]
    assert summary["text"] == valid_project_summary()["text"]
    assert summary["citations"] == [
        {"project_key": PROJECT_KEY, "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"},
        {"project_key": PROJECT_KEY, "session_ref": "S0002", "turn_ref": "T0001", "lines": "2-6"},
    ]


def test_write_project_summary_requires_existing_skeleton(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)  # no skeleton seeded

    result = call_write_project_summary_api(workspace_path=workspace)

    assert_invalid_result(result, path="daily_report")
    assert not daily_report_path(workspace).exists()


def test_write_project_summary_rejects_structural_parse_error(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    summary = valid_project_summary()
    summary["text"] = "  "

    result = call_write_project_summary_api(workspace_path=workspace, summary=summary)

    assert_invalid_result(result, path="summary.text")
    assert daily_report_text(workspace) == before


def test_write_project_summary_rejects_empty_citations(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    summary = valid_project_summary()
    summary["citations"] = []

    result = call_write_project_summary_api(workspace_path=workspace, summary=summary)

    assert_invalid_result(result, path="summary.citations")


def test_write_project_summary_rejects_unknown_project(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)

    result = call_write_project_summary_api(
        workspace_path=workspace, project_key="Missing-000000000000"
    )

    assert_invalid_result(result, path="project_key")
    assert daily_report_text(workspace) == before


def test_write_project_summary_rejects_unresolvable_citation(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    summary = valid_project_summary()
    summary["citations"] = [project_citation("S0001", "T9999")]

    result = call_write_project_summary_api(workspace_path=workspace, summary=summary)

    assert_invalid_result(result, path="summary.citations[0]")


def test_write_project_summary_rejects_citation_project_mismatch(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    summary = valid_project_summary()
    # A submitted project_key that disagrees with the tool's project must be rejected rather than
    # silently rebound to the tool's project.
    summary["citations"] = [cross_citation("S0001", "T0001", project_key="Other-aaaaaaaaaaaa")]

    result = call_write_project_summary_api(workspace_path=workspace, summary=summary)

    assert_invalid_result(result, path="summary.citations[0].project_key")
    assert daily_report_text(workspace) == before


def test_write_project_summary_rejected_write_leaves_report_unchanged(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    summary = valid_project_summary()
    summary["citations"] = [project_citation("S0001", "T9999")]

    call_write_project_summary_api(workspace_path=workspace, summary=summary)

    assert daily_report_text(workspace) == before


def test_write_project_summary_rerun_replaces_slot(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    call_write_project_summary_api(workspace_path=workspace)
    second = valid_project_summary()
    second["text"] = "Rewritten summary after a second synthesize pass."
    second["citations"] = [project_citation("S0001", "T0002")]

    result = call_write_project_summary_api(workspace_path=workspace, summary=second)

    assert_project_summary_written(result)
    summary = project_slot(workspace)["summary"]
    assert isinstance(summary, dict)  # a single object, never a list of accumulated writes
    assert summary["text"] == "Rewritten summary after a second synthesize pass."
    assert summary["citations"] == [
        {"project_key": PROJECT_KEY, "session_ref": "S0001", "turn_ref": "T0002", "lines": "9-12"}
    ]


def test_write_project_summary_rejects_project_absent_from_skeleton(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    # The project exists in the workspace, but the build-seeded skeleton omits its projects entry.
    report = load_daily_report(workspace)
    report["projects"] = []
    daily_report_path(workspace).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    result = call_write_project_summary_api(workspace_path=workspace)

    assert_invalid_result(result, path="project_key")


def test_write_project_summary_rejects_gap_turn_citation(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    summary = valid_project_summary()
    # S0001/T0003 is an indexed turn covered only by an evidence-gap item: it carries no committed
    # evidence and so cannot ground a summary claim, even though it is a "covered" turn.
    summary["citations"] = [project_citation("S0001", "T0003")]

    result = call_write_project_summary_api(workspace_path=workspace, summary=summary)

    assert_invalid_result(result, path="summary.citations[0]")


# --- write_report_title ----------------------------------------------------------------------


def test_write_report_title_patches_slot_with_resolved_citations(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)

    result = call_write_report_title_api(workspace_path=workspace)

    assert_report_title_written(result)
    title = load_daily_report(workspace)["report_title"]
    assert title["text"] == valid_report_title()["text"]
    assert title["citations"] == [
        {"project_key": PROJECT_KEY, "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}
    ]


def test_write_report_title_requires_existing_skeleton(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    result = call_write_report_title_api(workspace_path=workspace)

    assert_invalid_result(result, path="daily_report")
    assert not daily_report_path(workspace).exists()


def test_write_report_title_rejects_structural_parse_error(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    title = valid_report_title()
    title["text"] = "Prompt Diary Report"

    result = call_write_report_title_api(workspace_path=workspace, title=title)

    assert_invalid_result(result, path="title.text")
    assert daily_report_text(workspace) == before


def test_write_report_title_rejects_citation_without_project_key(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    title = valid_report_title()
    title["citations"] = [project_citation("S0001", "T0001")]

    result = call_write_report_title_api(workspace_path=workspace, title=title)

    assert_invalid_result(result, path="title.citations[0].project_key")


def test_write_report_title_rejects_unresolvable_citation(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    title = valid_report_title()
    title["citations"] = [cross_citation("S0001", "T9999")]

    result = call_write_report_title_api(workspace_path=workspace, title=title)

    assert_invalid_result(result, path="title.citations[0]")
    assert daily_report_text(workspace) == before


def test_write_report_title_requires_report_title_slot_in_skeleton(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    report = load_daily_report(workspace)
    del report["report_title"]
    daily_report_path(workspace).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    result = call_write_report_title_api(workspace_path=workspace)

    assert_invalid_result(result, path="daily_report")


def test_write_report_title_rerun_replaces_slot(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    call_write_report_title_api(workspace_path=workspace)
    second = valid_report_title()
    second["text"] = "Second Evidence Headline"
    second["citations"] = [cross_citation("S0002", "T0001")]

    result = call_write_report_title_api(workspace_path=workspace, title=second)

    assert_report_title_written(result)
    title = load_daily_report(workspace)["report_title"]
    assert isinstance(title, dict)
    assert title["text"] == "Second Evidence Headline"
    assert title["citations"][0]["lines"] == "2-6"


# --- write_engagement ------------------------------------------------------------------------


def test_write_engagement_patches_slot_with_resolved_citations(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)

    result = call_write_engagement_api(workspace_path=workspace)

    assert_engagement_written(result)
    engagement = load_daily_report(workspace)["engagement_assessment"]
    reading = engagement["overall_reading"]
    assert reading["text"] == valid_engagement()["overall_reading"]["text"]
    assert reading["confidence"] == "medium"
    assert reading["citations"] == [
        {"project_key": PROJECT_KEY, "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}
    ]
    observation = engagement["observations"][0]
    assert observation["dimension"] == "direction"
    assert observation["citations"][0]["lines"] == "2-8"
    assert engagement["limits"] == valid_engagement()["limits"]


def test_write_engagement_requires_existing_skeleton(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    result = call_write_engagement_api(workspace_path=workspace)

    assert_invalid_result(result, path="daily_report")
    assert not daily_report_path(workspace).exists()


def test_write_engagement_rejects_uncited_overall_reading(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    reading = _reading_with(citations=[])

    result = call_write_engagement_api(workspace_path=workspace, overall_reading=reading)

    assert_invalid_result(result, path="overall_reading.citations")
    assert daily_report_text(workspace) == before


def test_write_engagement_rejects_citation_without_project_key(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    reading = _reading_with(citations=[project_citation("S0001", "T0001")])

    result = call_write_engagement_api(workspace_path=workspace, overall_reading=reading)

    assert_invalid_result(result, path="overall_reading.citations[0].project_key")


def test_write_engagement_rejects_unknown_dimension(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    payload = valid_engagement()
    payload["observations"][0]["dimension"] = "vibes"

    result = call_write_engagement_api(
        workspace_path=workspace, observations=payload["observations"]
    )

    assert_invalid_result(result, path="observations[0].dimension")


def test_write_engagement_rejects_unresolvable_citation(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    reading = _reading_with(citations=[cross_citation("S0001", "T9999")])

    result = call_write_engagement_api(workspace_path=workspace, overall_reading=reading)

    assert_invalid_result(result, path="overall_reading.citations[0]")
    assert daily_report_text(workspace) == before


def test_write_engagement_rerun_replaces_slot(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    call_write_engagement_api(workspace_path=workspace)
    second = _reading_with(
        text="Rewritten engagement reading.",
        citations=[cross_citation("S0002", "T0001")],
    )

    result = call_write_engagement_api(workspace_path=workspace, overall_reading=second)

    assert_engagement_written(result)
    engagement = load_daily_report(workspace)["engagement_assessment"]
    assert isinstance(engagement, dict)
    assert engagement["overall_reading"]["text"] == "Rewritten engagement reading."
    assert engagement["overall_reading"]["citations"][0]["lines"] == "2-6"


def test_write_engagement_requires_engagement_slot_in_skeleton(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    # A partial/corrupted skeleton missing the engagement slot must be rejected, not filled.
    report = load_daily_report(workspace)
    del report["engagement_assessment"]
    daily_report_path(workspace).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    result = call_write_engagement_api(workspace_path=workspace)

    assert_invalid_result(result, path="daily_report")


# --- write_team_learning ---------------------------------------------------------------------


def test_write_team_learning_patches_slot_with_resolved_citations(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)

    result = call_write_team_learning_api(workspace_path=workspace)

    assert_team_learning_written(result)
    learning = load_daily_report(workspace)["team_learning"]
    assert learning["takeaways"]["text"] == valid_team_learning()["takeaways"]["text"]
    assert learning["takeaways"]["citations"] == [
        {"project_key": PROJECT_KEY, "session_ref": "S0002", "turn_ref": "T0001", "lines": "2-6"}
    ]
    pattern = learning["patterns"][0]
    assert pattern["kind"] == "reuse"
    assert pattern["citations"][0]["lines"] == "2-6"
    assert learning["limits"] == valid_team_learning()["limits"]


def test_write_team_learning_requires_existing_skeleton(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)

    result = call_write_team_learning_api(workspace_path=workspace)

    assert_invalid_result(result, path="daily_report")
    assert not daily_report_path(workspace).exists()


def test_write_team_learning_rejects_unknown_kind(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    before = daily_report_text(workspace)
    payload = valid_team_learning()
    payload["patterns"][0]["kind"] = "celebrate"

    result = call_write_team_learning_api(workspace_path=workspace, patterns=payload["patterns"])

    assert_invalid_result(result, path="patterns[0].kind")
    assert daily_report_text(workspace) == before


def test_write_team_learning_rejects_unresolvable_citation(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    payload = valid_team_learning()
    payload["patterns"][0]["citations"] = [cross_citation("S0009", "T0001")]

    result = call_write_team_learning_api(workspace_path=workspace, patterns=payload["patterns"])

    assert_invalid_result(result, path="patterns[0].citations[0]")


def test_write_team_learning_rerun_replaces_slot(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    call_write_team_learning_api(workspace_path=workspace)
    second = valid_team_learning()
    second["takeaways"]["text"] = "Rewritten team-learning takeaways."
    second["takeaways"]["citations"] = [cross_citation("S0001", "T0001")]

    result = call_write_team_learning_api(workspace_path=workspace, takeaways=second["takeaways"])

    assert_team_learning_written(result)
    learning = load_daily_report(workspace)["team_learning"]
    assert isinstance(learning, dict)
    assert learning["takeaways"]["text"] == "Rewritten team-learning takeaways."
    assert learning["takeaways"]["citations"][0]["lines"] == "2-8"


def test_write_team_learning_requires_team_learning_slot_in_skeleton(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path)
    report = load_daily_report(workspace)
    del report["team_learning"]
    daily_report_path(workspace).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    result = call_write_team_learning_api(workspace_path=workspace)

    assert_invalid_result(result, path="daily_report")


def _reading_with(
    *,
    text: str = "The user framed concrete goals and approved results.",
    citations: list[Any] | None = None,
    confidence: str = "medium",
) -> dict[str, Any]:
    return {
        "text": text,
        "citations": [cross_citation("S0001", "T0001")] if citations is None else citations,
        "confidence": confidence,
    }
