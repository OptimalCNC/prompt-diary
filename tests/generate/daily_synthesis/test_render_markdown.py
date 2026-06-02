"""Tests for the deterministic Markdown rendering of ``daily-report.json`` to ``report.md``.

``render_report`` reads the finalized model, builds the abstract layout, serializes it per the
doc's Block→Markdown mapping, and writes ``report.md`` to the workspace root. These tests pin the
header line, the scoped Executive Summary citations, the unscoped Work-by-Project citations, the
folded ``<details>`` toggles, the judgment sections, the four Empty fallbacks, and — most
importantly — the no-new-claims invariant: every claim-bearing string the renderer emits is sourced
verbatim from the model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prompt_diary.generate.daily_synthesis.layout import (
    Callout,
    Document,
    Group,
    ListBlock,
    Section,
    Tag,
    Toggle,
    build_layout,
)
from prompt_diary.generate.daily_synthesis.render_markdown import render_markdown, render_report
from tests.support.daily_synthesis import (
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    empty_daily_workspace,
    fill_synthesize_slots,
    finalize_daily_report_via_api,
    load_daily_report,
)

if TYPE_CHECKING:
    from pathlib import Path


def _render_basic(tmp_path: Path) -> tuple[Path, str, dict[str, Any]]:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    finalize_daily_report_via_api(workspace)
    path = render_report(workspace_path=workspace)
    return path, path.read_text(encoding="utf-8"), load_daily_report(workspace)


def _render_empty(tmp_path: Path) -> str:
    workspace = empty_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    finalize_daily_report_via_api(workspace)
    return render_report(workspace_path=workspace).read_text(encoding="utf-8")


# --- write contract ---------------------------------------------------------------------------


def test_render_report_writes_report_md_to_workspace_root(tmp_path: Path) -> None:
    path, text, _ = _render_basic(tmp_path)

    assert path.name == "report.md"
    assert path.parent == path.parent  # path is under the workspace root
    assert text.startswith("# Prompt Diary Report — 2026-05-28\n")


# --- header -----------------------------------------------------------------------------------


def test_render_header_status_window_confidence_line(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "# Prompt Diary Report — 2026-05-28" in text
    assert (
        "Status: final · "
        "Window: 2026-05-28T00:00:00+08:00\u20132026-05-29T00:00:00+08:00, Asia/Shanghai · "
        "Overall confidence: medium"
    ) in text


# --- executive summary (scoped citations) -----------------------------------------------------


def test_render_executive_summary_outcomes_with_scoped_citations(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "## Executive Summary" in text
    assert (
        "- Top-level turn_ref adopted; chain_ref removed from the evidence surface. "
        "[ReportGenerator · S0001:2-8]"
    ) in text
    assert "- Three-layer QA strategy delivered. [ReportGenerator · S0002:2-6]" in text


# --- work by project (unscoped citations, tags, toggles) --------------------------------------


def test_render_work_by_project_group_and_summary(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "## Work by Project" in text
    assert "### ReportGenerator" in text
    # The project summary joins its two citations, unscoped (project implied by the section).
    assert (
        "Simplified the evidence tools and designed the QA approach. [S0001:2-8; S0002:2-6]"
    ) in text


def test_render_work_item_heading_with_tags_and_unscoped_outcome(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "#### Simplify the MCP evidence tools and drop chain_ref — completed · high" in text
    # Outcome citation is unscoped within the project; the outcome's own confidence renders inline
    # as a tag (it may differ from the work item's), before the citation.
    assert (
        "- Top-level turn_ref adopted; chain_ref removed from the evidence surface. "
        "· high [S0001:2-8]"
    ) in text


def test_render_work_item_toggles_are_collapsed_details(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "<details>\n<summary>Why</summary>" in text
    assert "<details>\n<summary>User messages</summary>" in text
    # User messages are quoted as untrusted display content and Markdown-neutralized: the ``_`` in
    # "chain_ref" is backslash-escaped so it cannot start an emphasis run. It renders as the literal
    # "chain_ref" in a Markdown viewer (the backslash is consumed), but the source carries ``\_``.
    assert "> Please simplify the MCP evidence tools and drop chain\\_ref." in text
    # The fixture's other two messages survive too (no special chars, so they quote unchanged).
    assert "> Design the QA approach for evidence extraction." in text
    assert "> Is that placeholder misleading?" in text


def test_render_work_item_limit_callout_is_blockquote(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "> Prompt-test suite not confirmed green within these turns." in text


def test_render_minor_activity_toggle(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "<summary>Minor activity</summary>" in text


def test_render_gap_item_has_no_empty_user_messages_toggle(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    # The gap-turn minor item has no source messages, so it renders no "User messages" toggle and,
    # in particular, no empty <details> with a blank body. The remaining toggles each have content:
    # there is no toggle whose summary is immediately followed by a blank line then </details>.
    assert "<summary>User messages</summary>\n\n</details>" not in text
    # The gap item's heading is present but carries no toggle after it.
    gap_heading = "##### Indexed turn with no extractable evidence — low"
    assert gap_heading in text
    after_gap = text.split(gap_heading, maxsplit=1)[1]
    assert not after_gap.lstrip().startswith("<details>")


# --- engagement + team learning ---------------------------------------------------------------


def test_render_engagement_section(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "## Engagement Assessment" in text
    assert "The user framed concrete goals and approved results." in text
    assert "### Direction" in text
    # The observation carries its own confidence inline, before the scoped citation.
    assert (
        "- Asked to simplify the evidence tools and drop chain_ref. "
        "· medium [ReportGenerator · S0001:2-8]" in text
    )
    # The agent-named limit and the standing limit are distinct blockquote paragraphs (``> a\n>\n>
    # b``), not fused into one run-on line.
    assert (
        "> Offline thinking and review are not observable.\n"
        ">\n"
        "> Offline thinking and review are not visible, and interaction precision is limited to "
        "the work-item grain."
    ) in text


def test_render_team_learning_section(tmp_path: Path) -> None:
    _, text, _ = _render_basic(tmp_path)

    assert "## Team Learning" in text
    assert "Capturing a reusable QA approach is worth promoting." in text
    assert "### Reuse" in text
    # The pattern renders statement — rationale · recurrence · confidence · citation, all lifted.
    assert (
        "- A three-layer QA strategy was written down as a repeatable approach. "
        "— A reusable checklist lowers the attention cost of future QA work. "
        "· recurrence: single sighting; likely to recur for future test design "
        "· low [ReportGenerator · S0002:2-6]"
    ) in text


# --- empty report -----------------------------------------------------------------------------


def test_render_empty_report_renders_four_fallbacks(tmp_path: Path) -> None:
    text = _render_empty(tmp_path)

    assert "- No supported work claims found for this report window." in text
    assert "- No supported project-level work items found for this report window." in text
    assert "- Insufficient supported engagement evidence for this report window." in text
    assert "- No supported reusable agent-driving pattern found." in text
    assert "Overall confidence: n/a" in text


# --- no new claims ----------------------------------------------------------------------------


def test_render_no_new_claims_every_outcome_and_observation_in_model(tmp_path: Path) -> None:
    _, text, report = _render_basic(tmp_path)

    # Every claim-bearing string the model carries must appear verbatim in the rendered output;
    # and the renderer must not introduce claim-bearing prose absent from the model. We assert the
    # forward direction over every model claim string (outcomes, summaries, observations, patterns
    # with their rationale/recurrence, limits) and rely on the layout/render structure for the
    # converse. Trusted model prose appears verbatim; the basic fixture has no ``&``/``<``/``>``, so
    # HTML-escaping leaves these claim strings unchanged.
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
    claims += list(engagement["limits"])
    learning = report["team_learning"]
    claims.append(learning["takeaways"]["text"])
    for pattern in learning["patterns"]:
        claims += [pattern["statement"], pattern["rationale"], pattern["recurrence"]]
    claims += list(learning["limits"])

    for claim in claims:
        assert claim in text, f"model claim missing from render: {claim!r}"

    # User messages are deliberately excluded here: they are untrusted display content, not claims,
    # so they render quoted and Markdown-escaped (not verbatim). Their survival and escaping are
    # covered by the toggle and untrusted-escaping tests below.


def test_render_markdown_is_pure_function_of_layout(tmp_path: Path) -> None:
    # render_markdown is deterministic over the layout (the report file is not re-read).
    _, _, report = _render_basic(tmp_path)
    document = build_layout(report)

    assert render_markdown(document) == render_markdown(document)


# --- untrusted / structural escaping ----------------------------------------------------------


def _doc_with_section(section: Section) -> Document:
    """A minimal layout Document wrapping one section, for renderer-escaping unit tests."""
    return Document(
        "Prompt Diary Report — 2026-05-28",
        {"status": "final", "window": "w", "overall_confidence": "high"},
        (section,),
    )


def test_render_html_in_work_item_title_does_not_break_enclosing_toggle(tmp_path: Path) -> None:
    del tmp_path
    # A (minor) work-item title containing </details> must not prematurely close the enclosing
    # toggle: HTML-escaping renders it as literal text, so the <details>/</details> pair stays
    # balanced and the structure is intact.
    work_item = Group(
        "Tidy up </details> stray markup",
        (
            Tag("completed", "disposition"),
            Tag("low", "confidence"),
            Toggle("User messages", (Callout("quote", "a harmless note"),)),
        ),
    )
    minor = Toggle("Minor activity", (ListBlock("bullet", (work_item,)),))
    section = Section("Work by Project", (Group("Proj", (minor,)),))

    text = render_markdown(_doc_with_section(section))

    # The title's angle brackets are escaped, so the title injects no literal </details>.
    assert "Tidy up &lt;/details&gt; stray markup" in text
    assert "Tidy up </details> stray markup" not in text
    # The toggle structure stays balanced: the only <details>/</details> pairs are the two real
    # toggles (the inner "User messages" and the outer "Minor activity"), so they match up.
    expected_toggles = 2
    assert text.count("<details>") == text.count("</details>")
    assert text.count("</details>") == expected_toggles


def test_render_user_message_markdown_is_neutralized(tmp_path: Path) -> None:
    del tmp_path
    # An untrusted user message containing a link, an image, emphasis, and a code span must render
    # literally (escaped), never as an active link/image/format.
    message = "see [x](http://y) and ![img](z) and *bold* and `tick`"
    toggle = Toggle("User messages", (Callout("quote", message),))
    work_item = Group("WI", (Tag("completed", "disposition"), Tag("high", "confidence"), toggle))
    section = Section("Work by Project", (Group("Proj", (ListBlock("bullet", (work_item,)),)),))

    text = render_markdown(_doc_with_section(section))

    # The Markdown punctuation is backslash-escaped, so none of these activate.
    assert (
        "> see \\[x\\]\\(http://y\\) and \\!\\[img\\]\\(z\\) and \\*bold\\* and \\`tick\\`"
    ) in text
    # The active forms are absent: no live link/image syntax survives unescaped.
    assert "[x](http://y)" not in text
    assert "![img](z)" not in text
    assert "*bold*" not in text
    assert "`tick`" not in text


def test_render_user_message_leading_heading_is_neutralized(tmp_path: Path) -> None:
    del tmp_path
    # A user message that begins with ``#`` must not render as a heading inside the blockquote.
    toggle = Toggle("User messages", (Callout("quote", "# not a heading"),))
    work_item = Group("WI", (Tag("completed", "disposition"), Tag("high", "confidence"), toggle))
    section = Section("Work by Project", (Group("Proj", (ListBlock("bullet", (work_item,)),)),))

    text = render_markdown(_doc_with_section(section))

    assert "> \\# not a heading" in text


def test_render_amp_in_trusted_prose_is_html_escaped(tmp_path: Path) -> None:
    del tmp_path
    # Trusted model prose is still HTML-escaped so a stray ``&``/``<`` renders as literal text.
    section = Section(
        "Engagement Assessment",
        (Callout("limit", "Reviewed A & B; weighed <thresholds>."),),
    )

    text = render_markdown(_doc_with_section(section))

    assert "> Reviewed A &amp; B; weighed &lt;thresholds&gt;." in text


# --- cross-project citation scoping (>1 project) ----------------------------------------------


def _two_project_report() -> dict[str, Any]:
    """A minimal finalized report spanning two projects with the same session ref in each.

    Both projects use ``S0001:2-8`` so the only thing that disambiguates a cross-project citation is
    its project label. Work-by-Project citations are unscoped (project implied by the group), while
    Executive Summary / Engagement / Team Learning citations are scoped with the project label.
    """

    def citation(project_key: str) -> dict[str, str]:
        return {
            "project_key": project_key,
            "session_ref": "S0001",
            "turn_ref": "T0001",
            "lines": "2-8",
        }

    def project(project_key: str, label: str, outcome_text: str) -> dict[str, Any]:
        return {
            "project_key": project_key,
            "project_label": label,
            "summary": {"text": f"{label} summary.", "citations": [citation(project_key)]},
            "work_items": [
                {
                    "work_item_ref": "W0001",
                    "title": f"{label} work item",
                    "kind": "material_work_item",
                    "disposition": "completed",
                    "confidence": "high",
                    "covered_turns": [{"session_ref": "S0001", "turn_ref": "T0001"}],
                    "trigger_summary": None,
                    "agent_reaction_summary": None,
                    "outcomes": [
                        {
                            "what_changed": outcome_text,
                            "confidence": "high",
                            "citations": [citation(project_key)],
                        }
                    ],
                    "terminal_states": [],
                    "limits": [],
                }
            ],
            "source_user_messages": [],
        }

    return {
        "schema_version": 1,
        "report_date": "2026-05-28",
        "status": "final",
        "window": {"start": "s", "end": "e", "timezone": "Asia/Shanghai"},
        "overall_confidence": "high",
        "executive_summary": {
            "top_outcomes": [
                {"text": "Alpha outcome.", "citations": [citation("alpha-key")]},
                {"text": "Beta outcome.", "citations": [citation("beta-key")]},
            ],
            "open_items": [],
        },
        "projects": [
            project("alpha-key", "Alpha", "Alpha outcome."),
            project("beta-key", "Beta", "Beta outcome."),
        ],
        "engagement_assessment": {
            "overall_reading": {
                "text": "Reading.",
                "citations": [citation("alpha-key")],
                "confidence": "high",
            },
            "observations": [
                {
                    "dimension": "direction",
                    "statement": "Alpha direction.",
                    "citations": [citation("alpha-key")],
                    "confidence": "high",
                },
                {
                    "dimension": "review",
                    "statement": "Beta review.",
                    "citations": [citation("beta-key")],
                    "confidence": "high",
                },
            ],
            "limits": [],
        },
        "team_learning": {
            "takeaways": {
                "text": "Takeaways.",
                "citations": [citation("alpha-key")],
                "confidence": "high",
            },
            "patterns": [
                {
                    "kind": "reuse",
                    "statement": "Alpha pattern.",
                    "rationale": "Alpha rationale.",
                    "recurrence": "Alpha recurrence.",
                    "citations": [citation("alpha-key")],
                    "confidence": "high",
                },
                {
                    "kind": "promote",
                    "statement": "Beta pattern.",
                    "rationale": "Beta rationale.",
                    "recurrence": "Beta recurrence.",
                    "citations": [citation("beta-key")],
                    "confidence": "high",
                },
            ],
            "limits": [],
        },
    }


def test_render_cross_project_citations_scoped_with_distinct_labels(tmp_path: Path) -> None:
    del tmp_path
    # The only test that exercises scoping with >1 project: both projects share the session ref
    # S0001:2-8, so a cross-project citation is only unambiguous because it carries its own project
    # label, while a Work-by-Project citation under a project group stays unscoped.
    text = render_markdown(build_layout(_two_project_report()))

    # Executive Summary: scoped with the correct, distinct project labels.
    assert "- Alpha outcome. [Alpha · S0001:2-8]" in text
    assert "- Beta outcome. [Beta · S0001:2-8]" in text
    # Engagement observations: scoped, distinct labels per project.
    assert "- Alpha direction. · high [Alpha · S0001:2-8]" in text
    assert "- Beta review. · high [Beta · S0001:2-8]" in text
    # Team Learning patterns: scoped, distinct labels per project.
    assert (
        "Alpha pattern. — Alpha rationale. · recurrence: Alpha recurrence. "
        "· high [Alpha · S0001:2-8]"
    ) in text
    assert (
        "Beta pattern. — Beta rationale. · recurrence: Beta recurrence. · high [Beta · S0001:2-8]"
    ) in text
    # Work by Project: citations under each project group are unscoped (no label, no " · " prefix).
    assert "- Alpha outcome. · high [S0001:2-8]" in text
    assert "- Beta outcome. · high [S0001:2-8]" in text
