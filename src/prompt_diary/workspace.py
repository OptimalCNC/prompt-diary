"""Workspace preparation for prompt diary reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from prompt_diary.errors import PromptDiaryError
from prompt_diary.models import (
    JsonObject,
    JsonValue,
    PrepareResult,
    ReportTarget,
    SourceName,
    SourceSpec,
    serialize_datetime,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

CODEX_SOURCE_ENV = "PROMPT_DIARY_CODEX_SESSIONS"
CLAUDE_SOURCE_ENV = "PROMPT_DIARY_CLAUDE_PROJECTS"
REPORTS_DIRNAME = ".reports"
SCHEMA_VERSION = 1

_UNSAFE_DISPLAY_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_DASHES = re.compile(r"-+")


@dataclass(frozen=True)
class ProjectIdentity:
    """Deterministic project identity for workspace layout."""

    key: str
    label: str
    canonical_root: str
    is_unknown: bool


@dataclass(frozen=True)
class ParsedSession:
    """A source session selected for the target report window."""

    source: SourceName
    source_path: Path
    source_session_id: str
    project: ProjectIdentity
    target_start_line: int
    target_end_line: int
    total_lines: int
    source_checksum_sha256: str
    malformed_line_count: int
    untimestamped_record_count: int
    non_monotonic_timestamp_count: int
    first_event_at: str | None
    last_event_at: str | None

    @property
    def session_filename(self) -> str:
        """Return the copied filename for this session."""
        return self.source_path.name


def default_source_specs(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[SourceSpec, ...]:
    """Return default local assistant session sources."""
    values = os.environ if env is None else env
    user_home = Path.home() if home is None else home
    specs: list[SourceSpec] = []

    codex_root = _configured_path(values.get(CODEX_SOURCE_ENV), user_home / ".codex" / "sessions")
    if codex_root is not None:
        specs.append(SourceSpec(source="codex", root=codex_root))

    claude_root = _configured_path(
        values.get(CLAUDE_SOURCE_ENV),
        user_home / ".claude" / "projects",
    )
    if claude_root is not None:
        specs.append(SourceSpec(source="claude-code", root=claude_root))

    return tuple(specs)


def prepare_workspace(
    target: ReportTarget,
    *,
    reports_root: Path = Path(REPORTS_DIRNAME),
    source_specs: tuple[SourceSpec, ...] | None = None,
    force: bool = False,
    prepared_at: datetime | None = None,
) -> PrepareResult:
    """Prepare the deterministic report workspace for a target day."""
    workspace_path = reports_root / "work" / target.workspace_name
    audit_dir = reports_root / "private" / target.workspace_name
    audit_path = audit_dir / "audit.manifest.json"

    if workspace_path.exists() and not force:
        return _existing_prepare_result(target, workspace_path, audit_path)

    if force:
        _remove_existing_workspace(workspace_path, audit_dir)

    specs = default_source_specs() if source_specs is None else source_specs
    prepared_at_local = _timestamp_for_target(target, prepared_at)
    parsed_sessions = tuple(_selected_sessions(specs, target))
    _write_prepared_workspace(
        target=target,
        workspace_path=workspace_path,
        audit_path=audit_path,
        source_specs=specs,
        sessions=parsed_sessions,
        prepared_at=prepared_at_local,
    )

    project_count = len({session.project.key for session in parsed_sessions})
    message = (
        f"Prepared workspace {workspace_path} "
        f"with {project_count} project(s) and {len(parsed_sessions)} session(s)."
    )
    return PrepareResult(
        target=target,
        workspace_path=workspace_path,
        audit_path=audit_path,
        created=True,
        project_count=project_count,
        session_count=len(parsed_sessions),
        messages=(message,),
    )


def workspace_path_for_target(
    target: ReportTarget,
    *,
    reports_root: Path = Path(REPORTS_DIRNAME),
) -> Path:
    """Return the prepared workspace path for a target."""
    return reports_root / "work" / target.workspace_name


def audit_path_for_target(
    target: ReportTarget,
    *,
    reports_root: Path = Path(REPORTS_DIRNAME),
) -> Path:
    """Return the private audit manifest path for a target."""
    return reports_root / "private" / target.workspace_name / "audit.manifest.json"


def validate_workspace_matches_target(workspace_path: Path, target: ReportTarget) -> None:
    """Verify an existing workspace belongs to the requested target."""
    metadata_path = workspace_path / "metadata.json"
    metadata = _load_existing_metadata(metadata_path, target)
    expected_values = _target_match_values(target)
    mismatches: list[str] = []
    for key, expected_value in expected_values.items():
        actual_value = _metadata_match_value(metadata, key)
        if actual_value != expected_value:
            mismatches.append(f"{key}: expected {expected_value!r}, found {actual_value!r}")
    if mismatches:
        details = "; ".join(mismatches)
        raise PromptDiaryError(_workspace_target_mismatch_message(workspace_path, target, details))


def _configured_path(value: str | None, default: Path) -> Path | None:
    if value is not None and value.strip() == "":
        return None
    if value is None:
        return default.expanduser()
    return Path(value).expanduser()


def _existing_prepare_result(
    target: ReportTarget,
    workspace_path: Path,
    audit_path: Path,
) -> PrepareResult:
    validate_workspace_matches_target(workspace_path, target)
    project_count, session_count = _count_existing_workspace(workspace_path)
    message = f"Workspace already exists at {workspace_path}; use prepare --force to refresh it."
    return PrepareResult(
        target=target,
        workspace_path=workspace_path,
        audit_path=audit_path,
        created=False,
        project_count=project_count,
        session_count=session_count,
        messages=(message,),
    )


def _count_existing_workspace(workspace_path: Path) -> tuple[int, int]:
    project_root = workspace_path / "projects"
    if not project_root.exists():
        return 0, 0
    project_dirs = tuple(path for path in project_root.iterdir() if path.is_dir())
    session_count = 0
    for project_dir in project_dirs:
        index_path = project_dir / "sessions.index.jsonl"
        if index_path.exists():
            session_count += len(index_path.read_text(encoding="utf-8").splitlines())
    return len(project_dirs), session_count


def _load_existing_metadata(metadata_path: Path, target: ReportTarget) -> JsonObject:
    if not metadata_path.exists():
        raise PromptDiaryError(_existing_metadata_error_message(metadata_path, target))
    try:
        raw = cast("object", json.loads(metadata_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise PromptDiaryError(_existing_metadata_error_message(metadata_path, target)) from exc
    if not isinstance(raw, dict):
        raise PromptDiaryError(_existing_metadata_error_message(metadata_path, target))
    return cast("JsonObject", raw)


def _target_match_values(target: ReportTarget) -> dict[str, str]:
    return {
        "report_date": target.report_date.isoformat(),
        "timezone": target.timezone,
        "status": target.status,
        "report_window_local.start": serialize_datetime(target.report_window_local.start),
        "report_window_local.end": serialize_datetime(target.report_window_local.end),
        "report_window_utc.start": serialize_datetime(target.report_window_utc.start),
        "report_window_utc.end": serialize_datetime(target.report_window_utc.end),
    }


def _metadata_match_value(metadata: JsonObject, key: str) -> str | None:
    if "." not in key:
        return _string_value(metadata, key)
    object_key, nested_key = key.split(".", 1)
    nested = _object_value(metadata, object_key)
    if nested is None:
        return None
    return _string_value(nested, nested_key)


def _existing_metadata_error_message(metadata_path: Path, target: ReportTarget) -> str:
    return (
        f"Existing workspace metadata is missing or invalid at {metadata_path}. "
        f"Run prompt-diary prepare --date {target.report_date.isoformat()} "
        f"--timezone {target.timezone} --force to refresh it."
    )


def _workspace_target_mismatch_message(
    workspace_path: Path,
    target: ReportTarget,
    details: str,
) -> str:
    return (
        f"Existing workspace {workspace_path} does not match the requested target "
        f"({details}). Run prompt-diary prepare --date {target.report_date.isoformat()} "
        f"--timezone {target.timezone} --force to refresh it."
    )


def _remove_existing_workspace(workspace_path: Path, audit_dir: Path) -> None:
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    if audit_dir.exists():
        shutil.rmtree(audit_dir)


def _selected_sessions(
    source_specs: tuple[SourceSpec, ...],
    target: ReportTarget,
) -> Iterable[ParsedSession]:
    for spec in sorted(source_specs, key=lambda item: (item.source, item.root.as_posix())):
        for source_path in _jsonl_source_files(spec.root):
            parsed = _parse_session_file(source_path=source_path, spec=spec, target=target)
            if parsed is not None:
                yield parsed


def _jsonl_source_files(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root,) if root.suffix == ".jsonl" else ()
    if not root.exists():
        return ()
    return tuple(sorted(root.rglob("*.jsonl"), key=lambda path: path.as_posix()))


def _parse_session_file(
    *,
    source_path: Path,
    spec: SourceSpec,
    target: ReportTarget,
) -> ParsedSession | None:
    raw_bytes = source_path.read_bytes()
    checksum = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    state = _ParseState(source_path=source_path, source=spec.source)

    for line_number, line in enumerate(lines, start=1):
        record = _json_object_from_line(line)
        if record is None:
            state.malformed_line_count += 1
            continue
        _record_session_metadata(state, record)
        timestamp = _record_timestamp(spec.source, record)
        if timestamp is None:
            state.untimestamped_record_count += 1
            continue
        state.record_timestamp(timestamp, line_number, target)

    if state.target_start_line is None or state.target_end_line is None:
        return None

    source_session_id = _source_session_id_for_state(state)
    project_root = _project_root_for_session(state, spec)
    project = project_identity(
        project_root=project_root,
        source=spec.source,
        source_session_id=source_session_id,
    )

    first_event_at = cast("datetime", state.first_event_at)
    last_event_at = cast("datetime", state.last_event_at)
    return ParsedSession(
        source=spec.source,
        source_path=source_path,
        source_session_id=source_session_id,
        project=project,
        target_start_line=state.target_start_line,
        target_end_line=state.target_end_line,
        total_lines=len(lines),
        source_checksum_sha256=checksum,
        malformed_line_count=state.malformed_line_count,
        untimestamped_record_count=state.untimestamped_record_count,
        non_monotonic_timestamp_count=state.non_monotonic_timestamp_count,
        first_event_at=serialize_datetime(first_event_at),
        last_event_at=serialize_datetime(last_event_at),
    )


@dataclass
class _ParseState:
    source_path: Path
    source: SourceName
    source_session_id: str | None = None
    codex_session_meta_cwd: str | None = None
    codex_turn_context_cwd: str | None = None
    claude_cwd: str | None = None
    malformed_line_count: int = 0
    untimestamped_record_count: int = 0
    non_monotonic_timestamp_count: int = 0
    target_start_line: int | None = None
    target_end_line: int | None = None
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    previous_event_at: datetime | None = None

    def record_timestamp(self, timestamp: datetime, line_number: int, target: ReportTarget) -> None:
        """Record a parsed timestamp and update target span bounds."""
        if self.previous_event_at is not None and timestamp < self.previous_event_at:
            self.non_monotonic_timestamp_count += 1
        self.previous_event_at = timestamp
        self.first_event_at = _earliest(self.first_event_at, timestamp)
        self.last_event_at = _latest(self.last_event_at, timestamp)

        if target.report_window_utc.start <= timestamp < target.report_window_utc.end:
            if self.target_start_line is None:
                self.target_start_line = line_number
            self.target_end_line = line_number


def _record_session_metadata(state: _ParseState, record: JsonObject) -> None:
    if state.source == "codex":
        _record_codex_metadata(state, record)
    else:
        cwd = _string_value(record, "cwd")
        if cwd is not None and state.claude_cwd is None:
            state.claude_cwd = cwd


def _record_codex_metadata(state: _ParseState, record: JsonObject) -> None:
    record_type = _string_value(record, "type")
    payload = _object_value(record, "payload")
    if record_type == "session_meta" and payload is not None:
        source_session_id = _string_value(payload, "id")
        if source_session_id is not None:
            state.source_session_id = source_session_id
        cwd = _string_value(payload, "cwd")
        if cwd is not None:
            state.codex_session_meta_cwd = cwd
    if record_type == "turn_context" and payload is not None:
        cwd = _string_value(payload, "cwd")
        if cwd is not None and state.codex_turn_context_cwd is None:
            state.codex_turn_context_cwd = cwd


def _source_session_id_for_state(state: _ParseState) -> str:
    if state.source == "codex":
        return state.source_session_id or state.source_path.stem
    return _claude_source_session_id(state.source_path)


def _claude_source_session_id(source_path: Path) -> str:
    stem = source_path.stem
    parts = source_path.parts
    subagents_index = _last_subagents_index(parts)
    if subagents_index is None:
        return stem
    context_parts = parts[subagents_index + 1 : -1]
    if not context_parts:
        return f"{stem}@subagents"
    subagent_id = "/".join(context_parts)
    return f"{stem}@subagents/{subagent_id}"


def _last_subagents_index(parts: tuple[str, ...]) -> int | None:
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] == "subagents":
            return index
    return None


def _project_root_for_session(state: _ParseState, spec: SourceSpec) -> str | None:
    if state.source == "codex":
        return state.codex_session_meta_cwd or state.codex_turn_context_cwd or _fallback_root(spec)
    return state.claude_cwd or _fallback_root(spec)


def _fallback_root(spec: SourceSpec) -> str | None:
    if spec.fallback_project_root is None:
        return None
    return str(spec.fallback_project_root)


def project_identity(
    *,
    project_root: str | None,
    source: SourceName,
    source_session_id: str,
) -> ProjectIdentity:
    """Create a deterministic project identity from a canonical root or fallback identity."""
    if project_root is None or project_root.strip() == "":
        is_unknown = True
        canonical_root = f"unknown-project/{source}/{source_session_id}"
    else:
        is_unknown = False
        canonical_root = _canonical_project_root(project_root)
    display_name = _display_name(canonical_root, is_unknown=is_unknown)
    label = sanitize_display_name(display_name)
    hash12 = hashlib.sha256(canonical_root.encode("utf-8")).hexdigest()[:12]
    return ProjectIdentity(
        key=f"{label}-{hash12}",
        label=label,
        canonical_root=canonical_root,
        is_unknown=is_unknown,
    )


def sanitize_display_name(value: str) -> str:
    """Sanitize a project display name for stable folder keys and labels."""
    sanitized = _UNSAFE_DISPLAY_CHARS.sub("-", value)
    sanitized = _REPEATED_DASHES.sub("-", sanitized).strip("-")
    sanitized = sanitized[:48].strip("-")
    return sanitized or "unknown-project"


def _canonical_project_root(project_root: str) -> str:
    path = Path(project_root).expanduser()
    if path.exists():
        return str(path.resolve())
    return str(path).replace("\\", "/")


def _display_name(canonical_root: str, *, is_unknown: bool) -> str:
    if is_unknown:
        return "unknown-project"
    normalized = canonical_root.replace("\\", "/")
    return PurePosixPath(normalized).name or "unknown-project"


def _write_prepared_workspace(
    *,
    target: ReportTarget,
    workspace_path: Path,
    audit_path: Path,
    source_specs: tuple[SourceSpec, ...],
    sessions: tuple[ParsedSession, ...],
    prepared_at: datetime,
) -> None:
    workspace_path.mkdir(parents=True, exist_ok=True)
    (workspace_path / "projects").mkdir(exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    _write_json(workspace_path / "metadata.json", _metadata_json(target, prepared_at))
    _write_project_workspaces(workspace_path, sessions)
    _write_json(audit_path, _audit_manifest(target, prepared_at, source_specs, sessions))


def _metadata_json(target: ReportTarget, prepared_at: datetime) -> JsonObject:
    return {
        "schema_version": SCHEMA_VERSION,
        "report_date": target.report_date.isoformat(),
        "timezone": target.timezone,
        "status": target.status,
        "prepared_at": serialize_datetime(prepared_at),
        "report_window_local": {
            "start": serialize_datetime(target.report_window_local.start),
            "end": serialize_datetime(target.report_window_local.end),
        },
        "report_window_utc": {
            "start": serialize_datetime(target.report_window_utc.start),
            "end": serialize_datetime(target.report_window_utc.end),
        },
    }


def _write_project_workspaces(workspace_path: Path, sessions: tuple[ParsedSession, ...]) -> None:
    seen_destinations: set[tuple[str, str]] = set()
    for project in _projects_from_sessions(sessions):
        project_dir = workspace_path / "projects" / project.key
        project_dir.mkdir(parents=True, exist_ok=True)
        _write_json(project_dir / "project.json", _project_json(project))
        project_sessions = _sessions_for_project(sessions, project.key)
        _copy_project_sessions(project_dir, project_sessions, seen_destinations)


def _projects_from_sessions(sessions: tuple[ParsedSession, ...]) -> tuple[ProjectIdentity, ...]:
    projects = {session.project.key: session.project for session in sessions}
    return tuple(projects[key] for key in sorted(projects))


def _project_json(project: ProjectIdentity) -> JsonObject:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_key": project.key,
        "project_label": project.label,
    }


def _sessions_for_project(
    sessions: tuple[ParsedSession, ...],
    project_key: str,
) -> tuple[ParsedSession, ...]:
    filtered = [session for session in sessions if session.project.key == project_key]
    return tuple(
        sorted(
            filtered,
            key=lambda item: (
                item.source,
                item.source_session_id,
                _session_relative_path(item),
            ),
        )
    )


def _copy_project_sessions(
    project_dir: Path,
    sessions: tuple[ParsedSession, ...],
    seen_destinations: set[tuple[str, str]],
) -> None:
    index_rows: list[JsonObject] = []
    for position, session in enumerate(sessions, start=1):
        session_path = _session_relative_path(session)
        destination_key = (session.project.key, session_path)
        if destination_key in seen_destinations:
            raise PromptDiaryError(_filename_collision_message(session, session_path))
        seen_destinations.add(destination_key)
        destination = project_dir / session_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(session.source_path, destination)
        index_rows.append(_session_index_row(session, session_ref=f"S{position:04d}"))

    _write_jsonl(project_dir / "sessions.index.jsonl", index_rows)


def _session_relative_path(session: ParsedSession) -> str:
    return f"sessions/{session.source}/{session.session_filename}"


def _session_index_row(session: ParsedSession, *, session_ref: str) -> JsonObject:
    return {
        "session_ref": session_ref,
        "source": session.source,
        "source_session_id": session.source_session_id,
        "session_path": _session_relative_path(session),
        "target_start_line": session.target_start_line,
        "target_end_line": session.target_end_line,
    }


def _audit_manifest(
    target: ReportTarget,
    prepared_at: datetime,
    source_specs: tuple[SourceSpec, ...],
    sessions: tuple[ParsedSession, ...],
) -> JsonObject:
    return {
        "schema_version": SCHEMA_VERSION,
        "report_date": target.report_date.isoformat(),
        "timezone": target.timezone,
        "status": target.status,
        "prepared_at": serialize_datetime(prepared_at),
        "source_specs": [_source_spec_json(spec) for spec in source_specs],
        "sessions": [
            _session_audit_json(session) for session in _sorted_sessions_for_audit(sessions)
        ],
    }


def _source_spec_json(spec: SourceSpec) -> JsonObject:
    result: JsonObject = {
        "source": spec.source,
        "root": str(spec.root),
    }
    if spec.fallback_project_root is not None:
        result["fallback_project_root"] = str(spec.fallback_project_root)
    return result


def _session_audit_json(session: ParsedSession) -> JsonObject:
    return {
        "source": session.source,
        "source_session_id": session.source_session_id,
        "source_path": str(session.source_path),
        "workspace_project_key": session.project.key,
        "workspace_session_path": _session_relative_path(session),
        "canonical_project_root": session.project.canonical_root,
        "project_root_is_unknown": session.project.is_unknown,
        "source_checksum_sha256": session.source_checksum_sha256,
        "workspace_checksum_sha256": session.source_checksum_sha256,
        "total_lines": session.total_lines,
        "target_start_line": session.target_start_line,
        "target_end_line": session.target_end_line,
        "first_event_at": session.first_event_at,
        "last_event_at": session.last_event_at,
        "malformed_line_count": session.malformed_line_count,
        "untimestamped_record_count": session.untimestamped_record_count,
        "non_monotonic_timestamp_count": session.non_monotonic_timestamp_count,
        "warnings": _session_warnings(session),
    }


def _session_warnings(session: ParsedSession) -> list[JsonValue]:
    warnings: list[JsonValue] = []
    if session.malformed_line_count:
        warnings.append(f"{session.malformed_line_count} malformed JSONL line(s)")
    if session.untimestamped_record_count:
        warnings.append(f"{session.untimestamped_record_count} untimestamped record(s)")
    if session.non_monotonic_timestamp_count:
        warnings.append(f"{session.non_monotonic_timestamp_count} non-monotonic timestamp(s)")
    return warnings


def _sorted_sessions_for_audit(sessions: tuple[ParsedSession, ...]) -> tuple[ParsedSession, ...]:
    return tuple(
        sorted(
            sessions,
            key=lambda item: (
                item.project.key,
                item.source,
                item.source_session_id,
                _session_relative_path(item),
            ),
        )
    )


def _write_json(path: Path, content: JsonObject) -> None:
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[JsonObject]) -> None:
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _json_object_from_line(line: str) -> JsonObject | None:
    try:
        raw = cast("object", json.loads(line))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    return cast("JsonObject", raw)


def _record_timestamp(source: SourceName, record: JsonObject) -> datetime | None:
    timestamp = _parse_timestamp(_string_value(record, "timestamp"))
    if timestamp is not None:
        return timestamp
    if source == "codex" and _string_value(record, "type") == "session_meta":
        payload = _object_value(record, "payload")
        if payload is not None:
            return _parse_timestamp(_string_value(payload, "timestamp"))
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _string_value(record: JsonObject, key: str) -> str | None:
    value = record.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _object_value(record: JsonObject, key: str) -> JsonObject | None:
    value = record.get(key)
    if isinstance(value, dict):
        return cast("JsonObject", value)
    return None


def _earliest(current: datetime | None, candidate: datetime) -> datetime:
    if current is None or candidate < current:
        return candidate
    return current


def _latest(current: datetime | None, candidate: datetime) -> datetime:
    if current is None or candidate > current:
        return candidate
    return current


def _timestamp_for_target(target: ReportTarget, timestamp: datetime | None) -> datetime:
    target_tzinfo = _target_tzinfo(target)
    if timestamp is None:
        return datetime.now(target_tzinfo)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=target_tzinfo)
    return timestamp.astimezone(target_tzinfo)


def _target_tzinfo(target: ReportTarget) -> tzinfo:
    return target.report_window_local.start.tzinfo or timezone.utc


def _filename_collision_message(session: ParsedSession, session_path: str) -> str:
    return (
        "Session filename collision while preparing workspace: "
        f"{session.project.key}/{session_path}"
    )
