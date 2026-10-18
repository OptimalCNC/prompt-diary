"""Original human text survives the deterministic write and report projection path."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.generate.daily_synthesis.build import build_daily_report
from prompt_diary.generate.daily_synthesis.inputs import build_report_inputs
from prompt_diary.generate.rendering.render_markdown import render_report
from prompt_diary.generate.rendering.render_notion import render_notion_artifact
from tests.support.daily_synthesis import (
    call_write_engagement_api,
    call_write_project_summary_api,
    call_write_report_title_api,
    call_write_team_learning_api,
    cross_citation,
    finalize_daily_report_via_api,
    load_daily_report,
    project_citation,
)
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    assert_appended_result,
    call_write_evidence_api,
    copy_basic_evidence_workspace,
    load_evidence_card,
    valid_no_material_chain,
)
from tests.support.project_synthesis import (
    call_write_work_item_api,
    load_project_synthesis,
    valid_material_work_item,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path


def test_original_human_text_survives_evidence_and_report_projections(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    session_path = workspace / "projects" / PROJECT_KEY / "sessions" / "codex" / "session-001.jsonl"
    long_text = " \t" + "原文 👩🏽‍💻 café " * 250 + "  \t\n"
    code_text = (
        ' \t\n```python\napi_key = "FAKE_TEST_CREDENTIAL_7f2a"\nprint("原文 👩🏽‍💻")\n```\n \t'
    )
    skill_text = "Generated skill instructions must never become a human quotation."
    lines = session_path.read_text(encoding="utf-8").splitlines()
    lines[1] = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": long_text},
                    {"type": "input_text", "text": skill_text},
                    {"type": "input_text", "text": ""},
                    {"type": "input_text", "text": code_text},
                ],
                "internal_chat_message_metadata_passthrough": {
                    "content_item_kinds": [
                        "user.text",
                        "skills.selected_skill_instructions",
                        "user.text",
                        "user.text",
                    ]
                },
            },
        },
        ensure_ascii=False,
    )
    session_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The Agent submits citations only; every stored byte of quoted text comes from the tool.
    assert_appended_result(call_write_evidence_api(workspace_path=workspace), turn_ref="T0001")
    assert_appended_result(
        call_write_evidence_api(workspace_path=workspace, evidence_chain=valid_no_material_chain()),
        turn_ref="T0002",
    )
    card = load_evidence_card(workspace)
    assert card["evidence_chains"][0]["trigger"] == {
        "citations": [{"lines": "2-2"}],
        "quoted_messages": [{"text": long_text}, {"text": ""}, {"text": code_text}],
    }

    result = call_write_work_item_api(
        workspace_path=workspace, work_item=valid_material_work_item()
    )
    assert result.status == "appended"
    expected_messages = [
        {"session_ref": "S0001", "turn_ref": "T0001", "messages": [long_text, "", code_text]},
        {"session_ref": "S0001", "turn_ref": "T0002", "messages": ["continue"]},
    ]
    assert load_project_synthesis(workspace)["source_user_messages"] == expected_messages
    report = build_daily_report(workspace_path=workspace)
    assert report["projects"][0]["source_user_messages"] == expected_messages
    inputs = build_report_inputs(workspace_path=workspace)
    assert long_text in inputs.source_user_messages
    assert code_text in inputs.source_user_messages
    assert skill_text not in inputs.source_user_messages

    _complete_daily_report(workspace)
    assert load_daily_report(workspace)["projects"][0]["source_user_messages"] == expected_messages
    markdown = render_report(workspace_path=workspace).read_text(encoding="utf-8")
    assert "> " + long_text[:-1] + "\n>" in markdown
    assert (
        ">  \t\n"
        "> \\`\\`\\`python\n"
        '> api\\_key = "FAKE\\_TEST\\_CREDENTIAL\\_7f2a"\n'
        '> print\\("原文 👩🏽‍💻"\\)\n'
        "> \\`\\`\\`\n"
        ">  \t"
    ) in markdown
    assert skill_text not in markdown

    payload = cast(
        "dict[str, Any]",
        json.loads(render_notion_artifact(workspace_path=workspace).read_text(encoding="utf-8")),
    )
    quote_blocks = [
        block for block in _iter_notion_blocks(payload["children"]) if block["type"] == "quote"
    ]
    quotes = [
        "".join(run["text"]["content"] for run in block["quote"]["rich_text"])
        for block in quote_blocks
    ]
    assert long_text in quotes
    assert code_text in quotes
    assert skill_text not in quotes
    # The long message crosses Notion's per-run limit while remaining complete in the view.
    assert any(
        len(block["quote"]["rich_text"]) > 1
        for block, text in zip(quote_blocks, quotes, strict=True)
        if text == long_text
    )


def _complete_daily_report(workspace: Path) -> None:
    citation = cross_citation("S0001", "T0001")
    assert (
        call_write_project_summary_api(
            workspace_path=workspace,
            summary={
                "text": "Updated the evidence contract.",
                "citations": [project_citation("S0001", "T0001")],
            },
        ).status
        == "written"
    )
    assert (
        call_write_report_title_api(
            workspace_path=workspace,
            title={"text": "Evidence Contract Update", "citations": [citation]},
        ).status
        == "written"
    )
    assert (
        call_write_engagement_api(
            workspace_path=workspace,
            overall_reading={
                "text": "The user gave a detailed request.",
                "citations": [citation],
                "confidence": "high",
            },
            observations=[],
            limits=[],
        ).status
        == "written"
    )
    assert (
        call_write_team_learning_api(
            workspace_path=workspace,
            takeaways={
                "text": "Keep source text available for review.",
                "citations": [citation],
                "confidence": "high",
            },
            patterns=[],
            limits=[],
        ).status
        == "written"
    )
    assert finalize_daily_report_via_api(workspace).status == "finalized"


def _iter_notion_blocks(blocks: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for block in blocks:
        yield block
        yield from _iter_notion_blocks(block[block["type"]].get("children", []))
