"""Finalize the daily report after the synthesize passes have run.

Finalize is the deterministic closing step. It reads the post-pass ``daily-report.json``, rolls the
per-claim confidences of the material work items and the two judgment sections into a single
``overall_confidence``, and validates the whole document. A report that has any work item must have
every work-bearing project's ``summary`` filled and both judgment sections present; every filled
synthesized claim — the project summaries, the engagement and team-learning leads and their
entries, and the curated Executive Summary headlines — must carry non-empty ``text`` (where it has
one) and at least one citation; and every stored citation must carry its four resolved keys. The
faithfully-lifted Work-by-Project ``outcomes[]`` are exempt from the non-empty-citation rule, as an
uncited upstream outcome is legitimate. On success it writes the report back with
``overall_confidence`` filled and returns :class:`FinalizedResult`; a missing required slot, an
incomplete synthesized claim, or a malformed citation yields :class:`FinalizeInvalidResult` and
leaves the file untouched.

An empty report — no work items in any project — has no per-claim confidences to roll up, so its
``overall_confidence`` is ``null`` and its judgment slots stay ``null``; that is a valid finalized
state, not an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from prompt_diary.generate.daily_synthesis.model import CONFIDENCE_RANK, DailyReportWriteError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = [
    "DailyReportWriteError",
    "FinalizeInvalidResult",
    "FinalizeResult",
    "FinalizedResult",
    "finalize_daily_report",
]

_REPORT_NAME = "daily-report.json"
_CITATION_KEYS = ("project_key", "session_ref", "turn_ref", "lines")


@dataclass(frozen=True)
class FinalizedResult:
    """A successful finalize: the report is written with ``overall_confidence`` filled."""

    status: Literal["finalized"]
    overall_confidence: str | None


@dataclass(frozen=True)
class FinalizeInvalidResult:
    """A rejected finalize holding the structured errors found; the report is left unchanged."""

    status: Literal["invalid"]
    errors: tuple[DailyReportWriteError, ...]


FinalizeResult: TypeAlias = FinalizedResult | FinalizeInvalidResult


def finalize_daily_report(*, workspace_path: Path) -> FinalizeResult:
    """Roll up ``overall_confidence``, validate the report, and write it on success."""
    path = workspace_path / _REPORT_NAME
    report = _load_json(path)

    overall_confidence = _overall_confidence(report)
    errors = _validate(report)
    if errors:
        return FinalizeInvalidResult("invalid", tuple(errors))

    report["overall_confidence"] = overall_confidence
    _write_report(path, report)
    return FinalizedResult("finalized", overall_confidence)


def _overall_confidence(report: dict[str, Any]) -> str | None:
    values = list(_confidence_values(report))
    if not values:
        return None
    mean = sum(values) / len(values)
    if mean >= 2.5:
        return "high"
    if mean >= 1.5:
        return "medium"
    return "low"


def _confidence_values(report: dict[str, Any]) -> Iterator[int]:
    for project in _as_list(report.get("projects")):
        for item in _as_list(_as_mapping(project).get("work_items")):
            yield from _material_item_confidences(_as_mapping(item))
    yield from _section_confidences(
        _as_mapping(report.get("engagement_assessment")), "overall_reading", "observations"
    )
    yield from _section_confidences(
        _as_mapping(report.get("team_learning")), "takeaways", "patterns"
    )


def _material_item_confidences(item: dict[str, Any]) -> Iterator[int]:
    if item.get("kind") != "material_work_item":
        return
    yield from _ranked(item.get("confidence"))
    for outcome in _as_list(item.get("outcomes")):
        yield from _ranked(_as_mapping(outcome).get("confidence"))


def _section_confidences(section: dict[str, Any], lead_key: str, items_key: str) -> Iterator[int]:
    yield from _ranked(_as_mapping(section.get(lead_key)).get("confidence"))
    for entry in _as_list(section.get(items_key)):
        yield from _ranked(_as_mapping(entry).get("confidence"))


def _ranked(value: object) -> Iterator[int]:
    rank = CONFIDENCE_RANK.get(value) if isinstance(value, str) else None
    if rank is not None:
        yield rank


def _validate(report: dict[str, Any]) -> list[DailyReportWriteError]:
    errors: list[DailyReportWriteError] = []
    if _has_work_items(report):
        errors.extend(_required_slot_errors(report))
        errors.extend(_completeness_errors(report))
    errors.extend(_citation_errors(report))
    return errors


def _has_work_items(report: dict[str, Any]) -> bool:
    return any(
        _as_list(_as_mapping(project).get("work_items"))
        for project in _as_list(report.get("projects"))
    )


def _required_slot_errors(report: dict[str, Any]) -> Iterator[DailyReportWriteError]:
    for index, project in enumerate(_as_list(report.get("projects"))):
        mapping = _as_mapping(project)
        if _as_list(mapping.get("work_items")) and mapping.get("summary") is None:
            key = _as_str(mapping.get("project_key"))
            yield DailyReportWriteError(
                f"projects[{index}].summary", _missing_summary_message(key), _SUMMARY_HINT
            )
    if report.get("engagement_assessment") is None:
        yield DailyReportWriteError(
            "engagement_assessment",
            _missing_section_message("engagement_assessment"),
            _SECTION_HINT,
        )
    if report.get("team_learning") is None:
        yield DailyReportWriteError(
            "team_learning", _missing_section_message("team_learning"), _SECTION_HINT
        )


def _completeness_errors(report: dict[str, Any]) -> Iterator[DailyReportWriteError]:
    """Reject a filled synthesized claim that is incomplete — empty ``text`` or no ``citations``.

    Scoped to the SYNTHESIZED claim-bearing fields (per-project summaries, the two judgment leads
    and their entries) and the curated Executive Summary headlines. A present-but-empty slot would
    pass the required-slot presence check yet ground no claim, so finalize must reject it. Faithful
    Work-by-Project ``outcomes[]`` are deliberately not checked: they are uncited lifts of upstream
    work items, which may legitimately carry no citation.
    """
    summary = _as_mapping(report.get("executive_summary"))
    yield from _cited_claim_list(summary.get("top_outcomes"), "executive_summary.top_outcomes")
    yield from _cited_claim_list(summary.get("open_items"), "executive_summary.open_items")
    yield from _project_summary_completeness(report)
    yield from _section_completeness(
        report.get("engagement_assessment"),
        "engagement_assessment",
        "overall_reading",
        "observations",
    )
    yield from _section_completeness(
        report.get("team_learning"), "team_learning", "takeaways", "patterns"
    )


def _project_summary_completeness(report: dict[str, Any]) -> Iterator[DailyReportWriteError]:
    for index, project in enumerate(_as_list(report.get("projects"))):
        mapping = _as_mapping(project)
        # A summary that is still null is reported by the required-slot check, not here.
        if not _as_list(mapping.get("work_items")) or mapping.get("summary") is None:
            continue
        yield from _cited_claim(mapping.get("summary"), f"projects[{index}].summary")


def _section_completeness(
    section: object, base: str, lead_key: str, items_key: str
) -> Iterator[DailyReportWriteError]:
    # A null section is reported by the required-slot check; completeness applies to a filled one.
    if section is None:
        return
    mapping = _as_mapping(section)
    yield from _cited_claim(mapping.get(lead_key), f"{base}.{lead_key}")
    for index, entry in enumerate(_as_list(mapping.get(items_key))):
        yield from _citations_present(entry, f"{base}.{items_key}[{index}]")


def _cited_claim_list(value: object, base: str) -> Iterator[DailyReportWriteError]:
    for index, entry in enumerate(_as_list(value)):
        yield from _cited_claim(entry, f"{base}[{index}]")


def _cited_claim(value: object, base: str) -> Iterator[DailyReportWriteError]:
    """A synthesized claim must carry non-empty ``text`` and at least one citation."""
    mapping = _as_mapping(value)
    text = mapping.get("text")
    if not (isinstance(text, str) and text.strip()):
        yield DailyReportWriteError(base, _empty_claim_text_message(base), _CLAIM_TEXT_HINT)
    yield from _citations_present(value, base)


def _citations_present(value: object, base: str) -> Iterator[DailyReportWriteError]:
    if not _as_list(_as_mapping(value).get("citations")):
        path = f"{base}.citations"
        yield DailyReportWriteError(path, _empty_citations_message(path), _EMPTY_CITATIONS_HINT)


def _citation_errors(report: dict[str, Any]) -> Iterator[DailyReportWriteError]:
    for path, citation in _iter_citations(report):
        if not _is_well_formed(citation):
            yield DailyReportWriteError(path, _malformed_citation_message(path), _CITATION_HINT)


def _iter_citations(report: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    summary = _as_mapping(report.get("executive_summary"))
    yield from _cited_list(summary.get("top_outcomes"), "executive_summary.top_outcomes")
    yield from _cited_list(summary.get("open_items"), "executive_summary.open_items")
    for project_index, project in enumerate(_as_list(report.get("projects"))):
        yield from _project_citations(_as_mapping(project), project_index)
    yield from _section_citations(
        _as_mapping(report.get("engagement_assessment")),
        "engagement_assessment",
        "overall_reading",
        "observations",
    )
    yield from _section_citations(
        _as_mapping(report.get("team_learning")), "team_learning", "takeaways", "patterns"
    )


def _project_citations(
    project: dict[str, Any], project_index: int
) -> Iterator[tuple[str, dict[str, Any]]]:
    base = f"projects[{project_index}]"
    yield from _cited_object(project.get("summary"), f"{base}.summary")
    for item_index, item in enumerate(_as_list(project.get("work_items"))):
        for outcome_index, outcome in enumerate(_as_list(_as_mapping(item).get("outcomes"))):
            yield from _cited_object(
                outcome, f"{base}.work_items[{item_index}].outcomes[{outcome_index}]"
            )


def _section_citations(
    section: dict[str, Any], base: str, lead_key: str, items_key: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    yield from _cited_object(section.get(lead_key), f"{base}.{lead_key}")
    for index, entry in enumerate(_as_list(section.get(items_key))):
        yield from _cited_object(entry, f"{base}.{items_key}[{index}]")


def _cited_list(value: object, base: str) -> Iterator[tuple[str, dict[str, Any]]]:
    for index, entry in enumerate(_as_list(value)):
        yield from _cited_object(entry, f"{base}[{index}]")


def _cited_object(value: object, base: str) -> Iterator[tuple[str, dict[str, Any]]]:
    mapping = _as_mapping(value)
    for index, citation in enumerate(_as_list(mapping.get("citations"))):
        yield f"{base}.citations[{index}]", _as_mapping(citation)


def _is_well_formed(citation: dict[str, Any]) -> bool:
    return all(isinstance(citation.get(key), str) and citation.get(key) for key in _CITATION_KEYS)


def _load_json(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _missing_summary_message(project_key: str) -> str:
    return f"project {project_key!r} has work items but no summary"


def _missing_section_message(section: str) -> str:
    return f"a report with work items must have a non-null {section}"


def _malformed_citation_message(path: str) -> str:
    return f"{path} must carry the four resolved citation keys"


def _empty_claim_text_message(path: str) -> str:
    return f"{path}.text must be a non-empty synthesized claim"


def _empty_citations_message(path: str) -> str:
    return f"{path} must cite at least one turn"


_SUMMARY_HINT = "run the per-project summary pass before finalize"
_SECTION_HINT = "run the engagement and team-learning passes before finalize"
_CITATION_HINT = "a stored citation must carry project_key, session_ref, turn_ref, and lines"
_CLAIM_TEXT_HINT = "a synthesized claim renders its text; it must not be empty"
_EMPTY_CITATIONS_HINT = "every synthesized claim must cite the turns it rests on"
