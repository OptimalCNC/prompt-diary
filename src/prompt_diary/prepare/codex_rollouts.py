"""Resolve native Codex rollout deltas into one prepared linear transcript."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.models import JsonObject

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ROLLOUT_NAME = re.compile(
    rf"rollout-(?P<timestamp>\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}})-"
    rf"(?P<thread>{_UUID})(?:_(?P<rollout>{_UUID}))?\.jsonl"
)


@dataclass(frozen=True)
class SourceSegment:
    """Map a contiguous prepared line range to its original source."""

    source_path: Path
    source_start_line: int
    prepared_start_line: int
    line_count: int
    source_checksum_sha256: str


@dataclass(frozen=True)
class RolloutTranscript:
    """Prepared transcript and the boundary of its own rollout's records."""

    content: bytes
    source_checksum_sha256: str
    rollout_id: str | None
    owned_start_line: int = 1
    segments: tuple[SourceSegment, ...] = ()


@dataclass(frozen=True)
class _RolloutName:
    thread_id: str
    rollout_id: str


@dataclass(frozen=True)
class _HistoryBase:
    rollout_id: str
    end_ordinal_exclusive: int
    end_byte_offset: int


@dataclass(frozen=True)
class _SourceFile:
    path: Path
    content: bytes
    checksum: str
    name: _RolloutName | None
    metadata: JsonObject | None
    base: _HistoryBase | None
    header_ordinal: int | None


@dataclass(frozen=True)
class _PaginatedSource:
    source: _SourceFile
    first_ordinal: int


@dataclass(frozen=True)
class _SourceLines:
    source: _SourceFile
    start_line: int
    lines: tuple[bytes, ...]


class CodexRolloutStore:
    """Hide physical rollout lookup and bounded inheritance from preparation."""

    def __init__(self, source_paths: tuple[Path, ...]) -> None:
        self._paths: dict[str, list[Path]] = {}
        self._files: dict[Path, _SourceFile] = {}
        for path in sorted(set(source_paths)):
            name = _rollout_name(path)
            if name is not None:
                self._paths.setdefault(name.rollout_id, []).append(path)

    def prepare(self, source_path: Path) -> RolloutTranscript:
        """Return original bytes or materialize the explicit inherited prefix."""
        source = self._load(source_path)
        rollout_id = source.name.rollout_id if source.name is not None else None
        if source.base is None:
            return RolloutTranscript(source.content, source.checksum, rollout_id)
        self._require_paginated(source)
        inherited = self._inherited_lines(source, frozenset({source_path}))
        local_lines = tuple(source.content.splitlines(keepends=True))
        chunks = (
            _SourceLines(source, 1, local_lines[:1]),
            *inherited,
            _SourceLines(source, 2, local_lines[1:]),
        )
        owned_start_line = 2 + sum(len(chunk.lines) for chunk in inherited)
        prepared_line = 1
        segments: list[SourceSegment] = []
        content: list[bytes] = []
        for chunk in chunks:
            if not chunk.lines:
                continue
            segment = SourceSegment(
                source_path=chunk.source.path,
                source_start_line=chunk.start_line,
                prepared_start_line=prepared_line,
                line_count=len(chunk.lines),
                source_checksum_sha256=chunk.source.checksum,
            )
            previous = segments[-1] if segments else None
            if (
                previous is not None
                and previous.source_path == segment.source_path
                and previous.source_start_line + previous.line_count == segment.source_start_line
            ):
                segments[-1] = replace(
                    previous, line_count=previous.line_count + segment.line_count
                )
            else:
                segments.append(segment)
            content.extend(chunk.lines)
            prepared_line += len(chunk.lines)
        return RolloutTranscript(
            b"".join(content), source.checksum, rollout_id, owned_start_line, tuple(segments)
        )

    def _load(self, path: Path) -> _SourceFile:
        cached = self._files.get(path)
        if cached is not None:
            return cached
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise _history_error(path, f"cannot read source rollout: {exc}") from exc
        name = _rollout_name(path)
        record = _object(content.split(b"\n", maxsplit=1)[0])
        metadata = None
        if record is not None and record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, dict):
                metadata = payload
        if name is not None and metadata is not None and metadata.get("id") != name.thread_id:
            raise _history_error(path, "filename thread ID differs from session metadata")
        raw_base = metadata.get("history_base") if metadata is not None else None
        base = _parse_base(path, raw_base) if raw_base is not None else None
        source = _SourceFile(
            path,
            content,
            hashlib.sha256(content).hexdigest(),
            name,
            metadata,
            base,
            _ordinal(record.get("ordinal")) if record is not None else None,
        )
        self._files[path] = source
        return source

    def _inherited_lines(
        self, source: _SourceFile, visited: frozenset[Path]
    ) -> tuple[_SourceLines, ...]:
        base = source.base
        if base is None:
            return ()
        paths = self._paths.get(base.rollout_id, [])
        if not paths:
            raise _history_error(source.path, f"missing source rollout {base.rollout_id}")
        path = paths[0]
        if path in visited:
            raise _history_error(source.path, "cycle in history_base references")
        ancestor = self._load(path)
        if any(self._load(other).checksum != ancestor.checksum for other in paths[1:]):
            raise _history_error(source.path, f"ambiguous source rollout {base.rollout_id}")
        paginated = self._require_paginated(ancestor)
        prefix = _bounded_prefix(paginated, base)
        inherited = self._inherited_lines(ancestor, visited | {path})
        return (*inherited, _SourceLines(ancestor, 2, prefix[1:]))

    @staticmethod
    def _require_paginated(source: _SourceFile) -> _PaginatedSource:
        if source.name is None:
            raise _history_error(source.path, "history requires a canonical rollout filename")
        metadata = source.metadata
        if metadata is None or metadata.get("history_mode") != "paginated":
            raise _history_error(source.path, "history requires paginated session metadata")
        expected = source.base.end_ordinal_exclusive if source.base is not None else 0
        if source.header_ordinal != expected:
            raise _history_error(source.path, "session metadata has an invalid rollout ordinal")
        if not source.content.splitlines(keepends=True)[0].endswith(b"\n"):
            raise _history_error(source.path, "session metadata is not a complete JSONL record")
        return _PaginatedSource(source, expected)


