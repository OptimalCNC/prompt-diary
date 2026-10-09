"""Tests for the deterministic Notion rendering of ``daily-report.json`` to ``report.notion.json``.

``render_notion_artifact`` reads the finalized model, preserves semantic roles in the Notion layout,
and writes the page payload (title, metadata properties, body block children) to
``report.notion.json``. These tests pin visible project headings and summaries, collapsed work-item
details, assessment and practice structure, citations, the three Empty fallbacks,
and the two invariants that make Notion rendering faithful and safe:

- **No new claims** — every claim-bearing string the renderer emits is sourced verbatim from the
  model (asserted by finding each model string in the rendered ``text.content``).
- **Structural injection safety** — model strings are placed only in plain ``text.content`` with no
  ``link``, so a session-derived string carrying Markdown/HTML renders verbatim and cannot forge
  structure. Unlike the Markdown renderer there is no escaping, so the intraword ``_`` in
  ``chain_ref`` appears literally, and no rich-text run anywhere carries a link.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from prompt_diary.generate.rendering.layout import (
    Citation,
    Document,
    Group,
    ListBlock,
    Prose,
    Section,
    Tag,
    Toggle,
)
from prompt_diary.generate.rendering.notion_layout import build_notion_layout
from prompt_diary.generate.rendering.render_notion import (
    render_notion,
    render_notion_artifact,
)
from tests.support.daily_synthesis import (
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    empty_daily_workspace,
    fill_synthesize_slots,
    finalize_daily_report_via_api,
    load_daily_report,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path


# --- traversal helpers ------------------------------------------------------------------------


def _iter_blocks(blocks: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield every block, descending into a block's nested ``children`` (toggles)."""
    for block in blocks:
        yield block
        body = block.get(block["type"], {})
        children = body.get("children", [])
        yield from _iter_blocks(children)


def _of_type(blocks: Iterable[dict[str, Any]], block_type: str) -> list[dict[str, Any]]:
    return [block for block in _iter_blocks(blocks) if block["type"] == block_type]


def _rich_text(block: dict[str, Any]) -> list[dict[str, Any]]:
    return block[block["type"]].get("rich_text", [])


def _plain(block: dict[str, Any]) -> str:
    return "".join(run["text"]["content"] for run in _rich_text(block))


def _all_runs(blocks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for block in _iter_blocks(blocks) for run in _rich_text(block)]


def _code_runs(block: dict[str, Any]) -> list[dict[str, Any]]:
    return [run for run in _rich_text(block) if run.get("annotations", {}).get("code")]


def _citation_runs(block: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        run
        for run in _rich_text(block)
        if run.get("_prompt_diary_link_target") or run["text"]["content"] == "; "
    ]


def _plain_texts(blocks: Iterable[dict[str, Any]], block_type: str) -> list[str]:
    return [_plain(block) for block in _of_type(blocks, block_type)]


def _children_of(toggle: dict[str, Any]) -> list[dict[str, Any]]:
    return toggle["toggle"]["children"]


def _doc_with_section(section: Section) -> Document:
    """A minimal layout Document wrapping one section, for renderer unit tests."""
    return Document(
        "Renderer Escaping Review",
        {"status": "final", "window": "w", "overall_confidence": "high", "report_date": "d"},
        (section,),
    )


