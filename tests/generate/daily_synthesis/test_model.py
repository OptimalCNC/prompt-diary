"""Tests for daily-report section parsing."""

from __future__ import annotations

from prompt_diary.generate.daily_synthesis.model import (
    DISPOSITIONS,
    InvalidDailyReportInput,
    ParsedEngagement,
    ParsedProjectSummary,
    ParsedTeamLearning,
    derive_disposition,
    parse_engagement,
    parse_project_summary,
    parse_team_learning,
)
from tests.support.daily_synthesis import (
    PROJECT_KEY,
    cross_citation,
    project_citation,
    valid_engagement,
    valid_project_summary,
    valid_team_learning,
)


def _error_paths(result: object) -> list[str]:
    assert isinstance(result, InvalidDailyReportInput)
    return [error.path for error in result.errors]


# --- dispositions scale ----------------------------------------------------------------------


def test_dispositions_match_doc_scale() -> None:
    # The disposition scale pinned by daily-synthesis.md (completed / blocked / interrupted /
    # failed / clarification). Build is its only consumer.
    assert DISPOSITIONS == ("completed", "blocked", "interrupted", "failed", "clarification")


def test_derive_disposition_returns_none_for_non_material() -> None:
    # Minor kinds carry no disposition regardless of their (empty) terminal states.
    assert (
        derive_disposition(
            kind="no_material_work_item", terminal_types=frozenset(), has_outcomes=False
        )
        is None
    )
    assert (
        derive_disposition(kind="evidence_gap_item", terminal_types=frozenset(), has_outcomes=False)
        is None
    )


def test_derive_disposition_failed_wins_over_blocked_and_interrupted() -> None:
    types = frozenset({"failed", "blocked", "interrupted"})
    assert (
        derive_disposition(kind="material_work_item", terminal_types=types, has_outcomes=True)
        == "failed"
    )


def test_derive_disposition_blocked_wins_over_interrupted() -> None:
    types = frozenset({"blocked", "interrupted"})
    assert (
        derive_disposition(kind="material_work_item", terminal_types=types, has_outcomes=False)
        == "blocked"
    )


def test_derive_disposition_interrupted() -> None:
    assert (
        derive_disposition(
            kind="material_work_item", terminal_types=frozenset({"interrupted"}), has_outcomes=False
        )
        == "interrupted"
    )


def test_derive_disposition_completed_via_outcomes() -> None:
    # An outcome with a non-failure terminal still reads as completed.
    assert (
        derive_disposition(
            kind="material_work_item", terminal_types=frozenset({"other"}), has_outcomes=True
        )
        == "completed"
    )


def test_derive_disposition_completed_via_material_result() -> None:
    assert (
        derive_disposition(
            kind="material_work_item",
            terminal_types=frozenset({"material_result"}),
            has_outcomes=False,
        )
        == "completed"
    )


def test_derive_disposition_clarification_is_the_residual() -> None:
    # No failure branch, no completion signal: every remaining material item folds to clarification.
    assert (
        derive_disposition(
            kind="material_work_item",
            terminal_types=frozenset({"clarification_only"}),
            has_outcomes=False,
        )
        == "clarification"
    )
    assert (
        derive_disposition(
            kind="material_work_item", terminal_types=frozenset({"no_material"}), has_outcomes=False
        )
        == "clarification"
    )


# --- project summary -------------------------------------------------------------------------


def test_parse_project_summary_accepts_valid_submission() -> None:
    result = parse_project_summary(valid_project_summary())

    assert isinstance(result, ParsedProjectSummary)
    assert result.summary.text
    assert len(result.summary.citations) == 2
    assert result.summary.citations[0].session_ref == "S0001"
    # Per-project citations carry no project_key (the tool argument supplies it).
    assert result.summary.citations[0].project_key is None


def test_parse_project_summary_rejects_empty_text() -> None:
    summary = valid_project_summary()
    summary["text"] = "  "

    assert "summary.text" in _error_paths(parse_project_summary(summary))


def test_parse_project_summary_rejects_missing_citations() -> None:
    summary = valid_project_summary()
    summary["citations"] = []

    assert "summary.citations" in _error_paths(parse_project_summary(summary))


# --- engagement ------------------------------------------------------------------------------


def test_parse_engagement_accepts_valid_submission() -> None:
    payload = valid_engagement()

    result = parse_engagement(
        overall_reading=payload["overall_reading"],
        observations=payload["observations"],
        limits=payload["limits"],
    )

    assert isinstance(result, ParsedEngagement)
    assert result.engagement.overall_reading.confidence == "medium"
    assert result.engagement.observations[0].dimension == "direction"
    # Cross-project citations must name the project.
    assert result.engagement.overall_reading.citations[0].project_key is not None


