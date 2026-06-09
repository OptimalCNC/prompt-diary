"""Typed daily-report sections and self-contained parsing for daily synthesis.

This module owns the daily report's synthesized sections — the per-project summary, the engagement
assessment, and the team-learning analysis — and the chain-only validation that depends on nothing
but the submitted object. Parsing an untrusted submission either yields a fully typed section whose
values are well formed, or a structured list of ``DailyReportWriteError``.

Citations are parsed as unresolved ``CitationRef`` values. Resolving a ref to its indexed-turn line
range, and checking it against the prepared workspace and the pass's allowed scope, live in the
daily synthesis MCP API and in :mod:`prompt_diary.generate.daily_synthesis.citations`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias, cast

from prompt_diary.generate.prompts import ENGAGEMENT_DIMENSIONS, TEAM_LEARNING_PATTERN_KINDS

_CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
_DIMENSIONS = frozenset(item.value for item in ENGAGEMENT_DIMENSIONS)
_PATTERN_KINDS = frozenset(item.value for item in TEAM_LEARNING_PATTERN_KINDS)

# The work-item disposition scale (daily-synthesis.md). A material work item carries exactly one of
# these, derived deterministically by the Build step from its terminal states and outcomes; minor
# kinds carry no disposition. Ordered most-to-least severe, matching the derivation precedence.
DISPOSITIONS: tuple[str, ...] = ("completed", "blocked", "interrupted", "failed", "clarification")

# The confidence band ranking shared by Build's significance sort and Finalize's roll-up.
CONFIDENCE_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}

# The work-item kinds that constitute "reportable work": the two kinds project synthesis guarantees
# cover a committed, citable turn. A project whose work items are all gap/excluded kinds
# (``evidence_gap_item`` / ``excluded_with_reason``) has no citable turn, so it gets no summary pass
# and is not required to carry one; a report with no reportable work item anywhere leaves every
# judgment slot null. The runner's pass gating and Finalize's required-slot checks share this set.
REPORTABLE_WORK_ITEM_KINDS: frozenset[str] = frozenset(
    {"material_work_item", "no_material_work_item"}
)

_MATERIAL_WORK_ITEM = "material_work_item"


def derive_disposition(
    *, kind: str, terminal_types: frozenset[str], has_outcomes: bool
) -> str | None:
    """Derive a material work item's disposition from its terminal states and outcomes.

    Returns a member of :data:`DISPOSITIONS`, or ``None`` for a non-material work item. The
    precedence is most-to-least severe — a failed/blocked/interrupted branch wins over a completion,
    a completion (any outcome or a ``material_result`` terminal) wins over a bare clarification, and
    every remaining material work item folds into ``clarification`` as the residual disposition.
    """
    if kind != _MATERIAL_WORK_ITEM:
        return None
    if "failed" in terminal_types:
        return "failed"
    if "blocked" in terminal_types:
        return "blocked"
    if "interrupted" in terminal_types:
        return "interrupted"
    if has_outcomes or "material_result" in terminal_types:
        return "completed"
    return "clarification"


@dataclass(frozen=True)
class DailyReportWriteError:
    """Structured validation error returned by a rejected daily-report write."""

    path: str
    message: str
    hint: str


@dataclass(frozen=True)
class CitationRef:
    """An unresolved citation: a reference to one indexed turn.

    ``project_key`` is ``None`` for a per-project pass (its project is the tool argument) and set
    for a cross-project pass that must name the project explicitly, because session refs repeat
    across projects.
    """

    session_ref: str
    turn_ref: str
    project_key: str | None = None


@dataclass(frozen=True)
class CitedText:
    """Synthesized prose grounded by citations and hedged by a confidence value."""

    text: str
    citations: tuple[CitationRef, ...]
    confidence: str


@dataclass(frozen=True)
class ProjectSummary:
    """A per-project qualitative summary; its confidence is implicit in its work items."""

    text: str
    citations: tuple[CitationRef, ...]


@dataclass(frozen=True)
class Observation:
    """One engagement observation along a single dimension."""

    dimension: str
    statement: str
    citations: tuple[CitationRef, ...]
    confidence: str


@dataclass(frozen=True)
class EngagementAssessment:
    """The whole-report engagement reading."""

    overall_reading: CitedText
    observations: tuple[Observation, ...]
    limits: tuple[str, ...]


@dataclass(frozen=True)
class Pattern:
    """One team-learning pattern to promote, avoid, or reuse."""

    kind: str
    statement: str
    rationale: str
    recurrence: str
    citations: tuple[CitationRef, ...]
    confidence: str


@dataclass(frozen=True)
class TeamLearning:
    """The whole-report team-learning analysis."""

    takeaways: CitedText
    patterns: tuple[Pattern, ...]
    limits: tuple[str, ...]


@dataclass(frozen=True)
class ParsedProjectSummary:
    """A successful parse of a per-project summary submission."""

    summary: ProjectSummary


@dataclass(frozen=True)
class ParsedEngagement:
    """A successful parse of an engagement submission."""

    engagement: EngagementAssessment


@dataclass(frozen=True)
class ParsedTeamLearning:
    """A successful parse of a team-learning submission."""

    team_learning: TeamLearning


@dataclass(frozen=True)
class InvalidDailyReportInput:
    """A rejected parse holding the structural errors found in the submission."""

    errors: tuple[DailyReportWriteError, ...]


ProjectSummaryParseResult: TypeAlias = ParsedProjectSummary | InvalidDailyReportInput
EngagementParseResult: TypeAlias = ParsedEngagement | InvalidDailyReportInput
TeamLearningParseResult: TypeAlias = ParsedTeamLearning | InvalidDailyReportInput


def parse_project_summary(summary: dict[str, Any]) -> ProjectSummaryParseResult:
    """Parse a per-project ``summary`` submission into a typed section or structured errors."""
    errors: list[DailyReportWriteError] = []
    parsed = _parse_project_summary(summary, errors)
    if errors:
        return InvalidDailyReportInput(tuple(errors))
    return ParsedProjectSummary(parsed)


def parse_engagement(
    *, overall_reading: Any, observations: Any, limits: Any
) -> EngagementParseResult:
    """Parse an engagement submission into a typed section or structured errors."""
    errors: list[DailyReportWriteError] = []
    reading = _parse_cited_text(overall_reading, errors, path="overall_reading")
    parsed_observations = tuple(
        _parse_observation(item, errors, path=f"observations[{index}]")
        for index, item in enumerate(_as_list(observations))
    )
    parsed_limits = _parse_str_list(limits, errors, path="limits")
    if errors:
        return InvalidDailyReportInput(tuple(errors))
    return ParsedEngagement(EngagementAssessment(reading, parsed_observations, parsed_limits))


def parse_team_learning(*, takeaways: Any, patterns: Any, limits: Any) -> TeamLearningParseResult:
    """Parse a team-learning submission into a typed section or structured errors."""
    errors: list[DailyReportWriteError] = []
    parsed_takeaways = _parse_cited_text(takeaways, errors, path="takeaways")
    parsed_patterns = tuple(
        _parse_pattern(item, errors, path=f"patterns[{index}]")
        for index, item in enumerate(_as_list(patterns))
    )
    parsed_limits = _parse_str_list(limits, errors, path="limits")
    if errors:
        return InvalidDailyReportInput(tuple(errors))
    return ParsedTeamLearning(TeamLearning(parsed_takeaways, parsed_patterns, parsed_limits))


def _parse_project_summary(
    raw: dict[str, Any], errors: list[DailyReportWriteError]
) -> ProjectSummary:
    mapping = _as_mapping(raw)
    return ProjectSummary(
        text=_parse_nonempty(mapping.get("text"), errors, path="summary.text"),
        citations=_parse_citations(
            mapping.get("citations"), errors, path="summary.citations", require_project_key=False
        ),
    )


def _parse_cited_text(raw: object, errors: list[DailyReportWriteError], *, path: str) -> CitedText:
    mapping = _as_mapping(raw)
    return CitedText(
        text=_parse_nonempty(mapping.get("text"), errors, path=f"{path}.text"),
        citations=_parse_citations(
            mapping.get("citations"), errors, path=f"{path}.citations", require_project_key=True
        ),
        confidence=_parse_enum(
            mapping.get("confidence"),
            _CONFIDENCE_VALUES,
            errors,
            path=f"{path}.confidence",
            controlled="confidence",
        ),
    )


def _parse_observation(
    raw: object, errors: list[DailyReportWriteError], *, path: str
) -> Observation:
    mapping = _as_mapping(raw)
    return Observation(
        dimension=_parse_enum(
            mapping.get("dimension"),
            _DIMENSIONS,
            errors,
            path=f"{path}.dimension",
            controlled="engagement dimension",
        ),
        statement=_parse_nonempty(mapping.get("statement"), errors, path=f"{path}.statement"),
        citations=_parse_citations(
            mapping.get("citations"), errors, path=f"{path}.citations", require_project_key=True
        ),
        confidence=_parse_enum(
            mapping.get("confidence"),
            _CONFIDENCE_VALUES,
            errors,
            path=f"{path}.confidence",
            controlled="confidence",
        ),
    )


def _parse_pattern(raw: object, errors: list[DailyReportWriteError], *, path: str) -> Pattern:
    mapping = _as_mapping(raw)
    return Pattern(
        kind=_parse_enum(
            mapping.get("kind"),
            _PATTERN_KINDS,
            errors,
            path=f"{path}.kind",
            controlled="team-learning pattern kind",
        ),
        statement=_parse_nonempty(mapping.get("statement"), errors, path=f"{path}.statement"),
        rationale=_parse_nonempty(mapping.get("rationale"), errors, path=f"{path}.rationale"),
        recurrence=_parse_nonempty(mapping.get("recurrence"), errors, path=f"{path}.recurrence"),
        citations=_parse_citations(
            mapping.get("citations"), errors, path=f"{path}.citations", require_project_key=True
        ),
        confidence=_parse_enum(
            mapping.get("confidence"),
            _CONFIDENCE_VALUES,
            errors,
            path=f"{path}.confidence",
            controlled="confidence",
        ),
    )


def _parse_citations(
    value: object,
    errors: list[DailyReportWriteError],
    *,
    path: str,
    require_project_key: bool,
) -> tuple[CitationRef, ...]:
    items = _as_list(value)
    if not items:
        errors.append(DailyReportWriteError(path, _empty_citations_message(path), _CITATION_HINT))
    return tuple(
        _parse_citation_ref(
            item, errors, path=f"{path}[{index}]", require_project_key=require_project_key
        )
        for index, item in enumerate(items)
    )


def _parse_citation_ref(
    value: object,
    errors: list[DailyReportWriteError],
    *,
    path: str,
    require_project_key: bool,
) -> CitationRef:
    mapping = _as_mapping(value)
    session_ref = _parse_ref_field(mapping.get("session_ref"), errors, path=f"{path}.session_ref")
    turn_ref = _parse_ref_field(mapping.get("turn_ref"), errors, path=f"{path}.turn_ref")
    raw_project_key = mapping.get("project_key")
    project_key: str | None = None
    if require_project_key:
        project_key = _parse_ref_field(raw_project_key, errors, path=f"{path}.project_key")
    elif isinstance(raw_project_key, str) and raw_project_key.strip():
        # A per-project pass omits project_key (the tool argument supplies it), but if one is
        # submitted it is retained verbatim so the write tool can reject a mismatch rather than
        # silently binding the citation to the wrong project.
        project_key = raw_project_key
    return CitationRef(session_ref=session_ref, turn_ref=turn_ref, project_key=project_key)


def _parse_nonempty(value: object, errors: list[DailyReportWriteError], *, path: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    errors.append(DailyReportWriteError(path, _nonempty_message(path), _NONEMPTY_HINT))
    return value if isinstance(value, str) else ""


def _parse_ref_field(value: object, errors: list[DailyReportWriteError], *, path: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    errors.append(DailyReportWriteError(path, _nonempty_message(path), _REF_HINT))
    return value if isinstance(value, str) else ""


def _parse_enum(
    value: object,
    allowed: frozenset[str],
    errors: list[DailyReportWriteError],
    *,
    path: str,
    controlled: str,
) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    errors.append(
        DailyReportWriteError(
            path, _controlled_message(path, controlled), _controlled_hint(allowed)
        )
    )
    return value if isinstance(value, str) else ""


def _parse_str_list(
    value: object, errors: list[DailyReportWriteError], *, path: str
) -> tuple[str, ...]:
    result: list[str] = []
    for index, item in enumerate(_as_list(value)):
        if isinstance(item, str) and item.strip():
            result.append(item)
        else:
            errors.append(
                DailyReportWriteError(
                    f"{path}[{index}]", _nonempty_message(f"{path}[{index}]"), _NONEMPTY_HINT
                )
            )
    return tuple(result)


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _nonempty_message(path: str) -> str:
    return f"{path} must be a non-empty string"


def _controlled_message(path: str, controlled: str) -> str:
    return f"{path} must be a controlled {controlled} value"


def _controlled_hint(allowed: frozenset[str]) -> str:
    return "use a controlled value such as " + ", ".join(sorted(allowed))


def _empty_citations_message(path: str) -> str:
    return f"{path} must cite at least one turn"


_NONEMPTY_HINT = "provide a concise non-empty string"
_REF_HINT = 'reference a turn as {"session_ref": "S0001", "turn_ref": "T0001"}'
_CITATION_HINT = "every synthesized claim must cite the turns it rests on"
