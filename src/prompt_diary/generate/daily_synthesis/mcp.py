"""Transport-independent daily synthesis MCP tool APIs.

Each write tool patches one synthesize slot in the workspace-root ``daily-report.json``: the
per-project ``projects[p].summary``, the top-level ``report_title``, the top-level
``engagement_assessment``, or the top-level ``team_learning``. A deterministic Build step seeds the
file with those slots set to ``null``; the write tools require that skeleton (and the slot they
patch) to exist and only ever replace their own slot, so a re-run is idempotent rather than
additive.

A write is checked before it touches disk: the submission is parsed with the model's chain-only
parsers, then every citation must name a committed (evidence-bearing) turn of its project — a turn
that carries an extracted evidence chain — and is resolved to that turn's line range via the session
index. ``write_project_summary`` additionally rejects a citation whose submitted ``project_key``
disagrees with the tool's project. A rejected write returns structured errors and leaves the report
byte-for-byte unchanged; an accepted write atomic-replaces the file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from prompt_diary.generate.daily_synthesis.citations import CitationResolver
from prompt_diary.generate.daily_synthesis.model import (
    CitationRef,
    DailyReportWriteError,
    EngagementAssessment,
    InvalidDailyReportInput,
    ProjectSummary,
    ReportTitle,
    TeamLearning,
    parse_engagement,
    parse_project_summary,
    parse_report_title,
    parse_team_learning,
)
from prompt_diary.generate.project_synthesis.cards import (
    committed_turn_keys,
    load_committed_chains,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from prompt_diary.generate.daily_synthesis.citations import ResolvedCitation
    from prompt_diary.generate.workspace import PreparedWorkspace

__all__ = [
    "DailyReportInvalidResult",
    "DailyReportWriteError",
    "EngagementWrittenResult",
    "ProjectSummaryWrittenResult",
    "ReportTitleWrittenResult",
    "TeamLearningWrittenResult",
    "WriteEngagementResult",
    "WriteProjectSummaryResult",
    "WriteReportTitleResult",
    "WriteTeamLearningResult",
    "write_engagement",
    "write_project_summary",
    "write_report_title",
    "write_team_learning",
]

_REPORT_NAME = "daily-report.json"


@dataclass(frozen=True)
class ProjectSummaryWrittenResult:
    """Successful per-project summary write result."""

    status: Literal["written"]
    project_key: str


@dataclass(frozen=True)
class ReportTitleWrittenResult:
    """Successful whole-report title write result."""

    status: Literal["written"]


@dataclass(frozen=True)
class EngagementWrittenResult:
    """Successful engagement-assessment write result."""

    status: Literal["written"]


@dataclass(frozen=True)
class TeamLearningWrittenResult:
    """Successful team-learning write result."""

    status: Literal["written"]


@dataclass(frozen=True)
class DailyReportInvalidResult:
    """Rejected daily-report write result holding the structured errors found."""

    status: Literal["invalid"]
    errors: tuple[DailyReportWriteError, ...]


WriteProjectSummaryResult: TypeAlias = ProjectSummaryWrittenResult | DailyReportInvalidResult
WriteReportTitleResult: TypeAlias = ReportTitleWrittenResult | DailyReportInvalidResult
WriteEngagementResult: TypeAlias = EngagementWrittenResult | DailyReportInvalidResult
WriteTeamLearningResult: TypeAlias = TeamLearningWrittenResult | DailyReportInvalidResult


@dataclass
class _CitationScope:
    """Resolve a citation to its line range only when it names a committed turn of its project.

    Scope is the committed (evidence-bearing) turns — those that carry an extracted evidence chain —
    not the raw session index: a turn covered only by an evidence-gap work item carries no evidence
    and cannot ground a synthesized claim. The committed set is read per project and cached.
    """

    resolver: CitationResolver
    workspace_path: Path
    _committed: dict[str, frozenset[tuple[str, str]]] = field(default_factory=dict)

    def resolve(
        self, *, project_key: str, session_ref: str, turn_ref: str
    ) -> ResolvedCitation | None:
        """Return the resolved citation, or ``None`` if the turn is not committed in the project."""
        if (session_ref, turn_ref) not in self._committed_for(project_key):
            return None
        return self.resolver.resolve(
            project_key=project_key, session_ref=session_ref, turn_ref=turn_ref
        )

    def _committed_for(self, project_key: str) -> frozenset[tuple[str, str]]:
        cached = self._committed.get(project_key)
        if cached is None:
            cached = committed_turn_keys(load_committed_chains(self.workspace_path, project_key))
            self._committed[project_key] = cached
        return cached


def write_project_summary(
    *,
    workspace_path: Path,
    project_key: str,
    summary: dict[str, object],
) -> WriteProjectSummaryResult:
    """Validate and patch one project's ``summary`` slot in the daily report.

    The submission is parsed chain-only, then every citation is checked against the tool's
    ``project_key``: a citation that names a different project is rejected rather than silently
    rebound, and a citation that names a turn with no committed evidence in this project is rejected
    as out of scope. The patched slot is a single object, replacing any prior write.
    """
    report = _read_report(workspace_path)
    if report is None:
        return _missing_report()

    parsed = parse_project_summary(cast("dict[str, Any]", summary))
    if isinstance(parsed, InvalidDailyReportInput):
        return DailyReportInvalidResult("invalid", parsed.errors)
    section = parsed.summary

    workspace = load_prepared_workspace(workspace_path)
    if not _has_project(workspace, project_key):
        return _invalid("project_key", _unknown_project_message(project_key), _UNKNOWN_PROJECT_HINT)

    scope = _CitationScope(CitationResolver.from_workspace(workspace), workspace_path)
    resolved, errors = _resolve_project_summary_citations(section, scope, project_key)
    if errors:
        return DailyReportInvalidResult("invalid", tuple(errors))

    project_entry = _find_project_entry(report, project_key)
    if project_entry is None:
        return _invalid("project_key", _project_absent_message(project_key), _PROJECT_ABSENT_HINT)
    project_entry["summary"] = {
        "text": section.text,
        "citations": [citation.to_json() for citation in resolved],
    }
    _write_report(workspace_path, report)
    return ProjectSummaryWrittenResult("written", project_key)


def write_report_title(
    *,
    workspace_path: Path,
    title: dict[str, object],
) -> WriteReportTitleResult:
    """Validate and patch the top-level ``report_title`` slot in the daily report."""
    report = _read_report(workspace_path)
    if report is None:
        return _missing_report()
    if "report_title" not in report:
        return _missing_slot("report_title")

    parsed = parse_report_title(cast("dict[str, Any]", title))
    if isinstance(parsed, InvalidDailyReportInput):
        return DailyReportInvalidResult("invalid", parsed.errors)
    report_title = parsed.title

    scope = _CitationScope(
        CitationResolver.from_workspace(load_prepared_workspace(workspace_path)), workspace_path
    )
    resolved, errors = _resolve_report_title_citations(report_title, scope)
    if errors:
        return DailyReportInvalidResult("invalid", tuple(errors))

    report["report_title"] = {
        "text": report_title.text,
        "citations": [citation.to_json() for citation in resolved],
    }
    _write_report(workspace_path, report)
    return ReportTitleWrittenResult("written")


def write_engagement(
    *,
    workspace_path: Path,
    overall_reading: dict[str, object],
    observations: list[object],
    limits: list[object],
) -> WriteEngagementResult:
    """Validate and patch the top-level ``engagement_assessment`` slot in the daily report.

    Each citation names its project explicitly (cross-project pass) and must name a committed turn
    of that project. The patched slot replaces any prior engagement write.
    """
    report = _read_report(workspace_path)
    if report is None:
        return _missing_report()
    if "engagement_assessment" not in report:
        return _missing_slot("engagement_assessment")

    parsed = parse_engagement(
        overall_reading=overall_reading, observations=observations, limits=limits
    )
    if isinstance(parsed, InvalidDailyReportInput):
        return DailyReportInvalidResult("invalid", parsed.errors)
    assessment = parsed.engagement

    scope = _CitationScope(
        CitationResolver.from_workspace(load_prepared_workspace(workspace_path)), workspace_path
    )
    resolved, errors = _resolve_engagement_citations(assessment, scope)
    if errors:
        return DailyReportInvalidResult("invalid", tuple(errors))

    report["engagement_assessment"] = _engagement_slot(assessment, resolved)
    _write_report(workspace_path, report)
    return EngagementWrittenResult("written")


def write_team_learning(
    *,
    workspace_path: Path,
    takeaways: dict[str, object],
    patterns: list[object],
    limits: list[object],
) -> WriteTeamLearningResult:
    """Validate and patch the top-level ``team_learning`` slot in the daily report.

    Each citation names its project explicitly (cross-project pass) and must name a committed turn
    of that project. The patched slot replaces any prior team-learning write.
    """
    report = _read_report(workspace_path)
    if report is None:
        return _missing_report()
    if "team_learning" not in report:
        return _missing_slot("team_learning")

    parsed = parse_team_learning(takeaways=takeaways, patterns=patterns, limits=limits)
    if isinstance(parsed, InvalidDailyReportInput):
        return DailyReportInvalidResult("invalid", parsed.errors)
    learning = parsed.team_learning

    scope = _CitationScope(
        CitationResolver.from_workspace(load_prepared_workspace(workspace_path)), workspace_path
    )
    resolved, errors = _resolve_team_learning_citations(learning, scope)
    if errors:
        return DailyReportInvalidResult("invalid", tuple(errors))

    report["team_learning"] = _team_learning_slot(learning, resolved)
    _write_report(workspace_path, report)
    return TeamLearningWrittenResult("written")


def _resolve_project_summary_citations(
    summary: ProjectSummary,
    scope: _CitationScope,
    project_key: str,
) -> tuple[list[ResolvedCitation], list[DailyReportWriteError]]:
    resolved: list[ResolvedCitation] = []
    errors: list[DailyReportWriteError] = []
    for index, citation in enumerate(summary.citations):
        path = f"summary.citations[{index}]"
        if citation.project_key is not None and citation.project_key != project_key:
            errors.append(
                DailyReportWriteError(
                    f"{path}.project_key",
                    _project_mismatch_message(citation.project_key, project_key),
                    _PROJECT_MISMATCH_HINT,
                )
            )
            continue
        hit = scope.resolve(
            project_key=project_key,
            session_ref=citation.session_ref,
            turn_ref=citation.turn_ref,
        )
        if hit is None:
            errors.append(
                DailyReportWriteError(path, _uncovered_project_message(citation), _SCOPE_HINT)
            )
            continue
        resolved.append(hit)
    return resolved, errors


def _resolve_report_title_citations(
    title: ReportTitle,
    scope: _CitationScope,
) -> tuple[list[ResolvedCitation], list[DailyReportWriteError]]:
    resolved: list[ResolvedCitation] = []
    errors: list[DailyReportWriteError] = []
    for index, citation in enumerate(title.citations):
        path = f"title.citations[{index}]"
        hit = _resolve_named(citation, scope)
        if hit is None:
            errors.append(
                DailyReportWriteError(path, _uncovered_named_message(citation), _SCOPE_HINT)
            )
            continue
        resolved.append(hit)
    return resolved, errors


def _resolve_engagement_citations(
    assessment: EngagementAssessment,
    scope: _CitationScope,
) -> tuple[dict[str, list[ResolvedCitation]], list[DailyReportWriteError]]:
    resolved: dict[str, list[ResolvedCitation]] = {}
    errors: list[DailyReportWriteError] = []
    for path, citation in _iter_engagement_citations(assessment):
        hit = _resolve_named(citation, scope)
        if hit is None:
            errors.append(
                DailyReportWriteError(path, _uncovered_named_message(citation), _SCOPE_HINT)
            )
            continue
        _stash(resolved, _group_of(path), hit)
    return resolved, errors


def _resolve_team_learning_citations(
    learning: TeamLearning,
    scope: _CitationScope,
) -> tuple[dict[str, list[ResolvedCitation]], list[DailyReportWriteError]]:
    resolved: dict[str, list[ResolvedCitation]] = {}
    errors: list[DailyReportWriteError] = []
    for path, citation in _iter_team_learning_citations(learning):
        hit = _resolve_named(citation, scope)
        if hit is None:
            errors.append(
                DailyReportWriteError(path, _uncovered_named_message(citation), _SCOPE_HINT)
            )
            continue
        _stash(resolved, _group_of(path), hit)
    return resolved, errors


def _iter_engagement_citations(
    assessment: EngagementAssessment,
) -> Iterator[tuple[str, CitationRef]]:
    for index, citation in enumerate(assessment.overall_reading.citations):
        yield f"overall_reading.citations[{index}]", citation
    for obs_index, observation in enumerate(assessment.observations):
        for index, citation in enumerate(observation.citations):
            yield f"observations[{obs_index}].citations[{index}]", citation


def _iter_team_learning_citations(learning: TeamLearning) -> Iterator[tuple[str, CitationRef]]:
    for index, citation in enumerate(learning.takeaways.citations):
        yield f"takeaways.citations[{index}]", citation
    for pattern_index, pattern in enumerate(learning.patterns):
        for index, citation in enumerate(pattern.citations):
            yield f"patterns[{pattern_index}].citations[{index}]", citation


def _resolve_named(citation: CitationRef, scope: _CitationScope) -> ResolvedCitation | None:
    # The parser guarantees a non-None project_key on every cross-project citation; default to the
    # empty string only to satisfy the type checker, which then fails to resolve like any unknown.
    return scope.resolve(
        project_key=citation.project_key or "",
        session_ref=citation.session_ref,
        turn_ref=citation.turn_ref,
    )


def _engagement_slot(
    assessment: EngagementAssessment,
    resolved: dict[str, list[ResolvedCitation]],
) -> dict[str, Any]:
    return {
        "overall_reading": {
            "text": assessment.overall_reading.text,
            "citations": _json_of(resolved.get("overall_reading.citations")),
            "confidence": assessment.overall_reading.confidence,
        },
        "observations": [
            {
                "dimension": observation.dimension,
                "statement": observation.statement,
                "citations": _json_of(resolved.get(f"observations[{index}].citations")),
                "confidence": observation.confidence,
            }
            for index, observation in enumerate(assessment.observations)
        ],
        "limits": list(assessment.limits),
    }


def _team_learning_slot(
    learning: TeamLearning,
    resolved: dict[str, list[ResolvedCitation]],
) -> dict[str, Any]:
    return {
        "takeaways": {
            "text": learning.takeaways.text,
            "citations": _json_of(resolved.get("takeaways.citations")),
            "confidence": learning.takeaways.confidence,
        },
        "patterns": [
            {
                "kind": pattern.kind,
                "statement": pattern.statement,
                "rationale": pattern.rationale,
                "recurrence": pattern.recurrence,
                "citations": _json_of(resolved.get(f"patterns[{index}].citations")),
                "confidence": pattern.confidence,
            }
            for index, pattern in enumerate(learning.patterns)
        ],
        "limits": list(learning.limits),
    }


def _json_of(citations: list[ResolvedCitation] | None) -> list[dict[str, str]]:
    return [citation.to_json() for citation in citations] if citations is not None else []


def _stash(resolved: dict[str, list[ResolvedCitation]], group: str, hit: ResolvedCitation) -> None:
    resolved.setdefault(group, []).append(hit)


def _group_of(path: str) -> str:
    return path.rsplit("[", 1)[0]


def _read_report(workspace_path: Path) -> dict[str, Any] | None:
    path = _report_path(workspace_path)
    if not path.exists():
        return None
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    # A non-object skeleton (corrupted/hand-edited) has no slots to patch and is treated as a
    # missing skeleton so the write is rejected rather than crashing on a non-mapping.
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else None


def _write_report(workspace_path: Path, report: dict[str, Any]) -> None:
    path = _report_path(workspace_path)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _report_path(workspace_path: Path) -> Path:
    return workspace_path / _REPORT_NAME


def _has_project(workspace: PreparedWorkspace, project_key: str) -> bool:
    return any(project.project_key == project_key for project in workspace.projects)


def _find_project_entry(report: dict[str, Any], project_key: str) -> dict[str, Any] | None:
    for entry in _as_list(report.get("projects")):
        mapping = _as_mapping(entry)
        if mapping.get("project_key") == project_key:
            return mapping
    return None


def _missing_report() -> DailyReportInvalidResult:
    return _invalid("daily_report", _MISSING_REPORT_MESSAGE, _MISSING_REPORT_HINT)


def _missing_slot(slot: str) -> DailyReportInvalidResult:
    return _invalid("daily_report", _missing_slot_message(slot), _MISSING_SLOT_HINT)


def _invalid(path: str, message: str, hint: str) -> DailyReportInvalidResult:
    return DailyReportInvalidResult("invalid", (DailyReportWriteError(path, message, hint),))


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r}"


def _project_absent_message(project_key: str) -> str:
    return f"project {project_key!r} is not present in the daily report skeleton"


def _missing_slot_message(slot: str) -> str:
    return f"daily report skeleton is missing the {slot!r} slot"


def _project_mismatch_message(submitted: str, tool_project_key: str) -> str:
    return f"citation names a different project {submitted!r}, not {tool_project_key!r}"


def _uncovered_project_message(citation: CitationRef) -> str:
    return f"{citation.session_ref}/{citation.turn_ref} has no committed evidence in this project"


def _uncovered_named_message(citation: CitationRef) -> str:
    return (
        f"{citation.session_ref}/{citation.turn_ref} has no committed evidence in "
        f"project {citation.project_key!r}"
    )


_MISSING_REPORT_MESSAGE = "daily report skeleton not found"
_MISSING_REPORT_HINT = "the build step must run before synthesis passes write the daily report"
_MISSING_SLOT_HINT = "the build step seeds this slot as null before synthesis passes run"
_UNKNOWN_PROJECT_HINT = "use a project_key from the prepared workspace"
_PROJECT_ABSENT_HINT = "the build step seeds one projects entry per workspace project"
_PROJECT_MISMATCH_HINT = "omit project_key on a per-project pass or name this tool's project"
_SCOPE_HINT = "cite only turns with committed evidence in the named project"