def test_parse_engagement_rejects_uncited_overall_reading() -> None:
    payload = valid_engagement()
    payload["overall_reading"]["citations"] = []

    result = parse_engagement(
        overall_reading=payload["overall_reading"],
        observations=payload["observations"],
        limits=payload["limits"],
    )

    assert "overall_reading.citations" in _error_paths(result)


def test_parse_engagement_rejects_citation_without_project_key() -> None:
    payload = valid_engagement()
    payload["overall_reading"]["citations"] = [project_citation("S0001", "T0001")]

    result = parse_engagement(
        overall_reading=payload["overall_reading"],
        observations=payload["observations"],
        limits=payload["limits"],
    )

    assert "overall_reading.citations[0].project_key" in _error_paths(result)


def test_parse_engagement_rejects_unknown_dimension() -> None:
    payload = valid_engagement()
    payload["observations"][0]["dimension"] = "vibes"

    result = parse_engagement(
        overall_reading=payload["overall_reading"],
        observations=payload["observations"],
        limits=payload["limits"],
    )

    assert "observations[0].dimension" in _error_paths(result)


# --- team learning ---------------------------------------------------------------------------


def test_parse_team_learning_accepts_valid_submission() -> None:
    payload = valid_team_learning()

    result = parse_team_learning(
        takeaways=payload["takeaways"],
        patterns=payload["patterns"],
        limits=payload["limits"],
    )

    assert isinstance(result, ParsedTeamLearning)
    assert result.team_learning.patterns[0].kind == "reuse"
    assert result.team_learning.takeaways.confidence == "low"


def test_parse_team_learning_rejects_unknown_kind() -> None:
    payload = valid_team_learning()
    payload["patterns"][0]["kind"] = "celebrate"

    result = parse_team_learning(
        takeaways=payload["takeaways"],
        patterns=payload["patterns"],
        limits=payload["limits"],
    )

    assert "patterns[0].kind" in _error_paths(result)


def test_parse_team_learning_rejects_bad_confidence() -> None:
    payload = valid_team_learning()
    payload["patterns"][0]["confidence"] = "supreme"

    result = parse_team_learning(
        takeaways=payload["takeaways"],
        patterns=payload["patterns"],
        limits=payload["limits"],
    )

    assert "patterns[0].confidence" in _error_paths(result)


def test_parse_engagement_rejects_blank_limit() -> None:
    payload = valid_engagement()
    payload["limits"] = ["  "]

    result = parse_engagement(
        overall_reading=payload["overall_reading"],
        observations=payload["observations"],
        limits=payload["limits"],
    )

    assert "limits[0]" in _error_paths(result)


def test_parse_team_learning_rejects_blank_rationale() -> None:
    payload = valid_team_learning()
    payload["patterns"][0]["rationale"] = "   "

    result = parse_team_learning(
        takeaways=payload["takeaways"],
        patterns=payload["patterns"],
        limits=payload["limits"],
    )

    assert "patterns[0].rationale" in _error_paths(result)


# --- cross-cutting parse behavior ------------------------------------------------------------


def test_parse_project_summary_retains_submitted_project_key() -> None:
    summary = valid_project_summary()
    summary["citations"] = [cross_citation("S0001", "T0001")]

    result = parse_project_summary(summary)

    assert isinstance(result, ParsedProjectSummary)
    # A stray project_key is retained verbatim so the write tool can reject a project mismatch
    # rather than silently binding the citation to the tool's project.
    assert result.summary.citations[0].project_key == PROJECT_KEY


def test_parse_engagement_rejects_bad_observation_confidence() -> None:
    payload = valid_engagement()
    payload["observations"][0]["confidence"] = "supreme"

    result = parse_engagement(
        overall_reading=payload["overall_reading"],
        observations=payload["observations"],
        limits=payload["limits"],
    )

    assert "observations[0].confidence" in _error_paths(result)


def test_parse_engagement_reports_all_errors_in_one_pass() -> None:
    payload = valid_engagement()
    payload["overall_reading"]["citations"] = []
    payload["observations"][0]["dimension"] = "vibes"

    result = parse_engagement(
        overall_reading=payload["overall_reading"],
        observations=payload["observations"],
        limits=payload["limits"],
    )

    paths = _error_paths(result)
    assert "overall_reading.citations" in paths
    assert "observations[0].dimension" in paths
