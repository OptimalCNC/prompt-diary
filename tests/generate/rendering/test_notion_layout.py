"""Semantic fidelity through the Notion-only projection and renderer."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from prompt_diary.generate.rendering.notion_layout import (
    build_notion_layout,
    with_notion_project_labels,
)
from prompt_diary.generate.rendering.render_notion import render_notion


def _plain(block: dict[str, Any]) -> str:
    return "".join(run["text"]["content"] for run in block[block["type"]].get("rich_text", []))


def _work_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "projects": [{"project_key": "one", "project_label": "Project", "work_items": items}],
    }


@pytest.mark.parametrize(
    ("trigger", "response", "expected"),
    [
        (None, "Agent only.", ["Agent response  Agent only."]),
        ("Human only.", None, ["Human direction  Human only."]),
        ("Human.", "Agent.", ["Human direction  Human.", "Agent response  Agent."]),
        ("", "", []),
    ],
)
def test_optional_work_summaries_keep_their_actual_roles(
    trigger: str | None, response: str | None, expected: list[str]
) -> None:
    report = _work_report(
        [
            {
                "kind": "material_work_item",
                "title": "Work",
                "trigger_summary": trigger,
                "agent_reaction_summary": response,
            }
        ]
    )

    payload = render_notion(build_notion_layout(report))
    work = next(block for block in payload.children if block["type"] == "toggle")

    assert [_plain(block) for block in work["toggle"]["children"]] == expected


def test_terminal_state_is_a_recorded_result_and_does_not_become_an_outcome() -> None:
    report = _work_report(
        [
            {
                "kind": "minor",
                "title": "Attempt",
                "disposition": None,
                "terminal_states": [{"summary": "Waiting for input.", "confidence": "high"}],
            }
        ]
    )

    payload = render_notion(build_notion_layout(report))
    work = next(block for block in payload.children if block["type"] == "toggle")
    body = work["toggle"]["children"]

    assert _plain(work) == "Attempt"
    assert _plain(body[0]) == "Recorded result"
    assert _plain(body[1]).startswith("Waiting for input.")
    assert work["toggle"]["color"] == "gray"


def test_pattern_fields_with_prose_separators_stay_separate_and_verbatim() -> None:
    report = {
        "schema_version": 2,
        "team_learning": {
            "takeaways": {"text": "Learn from collaboration."},
            "patterns": [
                {
                    "kind": kind,
                    "statement": f"{kind} — preserve this · recurrence: literal text",
                    "rationale": "Why — this delimiter belongs to the rationale.",
                    "recurrence": "Repeated · recurrence: in the recorded evidence.",
                }
                for kind in ("reuse", "promote", "avoid")
            ],
        },
    }

    payload = render_notion(build_notion_layout(report))
    headings = [block for block in payload.children if block["type"] == "heading_2"]

    assert [_plain(heading) for heading in headings] == ["Promote", "Avoid", "Reuse"]
    for heading in headings:
        start = payload.children.index(heading) + 1
        body = payload.children[start : start + 3]
        assert [_plain(block) for block in body] == [
            f"{_plain(heading).lower()} — preserve this · recurrence: literal text",
            "Why it matters  Why — this delimiter belongs to the rationale.",
            "Recurrence  Repeated · recurrence: in the recorded evidence.",
        ]


def test_duplicate_labels_and_titles_keep_project_and_item_associations() -> None:
    projects: list[dict[str, Any]] = [
        {
            "project_key": key,
            "project_label": "Same project",
            "work_items": [
                {"kind": kind, "title": "Same title", "agent_reaction_summary": f"{key} {kind}"}
                for kind in ("minor", "material_work_item")
            ],
        }
        for key in ("one", "two")
    ]
    projects.insert(1, {"project_key": "empty", "project_label": "No work", "work_items": []})
    report = {
        "schema_version": 2,
        "projects": projects,
        "engagement_assessment": {
            "overall_reading": {
                "text": "Reading.",
                "citations": [
                    {"project_key": key, "session_ref": "S1", "turn_ref": "T1"}
                    for key in ("one", "two")
                ],
            }
        },
    }
    before = deepcopy(report)

    labeled = with_notion_project_labels(report)
    payload = render_notion(build_notion_layout(labeled))

    assert report == before
    assert labeled["projects"][1] is projects[1]
    assert [_plain(block) for block in payload.children if block["type"] == "heading_2"] == [
        "Same project ·1",
        "Same project ·2",
    ]
    work = [block for block in payload.children if block["type"] == "toggle"]
    assert [_plain(block["toggle"]["children"][0]) for block in work] == [
        "Agent response  one material_work_item",
        "Agent response  one minor",
        "Agent response  two material_work_item",
        "Agent response  two minor",
    ]
    reading = next(block for block in payload.children if _plain(block).startswith("Reading.\n"))
    assert "Same project ·1 · S1/T1; Same project ·2 · S1/T1" in _plain(reading)
