"""Tests for the engine-independent abstract layout built from ``daily-report.json``.

``build_layout`` turns a finalized daily report into the presentation tree the renderers walk: a
``Document`` header, the four sections (Executive Summary, Work by Project, Engagement Assessment,
Team Learning), a ``Group`` per project, work items material-first with labeled ``Toggle``s, the
judgment groups present only when they carry observations/patterns, and the per-section ``Empty``
fallback when a section's data is absent. Citations carry the cross-project scoping decision: bare
within Work by Project, project-qualified in the cross-project sections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from prompt_diary.generate.rendering.layout import (
    Callout,
    Document,
    Empty,
    Group,
    ListBlock,
    Prose,
    Section,
    Tag,
    Toggle,
    build_layout,
)
from tests.support.daily_synthesis import (
    PROJECT_LABEL,
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    empty_daily_workspace,
    fill_synthesize_slots,
    finalize_daily_report_via_api,
    load_daily_report,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.rendering.layout import Block


def _finalized_report(tmp_path: Path) -> dict[str, Any]:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    finalize_daily_report_via_api(workspace)
    return load_daily_report(workspace)


def _empty_report(tmp_path: Path) -> dict[str, Any]:
    workspace = empty_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    finalize_daily_report_via_api(workspace)
    return load_daily_report(workspace)


def _section(document: Document, title: str) -> Section:
    for child in document.children:
        if child.title == title:
            return child
    pytest.fail(f"no section {title!r}")


def _groups(section: Section) -> list[Group]:
    return [child for child in section.children if isinstance(child, Group)]


def _prose(blocks: tuple[Block, ...]) -> list[Prose]:
    return [block for block in blocks if isinstance(block, Prose)]


def _lists(blocks: tuple[Block, ...]) -> list[ListBlock]:
    return [block for block in blocks if isinstance(block, ListBlock)]


def _toggles(blocks: tuple[Block, ...]) -> list[Toggle]:
    return [block for block in blocks if isinstance(block, Toggle)]


def _callouts(blocks: tuple[Block, ...]) -> list[Callout]:
    return [block for block in blocks if isinstance(block, Callout)]


# --- document header --------------------------------------------------------------------------


def test_layout_document_title_and_properties(tmp_path: Path) -> None:
    document = build_layout(_finalized_report(tmp_path))

    assert document.title == "Prompt Diary Report — 2026-05-28"
    assert document.properties == {
        # ``report_date`` is carried for the Notion renderer's Date column; the Markdown header
        # ignores it (it reads only status/window/overall_confidence).
        "report_date": "2026-05-28",
        "status": "final",
        # The window range uses an en dash (U+2013), built here as an escape to keep the source
        # free of an ambiguous literal.
        "window": "2026-05-28T00:00:00+08:00\u20132026-05-29T00:00:00+08:00, Asia/Shanghai",
        "overall_confidence": "medium",
    }


def test_layout_sections_in_order(tmp_path: Path) -> None:
    document = build_layout(_finalized_report(tmp_path))

    titles = [child.title for child in document.children]
    assert titles == [
        "Executive Summary",
        "Work by Project",
        "Engagement Assessment",
        "Team Learning",
    ]


# --- executive summary ------------------------------------------------------------------------


def test_layout_executive_summary_outcomes_scoped(tmp_path: Path) -> None:
    section = _section(build_layout(_finalized_report(tmp_path)), "Executive Summary")
    outcomes = _lists(section.children)[0]

    texts = [_prose((item,))[0].text for item in outcomes.items]
    assert texts == [
        "Top-level turn_ref adopted; chain_ref removed from the evidence surface.",
        "Three-layer QA strategy delivered.",
    ]
    # Cross-project section: each citation is project-scoped.
    first = _prose((outcomes.items[0],))[0]
    assert first.citation is not None
    assert [ref["scoped"] for ref in first.citation.refs] == [True]
    assert first.citation.refs[0]["project_label"] == PROJECT_LABEL
    assert first.citation.refs[0]["session_ref"] == "S0001"
    assert first.citation.refs[0]["lines"] == "2-8"


def test_layout_executive_summary_open_items_empty_list(tmp_path: Path) -> None:
    section = _section(build_layout(_finalized_report(tmp_path)), "Executive Summary")
    open_items = _lists(section.children)[1]

    assert open_items.items == ()


# --- work by project --------------------------------------------------------------------------


def test_layout_work_by_project_group_per_project(tmp_path: Path) -> None:
    section = _section(build_layout(_finalized_report(tmp_path)), "Work by Project")
    groups = _groups(section)

    assert [group.label for group in groups] == [PROJECT_LABEL]


def test_layout_project_summary_prose_unscoped(tmp_path: Path) -> None:
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    summary = _prose(group.children)[0]

    assert summary.text == "Simplified the evidence tools and designed the QA approach."
    assert summary.citation is not None
    # Within a project group, citations are unscoped (project implied by the enclosing group).
    assert [ref["scoped"] for ref in summary.citation.refs] == [False, False]
    assert [ref["session_ref"] for ref in summary.citation.refs] == ["S0001", "S0002"]


def test_layout_material_work_items_first(tmp_path: Path) -> None:
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    work_items = _lists(group.children)[0]

    # Two material work items render as list items; the two minor ones fold into a toggle.
    assert len(work_items.items) == 2


def test_layout_work_item_carries_disposition_and_confidence_tags(tmp_path: Path) -> None:
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    first_item = _lists(group.children)[0].items[0]
    assert isinstance(first_item, Group)

    # The work-item Group is labelled by its title; its disposition/confidence are Tag children.
    tags = [child for child in first_item.children if isinstance(child, Tag)]
    assert first_item.label == "Simplify the MCP evidence tools and drop chain_ref"
    assert (tags[0].scale, tags[0].value) == ("disposition", "completed")
    assert (tags[1].scale, tags[1].value) == ("confidence", "high")


def test_layout_work_item_context_and_user_messages_toggles(tmp_path: Path) -> None:
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    first_item = _lists(group.children)[0].items[0]
    assert isinstance(first_item, Group)
    toggles = _toggles(first_item.children)

    assert [toggle.label for toggle in toggles] == ["Context and Response", "User Messages"]
    context_text = _prose(toggles[0].children)[0].text
    assert "User asked to simplify the MCP evidence tools and remove chain_ref." in context_text
    # User messages are the verbatim source_user_messages, carried as quote callouts (untrusted).
    messages = [child for child in toggles[1].children if isinstance(child, Callout)]
    assert all(message.tone == "quote" for message in messages)
    assert any("Please simplify the MCP evidence tools" in message.text for message in messages)


def test_layout_work_item_outcomes_unscoped(tmp_path: Path) -> None:
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    first_item = _lists(group.children)[0].items[0]
    assert isinstance(first_item, Group)
    outcomes = _lists(first_item.children)[0]

    outcome = _prose((outcomes.items[0],))[0]
    assert (
        outcome.text == "Top-level turn_ref adopted; chain_ref removed from the evidence surface."
    )
    assert outcome.citation is not None
    assert [ref["scoped"] for ref in outcome.citation.refs] == [False]
    # The outcome carries its own confidence inline as a tag (separate from the work-item heading).
    assert [(tag.scale, tag.value) for tag in outcome.tags] == [("confidence", "high")]


def test_layout_work_item_limit_callout(tmp_path: Path) -> None:
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    first_item = _lists(group.children)[0].items[0]
    assert isinstance(first_item, Group)
    callouts = _callouts(first_item.children)

    assert [callout.tone for callout in callouts] == ["limit"]
    assert "Prompt-test suite not confirmed green within these turns." in callouts[0].text


def test_layout_work_item_without_limits_has_no_callout(tmp_path: Path) -> None:
    # W0002 carries no limits, so its group has no limit callout.
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    second_item = _lists(group.children)[0].items[1]
    assert isinstance(second_item, Group)

    assert _callouts(second_item.children) == []


def _no_outcome_material_report() -> dict[str, Any]:
    """A minimal report whose one material item has a terminal state but no outcomes.

    Work by Project shows the terminal disposition as the visible claim in place of the outcomes, so
    the terminal state carries its own citation (unscoped within the project group).
    """
    citation = {"project_key": "k", "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}
    return {
        "schema_version": 1,
        "report_date": "2026-05-28",
        "status": "final",
        "window": {"start": "s", "end": "e", "timezone": "Asia/Shanghai"},
        "overall_confidence": "high",
        "executive_summary": {"top_outcomes": [], "open_items": []},
        "projects": [
            {
                "project_key": "k",
                "project_label": "Proj",
                "summary": {"text": "sum", "citations": [citation]},
                "work_items": [
                    {
                        "work_item_ref": "W0001",
                        "title": "Blocked item",
                        "kind": "material_work_item",
                        "disposition": "blocked",
                        "confidence": "high",
                        "covered_turns": [{"session_ref": "S0001", "turn_ref": "T0001"}],
                        "trigger_summary": None,
                        "agent_reaction_summary": None,
                        "outcomes": [],
                        "terminal_states": [
                            {"summary": "Blocked on a missing dependency.", "citations": [citation]}
                        ],
                        "limits": [],
                    }
                ],
                "source_user_messages": [],
            }
        ],
        "engagement_assessment": None,
        "team_learning": None,
    }


def test_layout_no_outcome_material_item_terminal_claim_is_cited(tmp_path: Path) -> None:
    del tmp_path
    # A no-outcome material item renders its terminal-state summary in place of the outcomes, and
    # that claim carries its citation (unscoped within the project group), not a bare line.
    group = _groups(_section(build_layout(_no_outcome_material_report()), "Work by Project"))[0]
    item = _lists(group.children)[0].items[0]
    assert isinstance(item, Group)
    terminal_claim = _prose(_lists(item.children)[0].items)[0]

    assert terminal_claim.text == "Blocked on a missing dependency."
    assert terminal_claim.citation is not None
    assert [ref["scoped"] for ref in terminal_claim.citation.refs] == [False]
    assert terminal_claim.citation.refs[0]["lines"] == "2-8"
    # A terminal state has no per-claim confidence, so (unlike an outcome) it shows no such tag.
    assert terminal_claim.tags == ()


def test_layout_minor_activity_toggle_gathers_minor_items(tmp_path: Path) -> None:
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    minor = [t for t in _toggles(group.children) if t.label == "Minor activity"]

    assert len(minor) == 1
    minor_list = _lists(minor[0].children)[0]
    titles = [item.label for item in minor_list.items if isinstance(item, Group)]
    assert "Clarify whether the placeholder wording was misleading" in titles
    assert "Indexed turn with no extractable evidence" in titles


def test_layout_minor_item_without_messages_has_no_user_messages_toggle(tmp_path: Path) -> None:
    # W0004 is a gap turn (T0003) with no source_user_messages, so its work-item group must carry
    # no "User messages" toggle at all — an empty toggle is omitted, mirroring the "Why" toggle.
    group = _groups(_section(build_layout(_finalized_report(tmp_path)), "Work by Project"))[0]
    minor = next(t for t in _toggles(group.children) if t.label == "Minor activity")
    gap_item = next(
        item
        for item in _lists(minor.children)[0].items
        if isinstance(item, Group) and item.label == "Indexed turn with no extractable evidence"
    )

    assert [t.label for t in _toggles(gap_item.children)] == []
    # The work item with a covered message (W0003, T0002) still carries its toggle, for contrast.
    placeholder_item = next(
        item
        for item in _lists(minor.children)[0].items
        if isinstance(item, Group)
        and item.label == "Clarify whether the placeholder wording was misleading"
    )
    assert [t.label for t in _toggles(placeholder_item.children)] == ["User Messages"]


# --- engagement assessment --------------------------------------------------------------------


def test_layout_engagement_overall_reading_and_present_group(tmp_path: Path) -> None:
    section = _section(build_layout(_finalized_report(tmp_path)), "Engagement Assessment")

    reading = _prose(section.children)[0]
    assert reading.text == "The user framed concrete goals and approved results."
    # Only the Direction dimension has an observation in the fixture.
    assert [group.label for group in _groups(section)] == ["Direction"]
    observation = _lists(_groups(section)[0].children)[0].items[0]
    statement = _prose((observation,))[0]
    assert statement.text == "Asked to simplify the evidence tools and drop chain_ref."
    assert statement.citation is not None
    assert statement.citation.refs[0]["scoped"] is True
    # Each observation carries its own confidence inline as a tag.
    assert [(tag.scale, tag.value) for tag in statement.tags] == [("confidence", "medium")]


def test_layout_engagement_overall_reading_carries_confidence_tag(tmp_path: Path) -> None:
    # The overall_reading is a standalone judgment, so its lead Prose carries its own confidence as
    # an inline tag — it must not render unhedged.
    section = _section(build_layout(_finalized_report(tmp_path)), "Engagement Assessment")

    reading = _prose(section.children)[0]
    assert [(tag.scale, tag.value) for tag in reading.tags] == [("confidence", "medium")]


def test_layout_engagement_has_limit_callout(tmp_path: Path) -> None:
    section = _section(build_layout(_finalized_report(tmp_path)), "Engagement Assessment")

    callouts = _callouts(section.children)
    assert len(callouts) == 1
    assert "Offline thinking and review are not observable." in callouts[0].text


# --- team learning ----------------------------------------------------------------------------


def test_layout_team_learning_takeaways_and_present_group(tmp_path: Path) -> None:
    section = _section(build_layout(_finalized_report(tmp_path)), "Team Learning")

    takeaways = _prose(section.children)[0]
    assert takeaways.text == "Capturing a reusable QA approach is worth promoting."
    # Only the reuse pattern is present in the fixture.
    assert [group.label for group in _groups(section)] == ["Reuse"]
    pattern = _prose((_lists(_groups(section)[0].children)[0].items[0],))[0]
    assert pattern.citation is not None
    assert pattern.citation.refs[0]["scoped"] is True
    # The pattern prose lifts statement, rationale, and recurrence into one run (no new claim), and
    # carries its own confidence inline as a tag.
    assert pattern.text == (
        "A three-layer QA strategy was written down as a repeatable approach. "
        "— A reusable checklist lowers the attention cost of future QA work. "
        "· recurrence: single sighting; likely to recur for future test design"
    )
    assert [(tag.scale, tag.value) for tag in pattern.tags] == [("confidence", "low")]


def test_layout_team_learning_takeaways_carries_confidence_tag(tmp_path: Path) -> None:
    # The takeaways is a standalone judgment, so its lead Prose carries its own confidence tag.
    section = _section(build_layout(_finalized_report(tmp_path)), "Team Learning")

    takeaways = _prose(section.children)[0]
    assert [(tag.scale, tag.value) for tag in takeaways.tags] == [("confidence", "low")]


# --- empty report -----------------------------------------------------------------------------


def test_layout_empty_report_all_sections_empty(tmp_path: Path) -> None:
    document = build_layout(_empty_report(tmp_path))

    for title, fallback in (
        ("Executive Summary", "No supported work claims found for this report window."),
        (
            "Work by Project",
            "No supported project-level work items found for this report window.",
        ),
        (
            "Engagement Assessment",
            "Insufficient supported engagement evidence for this report window.",
        ),
        ("Team Learning", "No supported reusable agent-driving pattern found."),
    ):
        section = _section(document, title)
        empties = [child for child in section.children if isinstance(child, Empty)]
        assert [empty.fallback for empty in empties] == [fallback]


def test_layout_empty_report_overall_confidence_not_applicable(tmp_path: Path) -> None:
    document = build_layout(_empty_report(tmp_path))

    assert document.properties["overall_confidence"] == "n/a"