def _render_basic(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    finalize_daily_report_via_api(workspace)
    path = render_notion_artifact(workspace_path=workspace)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, load_daily_report(workspace)


def _basic_children(tmp_path: Path) -> list[dict[str, Any]]:
    payload, _ = _render_basic(tmp_path)
    return payload["children"]


# --- write contract + header ------------------------------------------------------------------


def test_render_notion_writes_payload_to_workspace_root(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    finalize_daily_report_via_api(workspace)

    path = render_notion_artifact(workspace_path=workspace)

    assert path.name == "report.notion.json"
    assert path.parent == workspace


def test_render_notion_title_and_properties(tmp_path: Path) -> None:
    payload, _ = _render_basic(tmp_path)

    assert payload["title"] == "Evidence Tools and QA Strategy"
    assert payload["properties"] == {
        # ``report_date`` is added to the layout properties for the Notion Date column; the Markdown
        # renderer may also show it in its standalone file heading.
        "report_date": "2026-05-28",
        "status": "final",
        # The window range uses an en dash (U+2013), built here as an escape (as in test_layout).
        "window": "2026-05-28T00:00:00+08:00\u20132026-05-29T00:00:00+08:00, Asia/Shanghai",
        "overall_confidence": "medium",
    }


def test_render_notion_sections_are_heading_1(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    assert [_plain(block) for block in children if block["type"] == "heading_1"] == [
        "Work by Project",
        "Engagement Assessment",
        "Team Learning",
        "Evidence Chains",
    ]


def test_render_notion_opens_with_work_by_project_without_an_extra_daily_summary(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)

    assert _plain(children[0]) == "Work by Project"
    assert "Executive Summary" not in _plain_texts(children, "heading_1")
    assert "Daily reading" not in _plain_texts(children, "heading_1")


# --- work by project (visible project summary, work-item toggle) -------------------------------


def test_render_notion_project_is_heading_2_with_visible_summary_paragraph(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    assert children[1]["type"] == "heading_2"
    assert _plain(children[1]) == "ReportGenerator"
    summary = next(
        p
        for p in children
        if p["type"] == "paragraph"
        if "Simplified the evidence tools and designed the QA approach." in _plain(p)
    )
    assert children[2] == summary
    assert _plain(summary).splitlines() == [
        "Simplified the evidence tools and designed the QA approach.",
        "S0001/T0001; S0002/T0001",
    ]
    # The project summary's citations are unscoped (project implied), one link-targeted run per
    # turn reference. The publisher may replace those runs with native Notion inline links, so the
    # renderer keeps them as normal text instead of inline code.
    citations = _citation_runs(summary)
    assert [run["text"]["content"] for run in citations] == [
        "S0001/T0001",
        "; ",
        "S0002/T0001",
    ]
    assert _code_runs(summary) == []
    assert citations[0]["_prompt_diary_link_target"] == {
        "project_key": "ReportGenerator-e6ff7eeda632",
        "session_ref": "S0001",
        "turn_ref": "T0001",
    }
    assert citations[2]["_prompt_diary_link_target"] == {
        "project_key": "ReportGenerator-e6ff7eeda632",
        "session_ref": "S0002",
        "turn_ref": "T0001",
    }


def test_render_notion_work_item_toggle_keeps_metadata_inside_its_details(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)
    toggles = _of_type(children, "toggle")

    # Task names keep the collapsed reading path concise; recorded state and confidence remain
    # available inside the details without claiming a status that later outcomes may supersede.
    toggle = next(t for t in toggles if _plain(t).startswith("Simplify the MCP evidence tools"))
    assert _plain(toggle) == "Simplify the MCP evidence tools and drop chain_ref"
    metadata = _children_of(toggle)[0]
    assert metadata["type"] == "paragraph"
    assert _plain(metadata) == "Recorded state: completed · high confidence"
    runs = _rich_text(metadata)
    status = next(run for run in runs if run["text"]["content"] == "Recorded state: completed")
    confidence = next(run for run in runs if run["text"]["content"] == "high confidence")
    assert status["annotations"]["color"] == "gray"
    assert confidence["annotations"]["color"] == "gray"


def test_render_notion_work_item_details_follow_the_collaboration_before_original_messages(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)
    work_item = next(
        t
        for t in _of_type(children, "toggle")
        if _plain(t).startswith("Simplify the MCP evidence tools and drop chain_ref")
    )
    nested = _children_of(work_item)

    assert [block["type"] for block in nested] == [
        "paragraph",
        "paragraph",
        "paragraph",
        "paragraph",
        "bulleted_list_item",
        "paragraph",
        "paragraph",
        "quote",
    ]
    assert _plain(nested[0]) == "Recorded state: completed · high confidence"
    assert _plain(nested[1]) == (
        "Human direction  User asked to simplify the MCP evidence tools and remove chain_ref."
    )
    assert _plain(nested[2]) == (
        "Agent response  Updated the MCP tools page, evidence contract, and extractor prompt "
        "to a top-level turn_ref identity."
    )
    assert _plain(nested[3]) == "Outcomes"
    outcome = nested[4]
    assert "Top-level turn_ref adopted; chain_ref removed from the evidence surface." in _plain(
        outcome
    )
    assert _plain(nested[5]) == "Limits  Prompt-test suite not confirmed green within these turns."
    assert _plain(nested[6]) == "Original user messages"
    assert _plain(nested[7]) == "Please simplify the MCP evidence tools and drop chain_ref."
    assert all(_rich_text(nested[index])[0]["annotations"]["bold"] for index in (1, 2, 3, 6))
    assert all("children" not in block[block["type"]] for block in nested)
    assert [run["text"]["content"] for run in _citation_runs(outcome)] == ["S0001/T0001"]
    assert _code_runs(outcome) == []
    assert " · high confidence" in _plain(outcome)


def test_render_notion_work_item_body_skips_non_rendering_child_without_divider(
    tmp_path: Path,
) -> None:
    del tmp_path
    work_item = Group(
        "Skipped child",
        (
            Tag("completed", "disposition"),
            Tag("high", "confidence"),
            Citation(()),
            Prose("Visible body.", None),
        ),
    )
    section = Section("Work by Project", (Group("Proj", (ListBlock("bullet", (work_item,)),)),))

    payload = render_notion(_doc_with_section(section))
    toggle = next(t for t in _of_type(payload.children, "toggle") if "Skipped child" in _plain(t))
    nested = _children_of(toggle)

    assert [block["type"] for block in nested] == ["paragraph", "paragraph"]
    assert _plain(nested[0]) == "Recorded state: completed · high confidence"
    assert _plain(nested[1]) == "Visible body."


def test_render_notion_user_messages_are_verbatim_quote_blocks(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)
    quotes = _plain_texts(children, "quote")

    # Untrusted user messages render as quote blocks, verbatim — the intraword ``_`` is NOT escaped
    # (Notion stores content literally), unlike the Markdown view's ``chain\_ref``.
    assert "Please simplify the MCP evidence tools and drop chain_ref." in quotes
    assert "Design the QA approach for evidence extraction." in quotes
    assert "Is that placeholder misleading?" in quotes


def test_render_notion_keeps_work_limits_readable_without_routine_warning_callouts(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)

    limit = next(
        c
        for c in _of_type(children, "paragraph")
        if "Prompt-test suite not confirmed green within these turns." in _plain(c)
    )
    assert _plain(limit) == "Limits  Prompt-test suite not confirmed green within these turns."
    assert _rich_text(limit)[0]["annotations"]["bold"] is True
    assert _of_type(children, "callout") == []


def test_render_notion_minor_activity_has_a_quiet_label_and_work_item_toggles(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)

    minor = next(c for c in children if _plain(c) == "Minor activity")
    assert minor["type"] == "paragraph"
    assert _rich_text(minor)[0]["annotations"] == {"bold": True, "color": "gray"}
    minor_items = children[children.index(minor) + 1 : children.index(minor) + 3]
    assert [_plain(item) for item in minor_items] == [
        "Clarify whether the placeholder wording was misleading",
        "Indexed turn with no extractable evidence",
    ]
    assert all(item["type"] == "toggle" for item in minor_items)
    assert all(item["toggle"]["color"] == "gray" for item in minor_items)


# --- engagement + team learning ---------------------------------------------------------------


def test_render_notion_engagement_reading_dimension_and_limits(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    section = next(block for block in children if _plain(block) == "Engagement Assessment")
    start = children.index(section) + 1
    reading, limits, direction, observation = children[start : start + 4]
    assert reading["type"] == "paragraph"
    # Sources and confidence remain directly below the reading, within the same paragraph.
    assert _plain(reading).splitlines() == [
        "The user framed concrete goals and approved results.",
        "medium confidence ReportGenerator · S0001/T0001",
    ]
    assert [run["text"]["content"] for run in _citation_runs(reading)] == [
        "ReportGenerator · S0001/T0001"
    ]
    assert _code_runs(reading) == []
    # Supplied and standing limits stay adjacent to the reading as quiet, visible paragraphs.
    assert limits["type"] == "paragraph"
    assert "Offline thinking and review are not observable." in _plain(limits)
    assert "interaction precision is limited to the work-item grain" in _plain(limits)
    assert all(run["annotations"]["color"] == "gray" for run in _rich_text(limits))
    assert direction["type"] == "heading_2"
    assert _plain(direction) == "Direction"
    assert observation["type"] == "bulleted_list_item"
    assert "Asked to simplify the evidence tools and drop chain_ref." in _plain(observation)
    assert "medium confidence" in _plain(observation)
    assert [run["text"]["content"] for run in _citation_runs(observation)] == [
        "ReportGenerator · S0001/T0001"
    ]


def test_render_notion_team_learning_keeps_takeaway_and_limits_together(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    section = next(block for block in children if _plain(block) == "Team Learning")
    start = children.index(section) + 1
    reading, limits = children[start : start + 2]
    assert reading["type"] == "paragraph"
    assert _plain(reading).splitlines() == [
        "Capturing a reusable QA approach is worth promoting.",
        "low confidence ReportGenerator · S0002/T0001",
    ]
    assert "Single-day evidence; recurrence cannot be confirmed." in _plain(limits)
    assert "never a precise effort metric" in _plain(limits)
    assert all(run["annotations"]["color"] == "gray" for run in _rich_text(limits))


def test_render_notion_team_learning_pattern_keeps_its_supporting_fields_distinct(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)

    category = next(block for block in children if _plain(block) == "Reuse")
    assert category["type"] == "heading_2"
    start = children.index(category) + 1
    statement, rationale, recurrence, evidence = children[start : start + 4]
    assert all(
        block["type"] == "paragraph" for block in (statement, rationale, recurrence, evidence)
    )
    assert _plain(statement) == (
        "A three-layer QA strategy was written down as a repeatable approach. — low confidence"
    )
    runs = _rich_text(statement)
    assert runs[0]["annotations"]["bold"] is True
    assert runs[-1]["text"]["content"] == "low confidence"
    assert runs[-1]["annotations"]["color"] == "gray"
    assert _plain(rationale) == (
        "Why it matters  A reusable checklist lowers the attention cost of future QA work."
    )
    assert _plain(recurrence) == (
        "Recurrence  single sighting; likely to recur for future test design"
    )
    assert all(run["annotations"]["color"] == "gray" for run in _rich_text(recurrence))
    assert _plain(evidence) == "Evidence  ReportGenerator · S0002/T0001"
    assert [run["text"]["content"] for run in _citation_runs(evidence)] == [
        "ReportGenerator · S0002/T0001"
    ]


# --- empty report -----------------------------------------------------------------------------


def test_render_notion_empty_report_renders_three_fallbacks(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    finalize_daily_report_via_api(workspace)

    payload = json.loads(
        render_notion_artifact(workspace_path=workspace).read_text(encoding="utf-8")
    )
    fallbacks = _plain_texts(payload["children"], "bulleted_list_item")

    assert "No supported project-level work items found for this report window." in fallbacks
    assert "Insufficient supported engagement evidence for this report window." in fallbacks
    assert "No supported reusable agent-driving pattern found." in fallbacks
    assert "No supported work claims found for this report window." not in fallbacks
    assert payload["properties"]["overall_confidence"] == "n/a"
    assert "Evidence Chains" not in _plain_texts(payload["children"], "heading_1")


def test_render_notion_evidence_appendix_toggles_carry_stable_metadata(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)

    appendix = next(
        block
        for block in children
        if block["type"] == "heading_1" and block.get("_prompt_diary_evidence_appendix") is True
    )
    assert _plain(appendix) == "Evidence Chains"
    assert appendix["heading_1"]["is_toggleable"] is True
    assert "ReportGenerator" in _plain_texts([appendix], "heading_2")
    assert "S0001" not in _plain_texts([appendix], "heading_3")
    assert "S0002" not in _plain_texts([appendix], "heading_3")
    targets = [
        block for block in _iter_blocks([appendix]) if block.get("_prompt_diary_evidence_target")
    ]
    rendered_targets = [
        (target["type"], _plain(target), target["_prompt_diary_evidence_target"])
        for target in targets
    ]
    assert rendered_targets == [
        (
            "toggle",
            "S0001/T0001",
            {
                "project_key": "ReportGenerator-e6ff7eeda632",
                "session_ref": "S0001",
                "turn_ref": "T0001",
            },
        ),
        (
            "toggle",
            "S0001/T0002",
            {
                "project_key": "ReportGenerator-e6ff7eeda632",
                "session_ref": "S0001",
                "turn_ref": "T0002",
            },
        ),
        (
            "toggle",
            "S0002/T0001",
            {
                "project_key": "ReportGenerator-e6ff7eeda632",
                "session_ref": "S0002",
                "turn_ref": "T0001",
            },
        ),
    ]
    quotes = _plain_texts([appendix], "quote")
    assert "Please simplify the MCP evidence tools and drop chain_ref." in quotes
    assert "Is that placeholder misleading?" in quotes
    assert "Design the QA approach for evidence extraction." in quotes


# --- no new claims ----------------------------------------------------------------------------


def test_render_notion_no_new_claims_every_model_string_present(tmp_path: Path) -> None:
    payload, report = _render_basic(tmp_path)
    rendered = json.dumps(payload["children"], ensure_ascii=False)

    # Every claim-bearing model string appears verbatim in the rendered blocks (no escaping). The
    # converse — no extra claims — rests on the layout/render structure, as in the Markdown tests.
    claims: list[str] = []
    for project in report["projects"]:
        claims.append(project["summary"]["text"])
        for item in project["work_items"]:
            claims.append(item["title"])
            claims += [outcome["what_changed"] for outcome in item["outcomes"]]
            claims += list(item["limits"])
    engagement = report["engagement_assessment"]
    claims.append(engagement["overall_reading"]["text"])
    claims += [obs["statement"] for obs in engagement["observations"]]
    learning = report["team_learning"]
    claims.append(learning["takeaways"]["text"])
    for pattern in learning["patterns"]:
        claims += [pattern["statement"], pattern["rationale"], pattern["recurrence"]]

    for claim in claims:
        assert claim in rendered, f"model claim missing from render: {claim!r}"


def test_render_notion_every_run_is_plain_text_with_no_interpreted_field(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    # The structural safety invariant, asserted strongly: every run is a plain ``text`` run whose
    # keys are a subset of {type, text, annotations, _prompt_diary_link_target} and whose ``text``
    # has no ``link`` before publishing. No run is a ``mention`` / ``equation`` / URL-linked run in
    # the renderer artifact, so model-derived content is never an interpreted target.
    for run in _all_runs(children):
        assert run["type"] == "text"
        assert set(run).issubset({"type", "text", "annotations", "_prompt_diary_link_target"})
        assert "link" not in run["text"]


def test_render_notion_is_pure_function_of_layout(tmp_path: Path) -> None:
    _, report = _render_basic(tmp_path)

    # Two independent renders from two freshly built layouts must be byte-identical: the render
    # depends only on the layout, with no clock / fs / shared mutable state.
    assert render_notion(build_notion_layout(report)) == render_notion(build_notion_layout(report))


# --- structural injection safety --------------------------------------------------------------

# A single model string carrying a link, an image, a leading heading, and an embedded newline that
# tries to forge a section. In Notion every one of these must land verbatim in ``text.content``.
_INJECTION = "see [x](http://y) and ![img](z)\n# Injected\n## Injected section"


def test_render_notion_generic_sections_preserve_literal_group_content() -> None:
    section = Section(_INJECTION, (Toggle(_INJECTION, (Prose(_INJECTION),)),))

    payload = render_notion(_doc_with_section(section))

    assert _plain_texts(payload.children, "heading_1") == [_INJECTION]
    assert _plain_texts(payload.children, "paragraph") == [_INJECTION, _INJECTION]
    assert all("link" not in run["text"] for run in _all_runs(payload.children))


def test_render_notion_injection_string_is_literal_content_never_a_link(tmp_path: Path) -> None:
    del tmp_path
    # A work-item title (a model-derived label) carrying active Markdown renders as literal content.
    work_item = Group(_INJECTION, (Tag("completed", "disposition"), Tag("high", "confidence")))
    section = Section("Work by Project", (Group("Proj", (ListBlock("bullet", (work_item,)),)),))

    payload = render_notion(_doc_with_section(section))
    toggle = next(t for t in _of_type(payload.children, "toggle") if _INJECTION in _plain(t))

    # The whole injection string is present verbatim in the label, and no run carries a link.
    assert _INJECTION in _plain(toggle)
    assert all("link" not in run["text"] for run in _all_runs(payload.children))


# --- rich-text chunking + list styles ---------------------------------------------------------


def test_render_notion_long_content_splits_into_2000_char_runs(tmp_path: Path) -> None:
    del tmp_path
    long_text = "x" * 4500
    section = Section("Work by Project", (Group("Proj", (Prose(long_text, None),)),))

    payload = render_notion(_doc_with_section(section))
    summary = _of_type(payload.children, "paragraph")[0]
    runs = _rich_text(summary)

    expected_runs = 3  # 2000 + 2000 + 500
    assert len(runs) == expected_runs
    assert all(len(run["text"]["content"]) <= 2000 for run in runs)
    assert "".join(run["text"]["content"] for run in runs) == long_text


def test_render_notion_caps_rich_text_at_100_runs_with_a_marker(tmp_path: Path) -> None:
    del tmp_path
    # A single model string long enough to need >100 runs (>200K chars) would exceed Notion's
    # 100-object rich-text limit and be rejected. The renderer caps the array at 100 and replaces
    # the overflow with a fixed, renderer-controlled marker, so the emitted block is always valid.
    huge = "x" * (2000 * 150)  # 150 chunks, well over the 100-run cap
    section = Section("Work by Project", (Group("Proj", (Prose(huge, None),)),))

    payload = render_notion(_doc_with_section(section))
    summary = _of_type(payload.children, "paragraph")[0]
    runs = _rich_text(summary)

    assert len(runs) == 100
    assert runs[-1]["text"]["content"] == " [truncated]"


def test_render_notion_empty_project_summary_renders_an_empty_rich_text_paragraph(
    tmp_path: Path,
) -> None:
    del tmp_path
    # An empty model string (e.g. a gap-only project's empty summary) renders a valid paragraph with
    # an empty rich-text array, not a run with empty content.
    section = Section("Work by Project", (Group("Proj", (Prose("", None),)),))

    payload = render_notion(_doc_with_section(section))
    summary = _of_type(payload.children, "paragraph")[0]

    assert summary["paragraph"]["rich_text"] == []


def test_render_notion_numbered_list_renders_numbered_items(tmp_path: Path) -> None:
    del tmp_path
    section = Section(
        "Work by Project",
        (Group("Proj", (ListBlock("number", (Prose("first", None), Prose("second", None))),)),),
    )

    payload = render_notion(_doc_with_section(section))

    assert _plain_texts(payload.children, "numbered_list_item") == ["first", "second"]


# --- cross-project citation scoping (>1 project) ----------------------------------------------


def test_render_notion_citation_runs_are_plain_text_no_link(tmp_path: Path) -> None:
    del tmp_path
    citation = Citation(
        (
            {
                "project_label": "Alpha",
                "session_ref": "S0001",
                "turn_ref": "T0001",
                "scoped": True,
                "anchor": "evidence-alpha-s0001-t0001",
                "target": {
                    "project_key": "alpha",
                    "session_ref": "S0001",
                    "turn_ref": "T0001",
                },
            },
            {
                "project_label": "Beta",
                "session_ref": "S0002",
                "turn_ref": "T0002",
                "scoped": True,
                "anchor": "evidence-beta-s0002-t0002",
                "target": {
                    "project_key": "beta",
                    "session_ref": "S0002",
                    "turn_ref": "T0002",
                },
            },
        )
    )
    section = Section(
        "Engagement Assessment", (ListBlock("bullet", (Prose("Cross outcome.", citation),)),)
    )

    payload = render_notion(_doc_with_section(section))
    bullet = _of_type(payload.children, "bulleted_list_item")[0]
    citations = _citation_runs(bullet)

    # Multiple refs join with "; ", each scoped with its own project label, no link before publish.
    assert [run["text"]["content"] for run in citations] == [
        "Alpha · S0001/T0001",
        "; ",
        "Beta · S0002/T0002",
    ]
    assert _code_runs(bullet) == []
    assert all("link" not in run["text"] for run in _rich_text(bullet))
    assert all(run["annotations"]["color"] == "gray" for run in citations)


def test_render_notion_long_citation_content_is_chunked_into_text_runs(tmp_path: Path) -> None:
    del tmp_path
    # A citation whose text exceeds 2000 chars (e.g. a very long project label) must be chunked so
    # no single text run violates Notion's per-content limit.
    long_label = "L" * 4500
    citation = Citation(
        (
            {
                "project_label": long_label,
                "session_ref": "S0001",
                "turn_ref": "T0001",
                "scoped": True,
                "anchor": "evidence-alpha-s0001-t0001",
                "target": {
                    "project_key": "alpha",
                    "session_ref": "S0001",
                    "turn_ref": "T0001",
                },
            },
        )
    )
    section = Section(
        "Engagement Assessment", (ListBlock("bullet", (Prose("Outcome.", citation),)),)
    )

    payload = render_notion(_doc_with_section(section))
    bullet = _of_type(payload.children, "bulleted_list_item")[0]
    citations = _citation_runs(bullet)

    expected_text = f"{long_label} · S0001/T0001"
    assert len(citations) >= 2  # the long citation spans multiple text runs
    assert all(len(run["text"]["content"]) <= 2000 for run in citations)
    assert "".join(run["text"]["content"] for run in citations) == expected_text
    assert _code_runs(bullet) == []
