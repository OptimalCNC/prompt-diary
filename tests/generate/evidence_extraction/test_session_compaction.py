from __future__ import annotations

import hashlib
import json

from prompt_diary.generate.evidence_extraction.session_compaction import (
    compact_record,
    compact_record_to_json,
)


def test_records_raw_bytes_and_sha256_for_a_known_line() -> None:
    raw = '{"role":"user","content":"hi"}'

    record = compact_record(raw, line=7, source="codex")

    assert record.line == 7
    assert record.raw_bytes == len(raw.encode("utf-8"))
    assert record.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_simplified_role_shape_falls_back_to_top_level_role() -> None:
    raw = '{"role":"user","content":"Please update the docs."}'

    record = compact_record(raw, line=2, source="codex")

    assert record.record_type == "user"
    assert record.role == "user"
    assert record.truncated is False


def test_non_json_line_falls_back_without_raising() -> None:
    raw = "this is not json"

    record = compact_record(raw, line=4, source="claude-code")

    assert record.record_type == "unknown"
    assert record.role is None
    assert record.summary == "Malformed JSONL line."
    assert record.raw_bytes == len(raw.encode("utf-8"))
    assert record.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_json_array_line_is_treated_as_malformed() -> None:
    record = compact_record("[1, 2, 3]", line=9, source="codex")

    assert record.record_type == "unknown"
    assert record.summary == "Malformed JSONL line."


def test_simplified_type_only_shape_derives_record_type_from_type() -> None:
    raw = '{"type":"session_metadata","content":"Prepared workspace fixture."}'

    record = compact_record(raw, line=1, source="codex")

    assert record.record_type == "session_metadata"
    assert record.role is None
    assert record.summary == "session_metadata record."


def test_to_json_round_trips_through_json_dumps() -> None:
    raw = '{"role":"user","content":"hi"}'

    payload = compact_record_to_json(compact_record(raw, line=3, source="codex"))

    assert json.loads(json.dumps(payload)) == payload
    assert payload["line"] == 3
    assert payload["record_type"] == "user"
    assert payload["content_kinds"] == []