def _rollout_name(path: Path) -> _RolloutName | None:
    match = _ROLLOUT_NAME.fullmatch(path.name)
    if match is None:
        return None
    try:
        datetime.strptime(match["timestamp"], "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    thread_id = match["thread"].lower()
    return _RolloutName(thread_id, (match["rollout"] or thread_id).lower())


def _parse_base(path: Path, value: object) -> _HistoryBase:
    if isinstance(value, dict):
        values = cast("dict[str, object]", value)
        rollout_id = values.get("thread_id")
        ordinal = _ordinal(values.get("end_ordinal_exclusive"))
        byte_offset = _ordinal(values.get("end_byte_offset"))
        if (
            isinstance(rollout_id, str)
            and re.fullmatch(_UUID, rollout_id) is not None
            and ordinal is not None
            and byte_offset is not None
        ):
            return _HistoryBase(rollout_id.lower(), ordinal, byte_offset)
    raise _history_error(path, "malformed history_base")


def _bounded_prefix(paginated: _PaginatedSource, base: _HistoryBase) -> tuple[bytes, ...]:
    source = paginated.source
    if base.end_byte_offset > len(source.content):
        raise _history_error(source.path, "history_base byte offset is past the source rollout")
    prefix = source.content[: base.end_byte_offset]
    if not prefix.endswith(b"\n"):
        raise _history_error(
            source.path, "history_base byte offset is not a complete JSONL boundary"
        )
    lines = tuple(prefix.splitlines(keepends=True))
    expected = paginated.first_ordinal
    for index, line in enumerate(lines):
        record = _object(line)
        if (
            record is None
            or _ordinal(record.get("ordinal")) != expected
            or (index > 0 and record.get("type") == "session_meta")
        ):
            raise _history_error(
                source.path, "source prefix has malformed or discontinuous ordinals"
            )
        expected += 1
    if expected != base.end_ordinal_exclusive:
        raise _history_error(source.path, "history_base ordinal and byte offset disagree")
    return lines


def _object(line: bytes) -> JsonObject | None:
    try:
        value: object = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    return cast("JsonObject", value) if isinstance(value, dict) else None


def _ordinal(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _history_error(path: Path, detail: str) -> PromptDiaryError:
    return PromptDiaryError(f"Invalid Codex rollout history {path}: {detail}")
