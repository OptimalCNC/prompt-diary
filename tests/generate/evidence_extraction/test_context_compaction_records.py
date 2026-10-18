from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from prompt_diary.generate.evidence_extraction.session_compaction import (
    compact_record,
    compact_record_to_json,
)
from prompt_diary.source_records import parse_codex_input
from tests.support.session_reader import (
    copy_session_reader_workspace,
    read_compact_records,
    read_full_records,
    session_file_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def _summary_message(text: str, tags: object = None) -> dict[str, object]:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {
            "content_item_kinds": ["compaction.summary"] if tags is None else tags
        },
    }


def test_readable_codex_compaction_keeps_summary_once_and_preserves_physical_source(
    tmp_path: Path,
) -> None:
    summary = "# Current work\n" + "Continue reviewing the pending patch.\n" * 4000
    source = {
        "type": "compacted",
        "payload": {
            "message": summary,
            "replacement_history": [
                _summary_message("Old human request", ["user.text"]),
                _summary_message(summary),
            ],
            "resume_metadata": {"unrelated_runtime_setting": "private runtime value"},
        },
    }
    raw = json.dumps(source)
    workspace = copy_session_reader_workspace(tmp_path)
    session_file_path(workspace).write_text("{}\n" + raw + "\n", encoding="utf-8")

    records = read_compact_records(workspace_path=workspace, start_line=2, end_line=2)

    assert len(records) == 1
    assert compact_record_to_json(records[0]) == {
        "line": 2,
        "kind": "context_compaction",
        "source_type": "compacted",
        "text": summary,
    }
    assert read_full_records(workspace_path=workspace, start_line=2, end_line=2)[0].raw_line == raw
    assert parse_codex_input(source) is None


def test_codex_tagged_replacement_summary_remains_agent_context_despite_user_role() -> None:
    source = {
        "type": "compacted",
        "payload": {
            "message": "",
            "replacement_history": [
                _summary_message("Do not replay this old request", ["user.text"]),
                _summary_message("  Retained summary\n"),
                _summary_message("Final constraint"),
            ],
        },
    }
    record = compact_record(json.dumps(source), line=23, source="codex")

    assert record is not None
    assert compact_record_to_json(record) == {
        "line": 23,
        "kind": "context_compaction",
        "source_type": "compacted",
        "text": "  Retained summary\n\nFinal constraint",
    }
    assert parse_codex_input(source) is None


@pytest.mark.parametrize(
    "tags", [[], ["compaction.summary", "user.text"], [42], "compaction.summary"]
)
def test_uncertain_replacement_metadata_does_not_invent_a_compaction_summary(tags: object) -> None:
    raw = json.dumps(
        {
            "type": "compacted",
            "payload": {"replacement_history": [_summary_message("Uncertain authorship", tags)]},
        }
    )
    record = compact_record(raw, line=4, source="codex")
    assert record is not None
    assert compact_record_to_json(record) == {
        "line": 4,
        "kind": "context_compaction",
        "source_type": "compacted",
    }


def test_encrypted_codex_compaction_marks_unavailable_summary_without_exposing_ciphertext() -> None:
    raw = json.dumps(
        {
            "type": "compacted",
            "payload": {
                "message": "",
                "replacement_history": [
                    None,
                    {"type": "compaction", "encrypted_content": "opaque ciphertext"},
                ],
            },
        }
    )
    record = compact_record(raw, line=9, source="codex")
    assert record is not None
    assert compact_record_to_json(record) == {
        "line": 9,
        "kind": "context_compaction",
        "source_type": "compacted",
        "unavailable": ["encrypted_compaction_summary"],
    }


@pytest.mark.parametrize("native", [False, True])
def test_codex_compaction_marker_does_not_invent_text_or_human_input(*, native: bool) -> None:
    source = {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {"type": "ContextCompaction", "id": "runtime-id"},
            "started_at_ms": 123,
            "completed_at_ms": 456,
        },
    }
    if not native:
        source = {"type": "event_msg", "payload": {"type": "context_compacted"}}
    record = compact_record(json.dumps(source), line=18, source="codex")
    assert record is not None
    assert compact_record_to_json(record) == {
        "line": 18,
        "kind": "context_compaction",
        "source_type": "ContextCompaction" if native else "context_compacted",
    }
    assert parse_codex_input(source) is None


