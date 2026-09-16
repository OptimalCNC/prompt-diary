"""Lossless pagination of compact records and raw lines under one serialized output budget."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

MAX_SESSION_READ_BYTES = 32 * 1024
"""Maximum UTF-8 bytes in the canonical JSON text of one session-read result."""


@dataclass(frozen=True)
class ReadCursor:
    """Next physical line and Unicode-character offset in its serialized record or raw line."""

    line: int
    offset: int = 0


@dataclass(frozen=True)
class RecordFragment:
    """A lossless fragment of one oversized record; provenance describes the original line."""

    line: int
    raw_bytes: int
    raw_sha256: str
    record_format: Literal["compact_json", "raw_line"]
    offset: int
    total_chars: int
    content: str


class _Record(Protocol):
    @property
    def line(self) -> int: ...

    @property
    def raw_bytes(self) -> int: ...

    @property
    def raw_sha256(self) -> str: ...


_RecordT = TypeVar("_RecordT", bound=_Record)


@dataclass(frozen=True)
class RecordPage(Generic[_RecordT]):
    records: tuple[_RecordT | RecordFragment, ...]
    next_cursor: ReadCursor | None


@dataclass(frozen=True)
class PaginationError:
    message: str


def encode_read_json(value: object) -> str:
    """Serialize deterministically, preserving Unicode and escaping isolated surrogates."""
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        .encode("utf-8", errors="backslashreplace")
        .decode("utf-8")
    )


def paginate_records(
    records: Iterator[_RecordT],
    *,
    cursor: ReadCursor,
    end_line: int,
    record_format: Literal["compact_json", "raw_line"],
    record_content: Callable[[_RecordT], str],
    encode_page: Callable[[tuple[_RecordT | RecordFragment, ...], ReadCursor | None], str],
) -> RecordPage[_RecordT] | PaginationError:
    """Keep whole records when possible; split oversized ones without dropping any characters."""
    page: tuple[_RecordT | RecordFragment, ...] = ()
    for record in records:
        after_record = _after_record(record.line, end_line)
        if cursor.offset == 0 and _fits(encode_page((*page, record), after_record)):
            page = (*page, record)
        elif page:
            return RecordPage(page, cursor)
        else:
            fragment = _fit_fragment(
                record,
                content=record_content(record),
                offset=cursor.offset,
                record_format=record_format,
                after_record=after_record,
                encode_page=encode_page,
            )
            if isinstance(fragment, PaginationError):
                return fragment
            page = (fragment,)
            offset = fragment.offset + len(fragment.content)
            if offset < fragment.total_chars:
                return RecordPage(page, ReadCursor(record.line, offset))
        if after_record is None:
            break
        cursor = after_record
    return RecordPage(page, None)


def _after_record(line: int, end_line: int) -> ReadCursor | None:
    return ReadCursor(line + 1) if line < end_line else None


def _fits(encoded: str) -> bool:
    return len(encoded.encode("utf-8")) <= MAX_SESSION_READ_BYTES


def _fit_fragment(
    record: _RecordT,
    *,
    content: str,
    offset: int,
    record_format: Literal["compact_json", "raw_line"],
    after_record: ReadCursor | None,
    encode_page: Callable[[tuple[_RecordT | RecordFragment, ...], ReadCursor | None], str],
) -> RecordFragment | PaginationError:
    if offset >= len(content):
        return PaginationError("cursor offset is past the record content")

    def candidate(count: int) -> RecordFragment:
        return RecordFragment(
            line=record.line,
            raw_bytes=record.raw_bytes,
            raw_sha256=record.raw_sha256,
            record_format=record_format,
            offset=offset,
            total_chars=len(content),
            content=content[offset : offset + count],
        )

    remaining = len(content) - offset
    if remaining <= MAX_SESSION_READ_BYTES and _fits(
        encode_page((candidate(remaining),), after_record)
    ):
        return candidate(remaining)
    low, high = 0, min(remaining - 1, MAX_SESSION_READ_BYTES)
    while low < high:
        count = (low + high + 1) // 2
        if _fits(encode_page((candidate(count),), ReadCursor(record.line, offset + count))):
            low = count
        else:
            high = count - 1
    if low == 0:
        return PaginationError("session identifiers leave no room for record content in a page")
    return candidate(low)
