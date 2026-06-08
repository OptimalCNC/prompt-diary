"""Tests for the deterministic Notion rendering of ``daily-report.json`` to ``report.notion.json``.

``render_notion_artifact`` reads the finalized model, builds the abstract layout, serializes it per
the doc's Block→Notion mapping, and writes the page payload (title, metadata properties, body block
children) to ``report.notion.json``. These tests pin the title/properties, the heading_2 sections,
the project heading_3, the work-item ``toggle`` (the idiomatic Notion form for a titled cluster),
the plain nested Why / User-messages sections, the quote vs. callout split, the inline-code
citations, the four Empty fallbacks, and the two invariants that make Notion rendering faithful and
safe:

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
    build_layout,
)
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


def _plain_texts(blocks: Iterable[dict[str, Any]], block_type: str) -> list[str]:
    return [_plain(block) for block in _of_type(blocks, block_type)]


def _children_of(toggle: dict[str, Any]) -> list[dict[str, Any]]:
    return toggle["toggle"]["children"]


def _doc_with_section(section: Section) -> Document:
    """A minimal layout Document wrapping one section, for renderer unit tests."""
    return Document(
        "Prompt Diary Report — 2026-05-28",
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

    assert payload["title"] == "Prompt Diary Report — 2026-05-28"
    assert payload["properties"] == {
        # ``report_date`` is added to the layout properties for the Notion Date column; the Markdown
        # header (which reads only status/window/overall_confidence) is unaffected.
        "report_date": "2026-05-28",
        "status": "final",
        # The window range uses an en dash (U+2013), built here as an escape (as in test_layout).
        "window": "2026-05-28T00:00:00+08:00\u20132026-05-29T00:00:00+08:00, Asia/Shanghai",
        "overall_confidence": "medium",
    }


def test_render_notion_sections_are_heading_2(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    assert _plain_texts(children, "heading_2") == [
        "Executive Summary",
        "Work by Project",
        "Engagement Assessment",
        "Team Learning",
    ]


# --- executive summary (scoped, inline-code citations) ----------------------------------------


def test_render_notion_executive_summary_bullets_with_scoped_code_citations(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)
    bullets = _of_type(children, "bulleted_list_item")

    # The claim text carries no escaping: the intraword ``_`` in turn_ref/chain_ref is literal.
    top = next(
        b
        for b in bullets
        if "Top-level turn_ref adopted; chain_ref removed from the evidence surface." in _plain(b)
    )
    # The citation is an inline-code run, scoped with the project label, and carries no link.
    code = _code_runs(top)
    assert [run["text"]["content"] for run in code] == ["ReportGenerator · S0001:2-8"]
    assert all("link" not in run["text"] for run in _rich_text(top))


# --- work by project (project heading_3, work-item toggle) ------------------------------------


def test_render_notion_project_is_heading_3_with_summary_paragraph(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    assert "ReportGenerator" in _plain_texts(children, "heading_3")
    summary = next(
        p
        for p in _of_type(children, "paragraph")
        if "Simplified the evidence tools and designed the QA approach." in _plain(p)
    )
    # The project summary's two citations are unscoped (project implied) and joined in one code run.
    assert [run["text"]["content"] for run in _code_runs(summary)] == ["S0001:2-8; S0002:2-6"]


def test_render_notion_work_item_is_a_toggle_with_tags_in_label(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)
    toggles = _of_type(children, "toggle")

    labels = [_plain(t) for t in toggles]
    # A work item is a collapsible toggle, not a heading; its disposition + confidence ride in the
    # label, and the title is literal (no Markdown escaping of ``chain_ref``).
    assert "Simplify the MCP evidence tools and drop chain_ref — completed · high" in labels


def test_render_notion_work_item_toggle_nests_plain_why_messages_outcome_limit(
    tmp_path: Path,
) -> None:
    children = _basic_children(tmp_path)
    work_item = next(
        t
        for t in _of_type(children, "toggle")
        if _plain(t).startswith("Simplify the MCP evidence tools and drop chain_ref")
    )
    nested = _children_of(work_item)

    # The nested Why and User-messages sections are plain blocks, not additional toggles; the
    # outcome bullet and the limit callout all still live inside the work-item toggle.
    nested_toggle_labels = [_plain(t) for t in _of_type(nested, "toggle")]
    assert "Why" not in nested_toggle_labels
    assert "User messages" not in nested_toggle_labels
    nested_paragraphs = _plain_texts(nested, "paragraph")
    assert "Why" in nested_paragraphs
    assert "User messages" in nested_paragraphs
    outcome = next(
        b
        for b in _of_type(nested, "bulleted_list_item")
        if "Top-level turn_ref adopted; chain_ref removed from the evidence surface." in _plain(b)
    )
    assert [run["text"]["content"] for run in _code_runs(outcome)] == ["S0001:2-8"]
    # The outcome's own confidence renders inline as a ``· high`` run before the citation.
    assert " · high" in _plain(outcome)


def test_render_notion_user_messages_are_verbatim_quote_blocks(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)
    quotes = _plain_texts(children, "quote")

    # Untrusted user messages render as quote blocks, verbatim — the intraword ``_`` is NOT escaped
    # (Notion stores content literally), unlike the Markdown view's ``chain\_ref``.
    assert "Please simplify the MCP evidence tools and drop chain_ref." in quotes
    assert "Design the QA approach for evidence extraction." in quotes
    assert "Is that placeholder misleading?" in quotes


def test_render_notion_limit_is_a_callout_with_warning_icon(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)
    callouts = _of_type(children, "callout")

    limit = next(
        c
        for c in callouts
        if "Prompt-test suite not confirmed green within these turns." in _plain(c)
    )
    assert limit["callout"]["icon"] == {"emoji": "⚠️"}


def test_render_notion_minor_activity_toggle_holds_work_item_toggles(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    # Minor activity is a plain label; the minor work items remain work-item toggles.
    assert "Minor activity" in _plain_texts(children, "paragraph")
    assert all(_plain(t) != "Minor activity" for t in _of_type(children, "toggle"))


# --- engagement + team learning ---------------------------------------------------------------


def test_render_notion_engagement_reading_dimension_and_limits(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    reading = next(
        p
        for p in _of_type(children, "paragraph")
        if "The user framed concrete goals and approved results." in _plain(p)
    )
    # The lead reading carries its own confidence inline and a scoped citation.
    assert " · medium" in _plain(reading)
    assert [run["text"]["content"] for run in _code_runs(reading)] == [
        "ReportGenerator · S0001:2-8"
    ]
    assert "Direction" in _plain_texts(children, "heading_3")
    # The standing engagement limit always renders, as a callout.
    limits = _of_type(children, "callout")
    assert any(
        "interaction precision is limited to the work-item grain" in _plain(c) for c in limits
    )


def test_render_notion_team_learning_pattern_text(tmp_path: Path) -> None:
    children = _basic_children(tmp_path)

    assert "Reuse" in _plain_texts(children, "heading_3")
    pattern = next(
        b
        for b in _of_type(children, "bulleted_list_item")
        if "A three-layer QA strategy was written down as a repeatable approach." in _plain(b)
    )
    text = _plain(pattern)
    # statement — rationale · recurrence: ... · confidence, all lifted from the model.
    assert "— A reusable checklist lowers the attention cost of future QA work." in text
    assert "· recurrence: single sighting; likely to recur for future test design" in text
    assert " · low" in text


# --- empty report -----------------------------------------------------------------------------


def test_render_notion_empty_report_renders_four_fallbacks(tmp_path: Path) -> None:
    workspace = empty_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    finalize_daily_report_via_api(workspace)

    payload = json.loads(
        render_notion_artifact(workspace_path=workspace).read_text(encoding="utf-8")
    )
    fallbacks = _plain_texts(payload["children"], "bulleted_list_item")

    assert "No supported work claims found for this report window." in fallbacks
    assert "No supported project-level work items found for this report window." in fallbacks
    assert "Insufficient supported engagement evidence for this report window." in fallbacks
    assert "No supported reusable agent-driving pattern found." in fallbacks
    assert payload["properties"]["overall_confidence"] == "n/a"


# --- no new claims ----------------------------------------------------------------------------


def test_render_notion_no_new_claims_every_model_string_present(tmp_path: Path) -> None:
    payload, report = _render_basic(tmp_path)
    rendered = json.dumps(payload["children"], ensure_ascii=False)

    # Every claim-bearing model string appears verbatim in the rendered blocks (no escaping). The
    # converse — no extra claims — rests on the layout/render structure, as in the Markdown tests.
    claims: list[str] = []
    summary = report["executive_summary"]
    claims += [entry["text"] for entry in summary["top_outcomes"]]
    claims += [entry["text"] for entry in summary["open_items"]]
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
    # keys are a subset of {type, text, annotations} and whose ``text`` has no ``link``. No run is a
    # ``mention`` / ``equation`` / linked run, so nothing model-derived is an interpreted target.
    for run in _all_runs(children):
        assert run["type"] == "text"
        assert set(run).issubset({"type", "text", "annotations"})
        assert "link" not in run["text"]


def test_render_notion_is_pure_function_of_layout(tmp_path: Path) -> None:
    _, report = _render_basic(tmp_path)

    # Two independent renders from two freshly built layouts must be byte-identical: the render
    # depends only on the layout, with no clock / fs / shared mutable state.
    assert render_notion(build_layout(report)) == render_notion(build_layout(report))


# --- structural injection safety --------------------------------------------------------------

# A single model string carrying a link, an image, a leading heading, and an embedded newline that
# tries to forge a section. In Notion every one of these must land verbatim in ``text.content``.
_INJECTION = "see [x](http://y) and ![img](z)\n# Injected\n## Injected section"


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
    paragraph = next(p for p in _of_type(payload.children, "paragraph") if _plain(p))
    runs = _rich_text(paragraph)

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
    paragraph = next(p for p in _of_type(payload.children, "paragraph") if _plain(p))
    runs = _rich_text(paragraph)

    assert len(runs) == 100
    assert runs[-1]["text"]["content"] == " [truncated]"


def test_render_notion_empty_prose_renders_an_empty_rich_text_paragraph(tmp_path: Path) -> None:
    del tmp_path
    # An empty model string (e.g. a gap-only project's empty summary) renders a valid paragraph with
    # an empty rich-text array, not a run with empty content.
    section = Section("Work by Project", (Group("Proj", (Prose("", None),)),))

    payload = render_notion(_doc_with_section(section))
    paragraph = _of_type(payload.children, "paragraph")[0]

    assert paragraph["paragraph"]["rich_text"] == []


def test_render_notion_numbered_list_renders_numbered_items(tmp_path: Path) -> None:
    del tmp_path
    section = Section(
        "Work by Project",
        (Group("Proj", (ListBlock("number", (Prose("first", None), Prose("second", None))),)),),
    )

    payload = render_notion(_doc_with_section(section))

    assert _plain_texts(payload.children, "numbered_list_item") == ["first", "second"]


# --- cross-project citation scoping (>1 project) ----------------------------------------------


def test_render_notion_citation_runs_are_inline_code_no_link(tmp_path: Path) -> None:
    del tmp_path
    citation = Citation(
        (
            {"project_label": "Alpha", "session_ref": "S0001", "lines": "2-8", "scoped": True},
            {"project_label": "Beta", "session_ref": "S0002", "lines": "3-9", "scoped": True},
        )
    )
    section = Section(
        "Executive Summary", (ListBlock("bullet", (Prose("Cross outcome.", citation),)),)
    )

    payload = render_notion(_doc_with_section(section))
    bullet = _of_type(payload.children, "bulleted_list_item")[0]
    code = _code_runs(bullet)

    # Multiple refs join with "; " in one code run, each scoped with its own project label, no link.
    assert [run["text"]["content"] for run in code] == ["Alpha · S0001:2-8; Beta · S0002:3-9"]
    assert all("link" not in run["text"] for run in _rich_text(bullet))


def test_render_notion_long_citation_content_is_chunked_into_code_runs(tmp_path: Path) -> None:
    del tmp_path
    # A citation whose text exceeds 2000 chars (e.g. a very long project label) must be chunked so
    # no single code run violates Notion's per-content limit; adjacent code runs stay code spans.
    long_label = "L" * 4500
    citation = Citation(
        ({"project_label": long_label, "session_ref": "S0001", "lines": "2-8", "scoped": True},)
    )
    section = Section("Executive Summary", (ListBlock("bullet", (Prose("Outcome.", citation),)),))

    payload = render_notion(_doc_with_section(section))
    bullet = _of_type(payload.children, "bulleted_list_item")[0]
    code = _code_runs(bullet)

    expected_text = f"{long_label} · S0001:2-8"
    assert len(code) >= 2  # the long citation spans multiple code runs
    assert all(len(run["text"]["content"]) <= 2000 for run in code)
    assert "".join(run["text"]["content"] for run in code) == expected_text
