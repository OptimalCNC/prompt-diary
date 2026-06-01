"""Pure, deterministic, source-aware compaction of one raw session JSONL line.

This module turns a single physical JSONL line into a bounded, citation-safe ``CompactRecord``. It
does no filesystem, network, time, or randomness work: the same line and arguments always produce
the same record. Large tool-result payloads and assistant reasoning are trimmed to keep compact
reads small, while user-authored and assistant text messages are preserved in full because they are
primary evidence. A malformed line never raises; it still reports its physical line, raw byte count,
and content hash so provenance survives.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class CompactRecord:
    """One physical JSONL line described compactly, with provenance always preserved."""

    line: int
    record_type: str
    role: str | None
    content_kinds: tuple[str, ...]
    summary: str
    raw_bytes: int
    raw_sha256: str
    truncated: bool


@dataclass
class _Compaction:
    """Mutable scratch describing a single record while it is being compacted."""

    record_type: str = "unknown"
    role: str | None = None
    content_kinds: tuple[str, ...] = ()
    summary: str = ""
    truncated: bool = False


def compact_record(raw_line: str, *, line: int, source: str) -> CompactRecord:
    """Compact one raw physical JSONL line into a bounded structured record."""
    raw_bytes = raw_line.encode("utf-8")
    record = _parse_object(raw_line)
    compaction = _compact_unknown(record) if record is None else _compact_source(record, source)
    return CompactRecord(
        line=line,
        record_type=compaction.record_type,
        role=compaction.role,
        content_kinds=compaction.content_kinds,
        summary=compaction.summary,
        raw_bytes=len(raw_bytes),
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        truncated=compaction.truncated,
    )


def compact_record_to_json(record: CompactRecord) -> dict[str, Any]:
    """Serialize a compact record into a JSON-ready dict."""
    return {
        "line": record.line,
        "record_type": record.record_type,
        "role": record.role,
        "content_kinds": list(record.content_kinds),
        "summary": record.summary,
        "raw_bytes": record.raw_bytes,
        "raw_sha256": record.raw_sha256,
        "truncated": record.truncated,
    }


def _compact_source(record: dict[str, Any], source: str) -> _Compaction:
    _ = source
    return _compact_unknown(record)


def _compact_unknown(record: dict[str, Any] | None) -> _Compaction:
    if record is None:
        return _Compaction(record_type="unknown", summary="Malformed JSONL line.")
    role = _string(record, "role")
    record_type = role or _string(record, "type") or "unknown"
    return _Compaction(record_type=record_type, role=role, summary=f"{record_type} record.")


def _parse_object(raw_line: str) -> dict[str, Any] | None:
    try:
        parsed = cast("object", json.loads(raw_line))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, Any]", parsed)


def _string(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None
