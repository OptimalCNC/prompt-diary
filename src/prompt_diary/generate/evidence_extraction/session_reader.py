"""Transport-independent compact and full session-line reading MCP tool API.

``read_session_lines`` resolves an indexed session by ``(project_key, session_ref)`` against the
prepared workspace, validates a 1-based physical line range, and returns either bounded compact
records (the default) or verbatim raw JSONL lines. It performs no command execution, no network
access, and accepts no arbitrary filesystem path, matching the Prompt Diary MCP safety contract.
Line numbering matches ``prepare``: bytes are decoded as UTF-8 with replacement and split without
keeping ends, so returned line numbers equal physical JSONL line numbers and citations stay stable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from prompt_diary.generate.evidence_extraction.session_compaction import (
    CompactRecord,
    compact_record,
    line_provenance,
)
from prompt_diary.generate.evidence_extraction.session_pagination import (
    MAX_SESSION_READ_BYTES,
    PaginationError,
    ReadCursor,
    RecordFragment,
    encode_read_json,
    paginate_records,
)
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from prompt_diary.generate.workspace import IndexedSession, PreparedWorkspace

__all__ = [
    "MAX_SESSION_READ_BYTES",
    "FullRecord",
    "LineRange",
    "ReadCursor",
    "ReadSessionLinesCompactResult",
    "ReadSessionLinesFullResult",
    "ReadSessionLinesInvalidResult",
    "ReadSessionLinesResult",
    "RecordFragment",
    "SessionReadError",
    "read_session_lines",
    "serialize_read_result",
]


@dataclass(frozen=True)
class SessionReadError:
    """Structured validation error returned by a rejected session read."""

    field: str
    message: str
    hint: str


@dataclass(frozen=True)
class LineRange:
    """Inclusive 1-based physical line range that a read covered."""

    start: int
    end: int


@dataclass(frozen=True)
class FullRecord:
    """One physical JSONL line returned verbatim with its provenance."""

    line: int
    raw_line: str
    raw_bytes: int
    raw_sha256: str


@dataclass(frozen=True)
class ReadSessionLinesCompactResult:
    """Successful compact session read holding bounded compact records."""

    status: Literal["ok"]
    project_key: str
    session_ref: str
    line_range: LineRange
    mode: Literal["compact"]
    records: tuple[CompactRecord | RecordFragment, ...]
    next_cursor: ReadCursor | None


@dataclass(frozen=True)
class ReadSessionLinesFullResult:
    """Successful full session read holding verbatim raw JSONL records."""

    status: Literal["ok"]
    project_key: str
    session_ref: str
    line_range: LineRange
    mode: Literal["full"]
    records: tuple[FullRecord | RecordFragment, ...]
    next_cursor: ReadCursor | None


@dataclass(frozen=True)
class ReadSessionLinesInvalidResult:
    """Rejected session read carrying structured, user-correctable errors."""

    status: Literal["invalid"]
    errors: tuple[SessionReadError, ...]


ReadSessionLinesResult: TypeAlias = (
    ReadSessionLinesCompactResult | ReadSessionLinesFullResult | ReadSessionLinesInvalidResult
)


@dataclass(frozen=True)
class _ResolvedSession:
    """A submitted ``(project_key, session_ref)`` resolved to a readable session file."""

    session: IndexedSession
    physical_lines: tuple[str, ...]


def read_session_lines(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
    start_line: int,
    end_line: int,
    mode: Literal["compact", "full"] = "compact",
    cursor: ReadCursor | None = None,
) -> ReadSessionLinesResult:
    """Read one bounded page of a physical line range; follow next_cursor until it is null."""
    resolved = _resolve_session(
        workspace_path=workspace_path,
        project_key=project_key,
        session_ref=session_ref,
    )
    if isinstance(resolved, ReadSessionLinesInvalidResult):
        return resolved

    physical_lines = resolved.physical_lines
    range_error = _validate_range(
        start_line=start_line,
        end_line=end_line,
        total_lines=len(physical_lines),
    )
    if range_error is not None:
        return range_error

    line_range = LineRange(start=start_line, end=end_line)
    position = cursor or ReadCursor(start_line)
    if not start_line <= position.line <= end_line or position.offset < 0:
        return _invalid("cursor", "cursor is outside the requested range", _CURSOR_HINT)
    if mode == "full":
        return _full_page(
            physical_lines,
            project_key=project_key,
            session_ref=session_ref,
            line_range=line_range,
            cursor=position,
        )
    return _compact_page(
        physical_lines,
        project_key=project_key,
        session_ref=session_ref,
        line_range=line_range,
        cursor=position,
        source=resolved.session.source,
    )


def serialize_read_result(result: ReadSessionLinesResult) -> str:
    """Return the exact canonical JSON text budgeted by the API and emitted through MCP."""
    return encode_read_json(asdict(result))


def _compact_page(
    physical_lines: tuple[str, ...],
    *,
    project_key: str,
    session_ref: str,
    line_range: LineRange,
    cursor: ReadCursor,
    source: str,
) -> ReadSessionLinesCompactResult | ReadSessionLinesInvalidResult:
    def result(
        records: tuple[CompactRecord | RecordFragment, ...], next_cursor: ReadCursor | None
    ) -> ReadSessionLinesCompactResult:
        return ReadSessionLinesCompactResult(
            "ok", project_key, session_ref, line_range, "compact", records, next_cursor
        )

    page = paginate_records(
        _compact_records(
            physical_lines, start_line=cursor.line, end_line=line_range.end, source=source
        ),
        cursor=cursor,
        end_line=line_range.end,
        record_format="compact_json",
        record_content=lambda record: encode_read_json(asdict(record)),
        encode_page=lambda records, next_cursor: serialize_read_result(
            result(records, next_cursor)
        ),
    )
    if isinstance(page, PaginationError):
        return _invalid("cursor", page.message, _CURSOR_HINT)
    return result(page.records, page.next_cursor)


def _full_page(
    physical_lines: tuple[str, ...],
    *,
    project_key: str,
    session_ref: str,
    line_range: LineRange,
    cursor: ReadCursor,
) -> ReadSessionLinesFullResult | ReadSessionLinesInvalidResult:
    def result(
        records: tuple[FullRecord | RecordFragment, ...], next_cursor: ReadCursor | None
    ) -> ReadSessionLinesFullResult:
        return ReadSessionLinesFullResult(
            "ok", project_key, session_ref, line_range, "full", records, next_cursor
        )

    page = paginate_records(
        (
            _full_record(physical_lines[line - 1], line=line)
            for line in range(cursor.line, line_range.end + 1)
        ),
        cursor=cursor,
        end_line=line_range.end,
        record_format="raw_line",
        record_content=lambda record: record.raw_line,
        encode_page=lambda records, next_cursor: serialize_read_result(
            result(records, next_cursor)
        ),
    )
    if isinstance(page, PaginationError):
        return _invalid("cursor", page.message, _CURSOR_HINT)
    return result(page.records, page.next_cursor)


def _compact_records(
    physical_lines: tuple[str, ...], *, start_line: int, end_line: int, source: str
) -> Iterator[CompactRecord]:
    for index in range(start_line - 1, end_line):
        yield compact_record(
            physical_lines[index],
            line=index + 1,
            source=source,
            previous_line=physical_lines[index - 1] if index > 0 else None,
            next_line=physical_lines[index + 1] if index + 1 < len(physical_lines) else None,
        )


def _full_record(raw_line: str, *, line: int) -> FullRecord:
    raw_bytes, raw_sha256 = line_provenance(raw_line)
    return FullRecord(line=line, raw_line=raw_line, raw_bytes=raw_bytes, raw_sha256=raw_sha256)


def _validate_range(
    *,
    start_line: int,
    end_line: int,
    total_lines: int,
) -> ReadSessionLinesInvalidResult | None:
    if start_line < 1:
        return _invalid("start_line", _below_one_message(start_line), _RANGE_HINT)
    if end_line < start_line:
        return _invalid("end_line", _reversed_message(start_line, end_line), _RANGE_HINT)
    if start_line > total_lines:
        return _invalid("start_line", _start_past_end_message(start_line, total_lines), _RANGE_HINT)
    if end_line > total_lines:
        return _invalid("end_line", _end_past_end_message(end_line, total_lines), _RANGE_HINT)
    return None


def _resolve_session(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
) -> _ResolvedSession | ReadSessionLinesInvalidResult:
    workspace = load_prepared_workspace(workspace_path)
    session = _find_session(workspace, project_key, session_ref)
    if isinstance(session, ReadSessionLinesInvalidResult):
        return session
    session_file = workspace_path / "projects" / project_key / session.session_path
    if not session_file.is_file():
        return _invalid(
            "session_ref", _missing_session_file_message(session_ref), _MISSING_SESSION_FILE_HINT
        )
    raw_bytes = session_file.read_bytes()
    physical_lines = tuple(raw_bytes.decode("utf-8", errors="replace").splitlines())
    return _ResolvedSession(session=session, physical_lines=physical_lines)


def _find_session(
    workspace: PreparedWorkspace,
    project_key: str,
    session_ref: str,
) -> IndexedSession | ReadSessionLinesInvalidResult:
    project = next((item for item in workspace.projects if item.project_key == project_key), None)
    if project is None:
        return _invalid("project_key", _unknown_project_message(project_key), _UNKNOWN_PROJECT_HINT)
    session = next((item for item in project.sessions if item.session_ref == session_ref), None)
    if session is None:
        return _invalid(
            "session_ref", _unknown_session_message(session_ref, project_key), _UNKNOWN_SESSION_HINT
        )
    return session


def _invalid(field: str, message: str, hint: str) -> ReadSessionLinesInvalidResult:
    return ReadSessionLinesInvalidResult(
        "invalid", (SessionReadError(field, message[:1024], hint),)
    )


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r}"


def _unknown_session_message(session_ref: str, project_key: str) -> str:
    return f"unknown session_ref {session_ref!r} for project {project_key!r}"


def _missing_session_file_message(session_ref: str) -> str:
    return f"session file for session_ref {session_ref!r} is missing from the prepared workspace"


def _below_one_message(start_line: int) -> str:
    return f"start_line {start_line} must be a positive 1-based line number"


def _reversed_message(start_line: int, end_line: int) -> str:
    return f"end_line {end_line} must be >= start_line {start_line}"


def _start_past_end_message(start_line: int, total_lines: int) -> str:
    return f"start_line {start_line} is past the session's last line {total_lines}"


def _end_past_end_message(end_line: int, total_lines: int) -> str:
    return f"end_line {end_line} is past the session's last line {total_lines}"


_UNKNOWN_PROJECT_HINT = "use the project_key from the prepared workspace"
_UNKNOWN_SESSION_HINT = "use a session_ref listed in sessions.index.jsonl"
_MISSING_SESSION_FILE_HINT = "ensure the prepared workspace still contains the copied session file"
_RANGE_HINT = "request a 1-based line range contained by the session"
_CURSOR_HINT = "reuse next_cursor with the same project, session, range, and mode"