@pytest.mark.parametrize("summary", [None, "# Compacted context\n" + "Keep reviewing.\n" * 2000])
def test_flagged_assistant_compaction_is_agent_context_with_available_summary(
    summary: str | None,
) -> None:
    raw = json.dumps(
        {
            "type": "response_item",
            "metadata": {"compaction_output": True},
            "payload": {"type": "message", "role": "assistant", "content": summary},
        }
    )
    record = compact_record(raw, line=19, source="codex")
    assert record is not None
    assert record.kind == "context_compaction"
    assert record.source_type == "compaction_output"
    assert record.text == summary
    assert record.unavailable == (("compaction_summary",) if summary is None else ())


def test_compaction_output_flag_does_not_expose_reasoning() -> None:
    raw = json.dumps(
        {
            "type": "response_item",
            "metadata": {"compaction_output": True},
            "payload": {
                "type": "reasoning",
                "summary": [{"text": "private reasoning"}],
                "encrypted_content": "opaque reasoning",
            },
        }
    )
    assert compact_record(raw, line=20, source="codex") is None


@pytest.mark.parametrize("flag", [False, "true", None])
def test_unconfirmed_compaction_flag_keeps_ordinary_assistant_attribution(flag: object) -> None:
    raw = json.dumps(
        {
            "type": "response_item",
            "metadata": {"compaction_output": flag},
            "payload": {"type": "message", "role": "assistant", "content": "Reviewed the patch."},
        }
    )
    record = compact_record(raw, line=21, source="codex")
    assert record is not None
    assert record.kind == "assistant_message"
    assert record.text == "Reviewed the patch."


@pytest.mark.parametrize("text", [None, "Conversation compacted."])
def test_claude_compaction_boundary_retains_event_and_available_text(text: str | None) -> None:
    raw = json.dumps(
        {
            "type": "system",
            "subtype": "compact_boundary",
            "content": text,
            "compactMetadata": {"trigger": "auto", "preTokens": 123456},
        }
    )
    record = compact_record(raw, line=12, source="claude-code")
    assert record is not None
    expected = {"line": 12, "kind": "context_compaction", "source_type": "compact_boundary"}
    assert compact_record_to_json(record) == (
        expected if text is None else expected | {"text": text}
    )


@pytest.mark.parametrize("as_blocks", [False, True])
def test_claude_summary_is_agent_context_even_with_user_role_and_meta_flag(
    *, as_blocks: bool
) -> None:
    text = "# Summary\nThe human requested review; approval is still pending.\n" * 2000
    content = [{"type": "text", "text": text}] if as_blocks else text
    raw = json.dumps(
        {
            "type": "user",
            "isCompactSummary": True,
            "isMeta": True,
            "message": {"role": "user", "content": content},
        }
    )
    record = compact_record(raw, line=31, source="claude-code")
    assert record is not None
    assert compact_record_to_json(record) == {
        "line": 31,
        "kind": "context_compaction",
        "source_type": "isCompactSummary",
        "text": text,
    }


@pytest.mark.parametrize(
    "message",
    [
        None,
        {"content": 42},
        {
            "content": [
                None,
                {"type": "thinking", "thinking": "not summary"},
                {"type": "text", "text": 42},
                {"type": "image", "data": "opaque"},
            ]
        },
    ],
)
def test_unavailable_claude_summary_does_not_expose_reasoning_or_opaque_content(
    message: object,
) -> None:
    raw = json.dumps({"type": "user", "isCompactSummary": True, "message": message})
    record = compact_record(raw, line=5, source="claude-code")
    assert record is not None
    assert compact_record_to_json(record) == {
        "line": 5,
        "kind": "context_compaction",
        "source_type": "isCompactSummary",
        "unavailable": ["compaction_summary"],
    }
