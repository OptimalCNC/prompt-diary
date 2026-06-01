"""Workspace preparation for prompt diary reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

import msgspec

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
from prompt_diary.progress.events import PrepareFinished, PrepareStarted, PrepareStep
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from prompt_diary.progress.reporter import ProgressReporter

CODEX_SOURCE_ENV = "PROMPT_DIARY_CODEX_SESSIONS"
CLAUDE_SOURCE_ENV = "PROMPT_DIARY_CLAUDE_PROJECTS"
REPORTS_DIRNAME = ".reports"
SCHEMA_VERSION = 2

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
class TargetSubagent:
    """A source subagent transcript associated with a parent turn."""

    source_path: Path
    session_file: str
    source_session_id: str
    agent_role: str | None
    parent_spawn_line: int | None
    parent_result_line: int | None


@dataclass(frozen=True)
class ParsedTurn:
    """One trigger-owned work unit inside a parsed session."""

    turn_ref: str
    turn_start_line: int
    turn_end_line: int
    target_subagents: tuple[TargetSubagent, ...] = ()


@dataclass(frozen=True)
class ParsedSession:
    """A source session selected for the target report window."""

    source: SourceName
    source_path: Path
    source_session_id: str
    project: ProjectIdentity
    turns: tuple[ParsedTurn, ...]
    total_lines: int
    source_checksum_sha256: str
    malformed_line_count: int
    untimestamped_record_count: int
    non_monotonic_timestamp_count: int
    first_event_at: str | None
    last_event_at: str | None

    @property
    def target_start_line(self) -> int:
        return self.turns[0].turn_start_line

    @property
    def target_end_line(self) -> int:
        return self.turns[-1].turn_end_line

    @property
    def target_subagents(self) -> tuple[TargetSubagent, ...]:
        return tuple(sub for turn in self.turns for sub in turn.target_subagents)

    @property
    def session_filename(self) -> str:
        """Return the copied filename for this session."""
        return self.source_path.name


@dataclass(frozen=True)
class _SourceSubagent:
    source_path: Path
    source_session_id: str
    parent_source_session_id: str | None
    agent_role: str | None


@dataclass(frozen=True)
class _ParentSubagentReference:
    source_session_id: str
    agent_role: str | None
    parent_spawn_line: int | None
    parent_result_line: int | None


@dataclass
class _MutableParentSubagentReference:
    source_session_id: str
    agent_role: str | None = None
    parent_spawn_line: int | None = None
    parent_result_line: int | None = None

    def to_reference(self) -> _ParentSubagentReference:
        return _ParentSubagentReference(
            source_session_id=self.source_session_id,
            agent_role=self.agent_role,
            parent_spawn_line=self.parent_spawn_line,
            parent_result_line=self.parent_result_line,
        )


@dataclass(frozen=True)
class _PendingSubagentSpawn:
    line_number: int
    agent_role: str | None


@dataclass(frozen=True)
class _SourceSubagentIndex:
    by_parent_and_id: Mapping[tuple[str, str], _SourceSubagent]
    by_id: Mapping[str, tuple[_SourceSubagent, ...]]

    def find(
        self,
        *,
        parent_source_session_id: str,
        source_session_id: str,
    ) -> _SourceSubagent | None:
        matched = self.by_parent_and_id.get((parent_source_session_id, source_session_id))
        if matched is not None:
            return matched
        candidates = tuple(
            candidate
            for candidate in self.by_id.get(source_session_id, ())
            if candidate.parent_source_session_id in (None, parent_source_session_id)
        )
        if len(candidates) == 1:
            return candidates[0]
        return None


@dataclass(frozen=True)
class _SourceProbe:
    candidate_root_paths: frozenset[Path]
    subagent_index: _SourceSubagentIndex


@dataclass
class _ScanProgress:
    total: int
    stride: int
    reporter: ProgressReporter
    scope: str
    scanned: int = 0

    def advance(self) -> None:
        self.scanned += 1
        if self.scanned % self.stride == 0 or self.scanned == self.total:
            self.reporter.emit(
                PrepareStep(
                    at=time.monotonic(),
                    name="scanning_sessions",
                    done=self.scanned,
                    total=self.total,
                    scope=self.scope,
                )
            )


class _ProbePayload(msgspec.Struct):
    type: str | None = None
    role: str | None = None
    id: str | None = None
    thread_source: str | None = None
    agent_role: str | None = None
    source: object | None = None


class _ProbeMessage(msgspec.Struct):
    role: str | None = None


class _ProbeRecord(msgspec.Struct):
    timestamp: str | None = None
    type: str | None = None
    payload: _ProbePayload | None = None
    message: _ProbeMessage | None = None
    source_tool_assistant_uuid: str | None = msgspec.field(
        default=None,
        name="sourceToolAssistantUUID",
    )
    is_sidechain: bool | None = msgspec.field(default=None, name="isSidechain")
    agent_id: str | None = msgspec.field(default=None, name="agentId")
    session_id: str | None = msgspec.field(default=None, name="sessionId")
    attribution_agent: str | None = msgspec.field(default=None, name="attributionAgent")


class _ProbeContentItem(msgspec.Struct):
    text: str | None = None


class _ProbeContentPayload(msgspec.Struct):
    content: list[_ProbeContentItem] | None = None


class _ProbeContentRecord(msgspec.Struct):
    payload: _ProbeContentPayload | None = None


_PROBE_RECORD_DECODER = msgspec.json.Decoder(_ProbeRecord)
_PROBE_CONTENT_DECODER = msgspec.json.Decoder(_ProbeContentRecord)


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
    reporter: ProgressReporter = NULL_REPORTER,
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
    reporter.emit(PrepareStarted(at=time.monotonic(), sources=tuple(spec.source for spec in specs)))
    prepared_at_local = _timestamp_for_target(target, prepared_at)
    parsed_sessions = tuple(_selected_sessions(specs, target, reporter=reporter))
    reporter.emit(
        PrepareStep(at=time.monotonic(), name="discovering", done=len(parsed_sessions), total=None)
    )
    project_count = len({session.project.key for session in parsed_sessions})
    reporter.emit(
        PrepareStep(at=time.monotonic(), name="assigning_projects", done=project_count, total=None)
    )
    _write_prepared_workspace(
        target=target,
        workspace_path=workspace_path,
        audit_path=audit_path,
        source_specs=specs,
        sessions=parsed_sessions,
        prepared_at=prepared_at_local,
    )

    message = (
        f"Prepared workspace {workspace_path} "
        f"with {project_count} project(s) and {len(parsed_sessions)} session(s)."
    )
    reporter.emit(
        PrepareFinished(at=time.monotonic(), projects=project_count, sessions=len(parsed_sessions))
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
    *,
    reporter: ProgressReporter = NULL_REPORTER,
) -> Iterable[ParsedSession]:
    per_source = [(spec, _jsonl_source_files(spec.root)) for spec in source_specs]
    for spec, source_paths in per_source:
        scope = _source_scope(spec)
        progress = _ScanProgress(
            total=len(source_paths),
            stride=max(1, len(source_paths) // 100),
            reporter=reporter,
            scope=scope,
        )
        probe = _probe_source_files(
            source_paths=source_paths,
            source=spec.source,
            root=spec.root,
            target=target,
            progress=progress,
        )
        selected_count = 0
        for source_path in source_paths:
            if source_path not in probe.candidate_root_paths:
                continue
            parsed = _parse_session_file(source_path=source_path, spec=spec, target=target)
            if parsed is not None:
                selected_count += 1
                yield _with_target_subagents(parsed, probe.subagent_index)
        reporter.emit(
            PrepareStep(
                at=time.monotonic(),
                name="discovering",
                done=selected_count,
                total=None,
                scope=scope,
            )
        )


def _jsonl_source_files(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root,) if root.suffix == ".jsonl" else ()
    if not root.exists():
        return ()
    return tuple(sorted(root.rglob("*.jsonl"), key=lambda path: path.as_posix()))


def _source_scope(spec: SourceSpec) -> str:
    return f"{spec.source} {_display_path(spec.root)}"


def _display_path(path: Path) -> str:
    expanded = path.expanduser()
    try:
        relative = expanded.relative_to(Path.home())
    except ValueError:
        return str(path)
    return f"~/{relative.as_posix()}"


def _probe_source_files(
    *,
    source_paths: tuple[Path, ...],
    source: SourceName,
    root: Path,
    target: ReportTarget,
    progress: _ScanProgress,
) -> _SourceProbe:
    candidate_root_paths: set[Path] = set()
    subagents: list[_SourceSubagent] = []
    for source_path in source_paths:
        is_candidate, subagent = _probe_source_file(
            source_path=source_path,
            source=source,
            root=root,
            target=target,
        )
        progress.advance()
        if is_candidate:
            candidate_root_paths.add(source_path)
        if subagent is not None:
            subagents.append(subagent)
    return _SourceProbe(
        candidate_root_paths=frozenset(candidate_root_paths),
        subagent_index=_source_subagent_index_from_items(subagents),
    )


def _probe_source_file(
    *,
    source_path: Path,
    source: SourceName,
    root: Path,
    target: ReportTarget,
) -> tuple[bool, _SourceSubagent | None]:
    subagent_state = _SubagentMetadata(
        is_subagent=_path_identifies_subagent_session(
            source_path=source_path,
            source=source,
            root=root,
        ),
        parent_source_session_id=_path_parent_session_id(source_path=source_path, root=root)
        if source == "claude-code"
        else None,
    )
    candidate_root = False
    with source_path.open("rb") as handle:
        for line in handle:
            record = _probe_record_from_line(line)
            if record is None:
                candidate_root = True
                continue
            _record_probe_subagent_metadata(subagent_state, record, source)
            if _probe_is_target_trigger(record, line, source, target):
                candidate_root = True

    subagent = (
        _source_subagent_from_probe(source_path, subagent_state)
        if subagent_state.is_subagent
        else None
    )
    return candidate_root and not subagent_state.is_subagent, subagent


def _source_subagent_index_from_items(subagents: Iterable[_SourceSubagent]) -> _SourceSubagentIndex:
    by_parent_and_id: dict[tuple[str, str], _SourceSubagent] = {}
    by_id_builder: dict[str, list[_SourceSubagent]] = {}
    for subagent in subagents:
        by_id_builder.setdefault(subagent.source_session_id, []).append(subagent)
        if subagent.parent_source_session_id is not None:
            by_parent_and_id[(subagent.parent_source_session_id, subagent.source_session_id)] = (
                subagent
            )
    by_id = {key: tuple(value) for key, value in by_id_builder.items()}
    return _SourceSubagentIndex(by_parent_and_id=by_parent_and_id, by_id=by_id)


def _probe_record_from_line(line: bytes) -> _ProbeRecord | None:
    try:
        return _PROBE_RECORD_DECODER.decode(line)
    except msgspec.DecodeError:
        return None


def _probe_is_target_trigger(
    record: _ProbeRecord,
    line: bytes,
    source: SourceName,
    target: ReportTarget,
) -> bool:
    timestamp = _parse_timestamp(record.timestamp)
    if timestamp is None:
        return False
    if not target.report_window_utc.start <= timestamp < target.report_window_utc.end:
        return False
    return _probe_is_human_trigger(record, line, source)


def _probe_is_human_trigger(record: _ProbeRecord, line: bytes, source: SourceName) -> bool:
    if source == "codex":
        return _probe_is_codex_human_trigger(record, line)
    return _probe_is_claude_human_trigger(record)


def _probe_is_codex_human_trigger(record: _ProbeRecord, line: bytes) -> bool:
    payload = record.payload
    if payload is None:
        return False
    if record.type == "event_msg" and payload.type == "user_message":
        return True
    if record.type == "response_item":
        if payload.role != "user" or payload.type != "message":
            return False
        text = _probe_codex_message_text(line)
        return text is None or not text.startswith(_CODEX_SOURCE_CONTEXT_PREFIXES)
    return False


def _probe_codex_message_text(line: bytes) -> str | None:
    try:
        record = _PROBE_CONTENT_DECODER.decode(line)
    except msgspec.DecodeError:
        return None
    payload = record.payload
    if payload is None or payload.content is None:
        return ""
    for item in payload.content:
        if item.text is not None and item.text.strip():
            return item.text.strip()
    return ""


def _probe_is_claude_human_trigger(record: _ProbeRecord) -> bool:
    message = record.message
    if record.type != "user" or message is None or message.role != "user":
        return False
    if record.source_tool_assistant_uuid is not None:
        return False
    return record.is_sidechain is not True


def _record_probe_subagent_metadata(
    state: _SubagentMetadata,
    record: _ProbeRecord,
    source: SourceName,
) -> None:
    if source == "codex":
        _record_probe_codex_subagent_metadata(state, record)
    else:
        _record_probe_claude_subagent_metadata(state, record)


def _record_probe_codex_subagent_metadata(
    state: _SubagentMetadata,
    record: _ProbeRecord,
) -> None:
    if record.type != "session_meta":
        return
    payload = record.payload
    if payload is None:
        return
    if payload.id is not None:
        state.source_session_id = payload.id
    if payload.agent_role is not None:
        state.agent_role = payload.agent_role
    if payload.thread_source == "subagent":
        state.is_subagent = True
    subagent = _probe_object_value(payload.source, "subagent")
    if subagent is None:
        return
    thread_spawn = _probe_object_value(subagent, "thread_spawn")
    if thread_spawn is None:
        return
    parent_thread_id = _probe_string_value(_probe_object_value(thread_spawn, "parent_thread_id"))
    if parent_thread_id is not None:
        state.is_subagent = True
        state.parent_source_session_id = parent_thread_id
    agent_role = _probe_string_value(_probe_object_value(thread_spawn, "agent_role"))
    if agent_role is not None and state.agent_role is None:
        state.agent_role = agent_role


def _record_probe_claude_subagent_metadata(
    state: _SubagentMetadata,
    record: _ProbeRecord,
) -> None:
    if record.is_sidechain:
        state.is_subagent = True
    if record.agent_id is not None:
        state.source_session_id = record.agent_id
    if record.session_id is not None:
        state.parent_source_session_id = record.session_id
    if record.attribution_agent is not None:
        state.agent_role = record.attribution_agent


def _source_subagent_from_probe(
    source_path: Path,
    state: _SubagentMetadata,
) -> _SourceSubagent:
    return _SourceSubagent(
        source_path=source_path,
        source_session_id=state.source_session_id or source_path.stem,
        parent_source_session_id=state.parent_source_session_id,
        agent_role=state.agent_role,
    )


def _probe_object_value(value: object | None, key: str) -> object | None:
    if not isinstance(value, dict):
        return None
    return cast("dict[object, object]", value).get(key)


def _probe_string_value(value: object | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


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
    state = _ParseState(
        source_path=source_path,
        source=spec.source,
        is_subagent=_path_identifies_subagent_session(
            source_path=source_path,
            source=spec.source,
            root=spec.root,
        ),
    )

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
        state.record_timestamp(timestamp)
        if _is_human_trigger(record, spec.source) and (
            not state.triggers or state.triggers[-1].line_number != line_number - 1
        ):
            state.triggers.append(_TriggerLine(line_number=line_number, timestamp=timestamp))

    turns = _build_turns(
        triggers=state.triggers,
        target=target,
        lines=lines,
        source=spec.source,
        total_lines=len(lines),
    )
    if state.is_subagent or not turns:
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
        turns=turns,
        total_lines=len(lines),
        source_checksum_sha256=checksum,
        malformed_line_count=state.malformed_line_count,
        untimestamped_record_count=state.untimestamped_record_count,
        non_monotonic_timestamp_count=state.non_monotonic_timestamp_count,
        first_event_at=serialize_datetime(first_event_at),
        last_event_at=serialize_datetime(last_event_at),
    )


@dataclass(frozen=True)
class _TriggerLine:
    line_number: int
    timestamp: datetime


@dataclass
class _ParseState:
    source_path: Path
    source: SourceName
    is_subagent: bool
    source_session_id: str | None = None
    codex_session_meta_cwd: str | None = None
    codex_turn_context_cwd: str | None = None
    claude_cwd: str | None = None
    malformed_line_count: int = 0
    untimestamped_record_count: int = 0
    non_monotonic_timestamp_count: int = 0
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    previous_event_at: datetime | None = None
    triggers: list[_TriggerLine] = field(default_factory=list)

    def record_timestamp(self, timestamp: datetime) -> None:
        if self.previous_event_at is not None and timestamp < self.previous_event_at:
            self.non_monotonic_timestamp_count += 1
        self.previous_event_at = timestamp
        self.first_event_at = _earliest(self.first_event_at, timestamp)
        self.last_event_at = _latest(self.last_event_at, timestamp)


_CODEX_SOURCE_CONTEXT_PREFIXES = (
    "<environment_context>",
    "# AGENTS.md",
    "<turn_aborted>",
    "<subagent_notification>",
    "<INSTRUCTIONS>",
)


def _is_human_trigger(record: JsonObject, source: SourceName) -> bool:
    if source == "codex":
        return _is_codex_human_trigger(record)
    return _is_claude_human_trigger(record)


def _is_codex_human_trigger(record: JsonObject) -> bool:
    record_type = _string_value(record, "type")
    payload = _object_value(record, "payload")
    if payload is None:
        return False
    if record_type == "event_msg" and _string_value(payload, "type") == "user_message":
        return True
    if record_type == "response_item":
        if _string_value(payload, "role") != "user" or _string_value(payload, "type") != "message":
            return False
        text = _codex_message_text(payload)
        return not text.startswith(_CODEX_SOURCE_CONTEXT_PREFIXES)
    return False


def _codex_message_text(payload: JsonObject) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    for item in content:
        if isinstance(item, dict):
            text = cast("JsonObject", item).get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _is_claude_human_trigger(record: JsonObject) -> bool:
    if _string_value(record, "type") != "user":
        return False
    message = _object_value(record, "message")
    if message is None or _string_value(message, "role") != "user":
        return False
    if _string_value(record, "sourceToolAssistantUUID") is not None:
        return False
    return not _bool_value(record, "isSidechain")


def _build_turns(
    *,
    triggers: list[_TriggerLine],
    target: ReportTarget,
    lines: list[str],
    source: SourceName,
    total_lines: int,
) -> tuple[ParsedTurn, ...]:
    in_window = [
        (i, t)
        for i, t in enumerate(triggers)
        if target.report_window_utc.start <= t.timestamp < target.report_window_utc.end
    ]
    if not in_window:
        return ()
    result: list[ParsedTurn] = []
    for position, (idx, trigger) in enumerate(in_window, start=1):
        next_trigger = triggers[idx + 1] if idx + 1 < len(triggers) else None
        if next_trigger is not None:
            turn_end = _turn_end_before_next_trigger(lines, next_trigger.line_number, source)
        else:
            turn_end = total_lines
        result.append(
            ParsedTurn(
                turn_ref=_turn_ref(position),
                turn_start_line=trigger.line_number,
                turn_end_line=turn_end,
            )
        )
    return tuple(result)


def _turn_ref(position: int) -> str:
    return f"T{position:04d}"


def _turn_end_before_next_trigger(
    lines: list[str],
    next_trigger_line: int,
    source: SourceName,
) -> int:
    for line_number in range(next_trigger_line - 1, 0, -1):
        record = _json_object_from_line(lines[line_number - 1])
        if record is None or not _is_pre_trigger_scaffolding(record, source):
            return line_number
    return next_trigger_line - 1


def _is_pre_trigger_scaffolding(record: JsonObject, source: SourceName) -> bool:
    if source != "codex":
        return False
    record_type = _string_value(record, "type")
    payload = _object_value(record, "payload")
    if record_type == "turn_context":
        return True
    if record_type == "event_msg" and payload is not None:
        ptype = _string_value(payload, "type")
        if ptype in ("task_started", "turn_started"):
            return True
    if record_type == "response_item" and payload is not None:
        if _string_value(payload, "role") == "developer":
            return True
        if _string_value(payload, "role") == "user" and _string_value(payload, "type") == "message":
            text = _codex_message_text(payload)
            if text.startswith(_CODEX_SOURCE_CONTEXT_PREFIXES):
                return True
    return False


def _record_session_metadata(state: _ParseState, record: JsonObject) -> None:
    if state.source == "codex":
        _record_codex_metadata(state, record)
    else:
        if _bool_value(record, "isSidechain"):  # pragma: no cover
            state.is_subagent = True
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
        if _string_value(payload, "thread_source") == "subagent":  # pragma: no cover
            state.is_subagent = True
        source = _object_value(payload, "source")
        if source is not None:  # pragma: no cover
            subagent = _object_value(source, "subagent")
            if subagent is not None:
                thread_spawn = _object_value(subagent, "thread_spawn")
                if thread_spawn is not None and _string_value(thread_spawn, "parent_thread_id"):
                    state.is_subagent = True
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
    return source_path.stem


def _last_subagents_index(parts: tuple[str, ...]) -> int | None:
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] == "subagents":
            return index
    return None


def _path_identifies_subagent_session(*, source_path: Path, source: SourceName, root: Path) -> bool:
    """Return whether the source path is a source-native subagent transcript.

    Codex subagents are normally detected from `session_meta` rather than path. Claude Code stores
    sidechain transcripts as `<project>/<parent-session-id>/subagents/agent-<agent-id>.jsonl`,
    beside the parent `<parent-session-id>.jsonl` file, so any Claude JSONL path with a
    `subagents` component is excluded from root-session discovery.
    """
    if source != "claude-code":
        return False
    return "subagents" in _relative_parts(source_path, root)


@dataclass
class _SubagentMetadata:
    is_subagent: bool = False
    source_session_id: str | None = None
    parent_source_session_id: str | None = None
    agent_role: str | None = None


def _path_parent_session_id(*, source_path: Path, root: Path) -> str | None:
    parts = _relative_parts(source_path, root)
    subagents_index = _last_subagents_index(parts)
    if subagents_index is None or subagents_index == 0:
        return None
    return parts[subagents_index - 1]


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    return path.relative_to(root).parts


def _with_target_subagents(
    session: ParsedSession,
    subagent_index: _SourceSubagentIndex,
) -> ParsedSession:
    references = (
        _codex_parent_subagent_references(session)
        if session.source == "codex"
        else _claude_parent_subagent_references(session)
    )
    new_turns: list[ParsedTurn] = []
    any_subagents = False
    for turn in session.turns:
        turn_subagents = _subagents_for_turn(
            turn, references, subagent_index, session.source_session_id
        )
        if turn_subagents:
            any_subagents = True
        new_turns.append(
            ParsedTurn(
                turn_ref=turn.turn_ref,
                turn_start_line=turn.turn_start_line,
                turn_end_line=turn.turn_end_line,
                target_subagents=turn_subagents,
            )
        )
    if not any_subagents:
        return session
    return ParsedSession(
        source=session.source,
        source_path=session.source_path,
        source_session_id=session.source_session_id,
        project=session.project,
        turns=tuple(new_turns),
        total_lines=session.total_lines,
        source_checksum_sha256=session.source_checksum_sha256,
        malformed_line_count=session.malformed_line_count,
        untimestamped_record_count=session.untimestamped_record_count,
        non_monotonic_timestamp_count=session.non_monotonic_timestamp_count,
        first_event_at=session.first_event_at,
        last_event_at=session.last_event_at,
    )


def _subagents_for_turn(
    turn: ParsedTurn,
    references: tuple[_ParentSubagentReference, ...],
    subagent_index: _SourceSubagentIndex,
    parent_session_id: str,
) -> tuple[TargetSubagent, ...]:
    result: list[TargetSubagent] = []
    for reference in references:
        if not _reference_in_turn(turn, reference):
            continue
        source_subagent = subagent_index.find(
            parent_source_session_id=parent_session_id,
            source_session_id=reference.source_session_id,
        )
        if source_subagent is None:
            continue
        result.append(
            TargetSubagent(
                source_path=source_subagent.source_path,
                session_file=source_subagent.source_path.name,
                source_session_id=source_subagent.source_session_id,
                agent_role=reference.agent_role or source_subagent.agent_role,
                parent_spawn_line=reference.parent_spawn_line,
                parent_result_line=reference.parent_result_line,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.parent_spawn_line or item.parent_result_line or 0,
                item.source_session_id,
            ),
        )
    )


def _reference_in_turn(
    turn: ParsedTurn,
    reference: _ParentSubagentReference,
) -> bool:
    return _line_in_turn(turn, reference.parent_spawn_line) or _line_in_turn(
        turn, reference.parent_result_line
    )


def _line_in_turn(turn: ParsedTurn, line_number: int | None) -> bool:
    if line_number is None:
        return False
    return turn.turn_start_line <= line_number <= turn.turn_end_line


def _codex_parent_subagent_references(
    session: ParsedSession,
) -> tuple[_ParentSubagentReference, ...]:
    """Find Codex parent spawn/result lines that refer to subagent thread ids.

    A Codex `spawn_agent` function call line contains the delegation prompt and optional
    `agent_type`; the matching function-call output exposes `agent_id`. A later `wait_agent`
    output with `status: {<agent_id>: {completed: ...}}` is the result line.
    """
    spawn_calls: dict[str, _PendingSubagentSpawn] = {}
    references: dict[str, _MutableParentSubagentReference] = {}
    for line_number, line in enumerate(
        session.source_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        record = _json_object_from_line(line)
        if record is None:
            continue
        payload = _codex_response_item_payload(record)
        if payload is None:
            continue
        _record_codex_spawn_call(spawn_calls, payload, line_number=line_number)
        _record_codex_function_output(
            references,
            spawn_calls,
            payload,
            line_number=line_number,
        )
    return _sorted_parent_references(references)


def _codex_response_item_payload(record: JsonObject) -> JsonObject | None:
    if _string_value(record, "type") != "response_item":
        return None
    return _object_value(record, "payload")


def _record_codex_spawn_call(
    spawn_calls: dict[str, _PendingSubagentSpawn],
    payload: JsonObject,
    *,
    line_number: int,
) -> None:
    if _string_value(payload, "type") != "function_call":
        return
    if _string_value(payload, "name") != "spawn_agent":
        return
    call_id = _string_value(payload, "call_id")
    if call_id is None:
        return
    arguments = _json_object_from_string(_string_value(payload, "arguments"))
    agent_role = _string_value(arguments, "agent_type") if arguments is not None else None
    spawn_calls[call_id] = _PendingSubagentSpawn(
        line_number=line_number,
        agent_role=agent_role,
    )


def _record_codex_function_output(
    references: dict[str, _MutableParentSubagentReference],
    spawn_calls: dict[str, _PendingSubagentSpawn],
    payload: JsonObject,
    *,
    line_number: int,
) -> None:
    if _string_value(payload, "type") != "function_call_output":
        return
    output = _json_object_from_string(_string_value(payload, "output"))
    if output is None:
        return
    _record_codex_spawn_output(references, spawn_calls, payload, output)
    status = _object_value(output, "status")
    if status is not None:
        _record_codex_wait_results(references, status, result_line=line_number)


def _record_codex_spawn_output(
    references: dict[str, _MutableParentSubagentReference],
    spawn_calls: dict[str, _PendingSubagentSpawn],
    payload: JsonObject,
    output: JsonObject,
) -> None:
    call_id = _string_value(payload, "call_id")
    if call_id is None:
        return
    pending_spawn = spawn_calls.get(call_id)
    agent_id = _string_value(output, "agent_id")
    if pending_spawn is None or agent_id is None:
        return
    reference = references.setdefault(
        agent_id,
        _MutableParentSubagentReference(source_session_id=agent_id),
    )
    reference.parent_spawn_line = pending_spawn.line_number
    if pending_spawn.agent_role is not None:
        reference.agent_role = pending_spawn.agent_role


def _record_codex_wait_results(
    references: dict[str, _MutableParentSubagentReference],
    status: JsonObject,
    *,
    result_line: int,
) -> None:
    for agent_id, value in status.items():
        if not isinstance(value, dict):
            continue
        agent_status = cast("JsonObject", value)
        if "completed" not in agent_status:
            continue
        reference = references.setdefault(
            agent_id,
            _MutableParentSubagentReference(source_session_id=agent_id),
        )
        reference.parent_result_line = result_line


def _claude_parent_subagent_references(
    session: ParsedSession,
) -> tuple[_ParentSubagentReference, ...]:
    """Find Claude Code parent Agent-tool spawn/result lines.

    Claude Code launches subagents with an assistant `Agent` tool_use. The child id is returned by
    a following user tool result in top-level `toolUseResult.agentId`; synchronous completed
    results use that line as `parent_result_line`, while async launches are completed by later
    task-notification attachments containing the same agent id.
    """
    pending_by_tool_use_id: dict[str, _PendingSubagentSpawn] = {}
    references: dict[str, _MutableParentSubagentReference] = {}
    for line_number, line in enumerate(
        session.source_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        record = _json_object_from_line(line)
        if record is None:
            continue
        _record_claude_agent_tool_uses(pending_by_tool_use_id, record, line_number=line_number)
        _record_claude_tool_result(
            references,
            pending_by_tool_use_id,
            record,
            line_number=line_number,
        )
        _record_claude_task_notification(references, record, line_number=line_number)
    return _sorted_parent_references(references)


def _record_claude_agent_tool_uses(
    pending_by_tool_use_id: dict[str, _PendingSubagentSpawn],
    record: JsonObject,
    *,
    line_number: int,
) -> None:
    message = _object_value(record, "message")
    if message is None:
        return
    for content_item in _object_list_value(message, "content"):
        if _string_value(content_item, "type") != "tool_use":
            continue
        if _string_value(content_item, "name") != "Agent":
            continue
        tool_use_id = _string_value(content_item, "id")
        if tool_use_id is None:
            continue
        tool_input = _object_value(content_item, "input")
        pending_by_tool_use_id[tool_use_id] = _PendingSubagentSpawn(
            line_number=line_number,
            agent_role=_string_value(tool_input, "subagent_type")
            if tool_input is not None
            else None,
        )


def _record_claude_tool_result(
    references: dict[str, _MutableParentSubagentReference],
    pending_by_tool_use_id: dict[str, _PendingSubagentSpawn],
    record: JsonObject,
    *,
    line_number: int,
) -> None:
    tool_use_id = _claude_tool_result_id(record)
    tool_use_result = _object_value(record, "toolUseResult")
    if tool_use_result is None:
        return
    agent_id = _string_value(tool_use_result, "agentId")
    if agent_id is None:
        return
    reference = references.setdefault(
        agent_id,
        _MutableParentSubagentReference(source_session_id=agent_id),
    )
    if tool_use_id is not None:
        pending_spawn = pending_by_tool_use_id.get(tool_use_id)
        if pending_spawn is not None:
            reference.parent_spawn_line = pending_spawn.line_number
            if pending_spawn.agent_role is not None:
                reference.agent_role = pending_spawn.agent_role
    if _string_value(tool_use_result, "status") == "completed":
        reference.parent_result_line = line_number


def _record_claude_task_notification(
    references: dict[str, _MutableParentSubagentReference],
    record: JsonObject,
    *,
    line_number: int,
) -> None:
    agent_id = _claude_task_notification_agent_id(record) or _claude_result_message_agent_id(
        record,
        references.keys(),
    )
    if agent_id is None:
        return
    reference = references.setdefault(
        agent_id,
        _MutableParentSubagentReference(source_session_id=agent_id),
    )
    reference.parent_result_line = line_number


def _claude_tool_result_id(record: JsonObject) -> str | None:
    message = _object_value(record, "message")
    if message is None:
        return None
    for content_item in _object_list_value(message, "content"):
        if _string_value(content_item, "type") == "tool_result":
            return _string_value(content_item, "tool_use_id")
    return None


def _claude_task_notification_agent_id(record: JsonObject) -> str | None:
    attachment = _object_value(record, "attachment")
    if attachment is None:
        return None
    if _string_value(attachment, "commandMode") != "task-notification":
        return None
    prompt = _string_value(attachment, "prompt")
    if prompt is None:
        return None
    match = re.search(r"agentId:\s*([^\s<]+)", prompt)
    if match is None:
        return None
    return match.group(1)


def _claude_result_message_agent_id(
    record: JsonObject,
    known_agent_ids: Iterable[str],
) -> str | None:
    if _object_value(record, "toolUseResult") is not None:
        return None
    message = _object_value(record, "message")
    if message is None:
        return None
    message_text = "\n".join(_json_string_fragments(message))
    for agent_id in sorted(known_agent_ids):
        if agent_id in message_text:
            return agent_id
    return None


def _json_string_fragments(value: JsonValue) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(fragment for item in value for fragment in _json_string_fragments(item))
    if isinstance(value, dict):
        json_object = cast("JsonObject", value)
        return tuple(
            fragment for item in json_object.values() for fragment in _json_string_fragments(item)
        )
    return ()


def _sorted_parent_references(
    references: dict[str, _MutableParentSubagentReference],
) -> tuple[_ParentSubagentReference, ...]:
    return tuple(
        reference.to_reference()
        for _, reference in sorted(
            references.items(),
            key=lambda item: (
                item[1].parent_spawn_line or item[1].parent_result_line or 0,
                item[0],
            ),
        )
    )


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


def _write_project_workspaces(
    workspace_path: Path,
    sessions: tuple[ParsedSession, ...],
) -> None:
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
        _copy_target_subagents(project_dir, session, seen_destinations)
        index_rows.append(_session_index_row(session, session_ref=f"S{position:04d}"))

    _write_jsonl(project_dir / "sessions.index.jsonl", index_rows)


def _copy_target_subagents(
    project_dir: Path,
    session: ParsedSession,
    seen_destinations: set[tuple[str, str]],
) -> None:
    copied_files: set[str] = set()
    for subagent in session.target_subagents:
        if subagent.session_file in copied_files:
            continue
        copied_files.add(subagent.session_file)
        subagent_path = _subagent_relative_path(session)
        session_path = f"{subagent_path}/{subagent.session_file}"
        destination_key = (session.project.key, session_path)
        if destination_key in seen_destinations:
            raise PromptDiaryError(_filename_collision_message(session, session_path))
        seen_destinations.add(destination_key)
        destination = project_dir / session_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(subagent.source_path, destination)


def _session_relative_path(session: ParsedSession) -> str:
    return f"sessions/{session.source}/{session.session_filename}"


def _subagent_relative_path(session: ParsedSession) -> str:
    if not session.target_subagents:
        return ""
    return f"sessions/{session.source}/subagents/{session.source_session_id}"


def _session_index_row(session: ParsedSession, *, session_ref: str) -> JsonObject:
    return {
        "session_ref": session_ref,
        "source": session.source,
        "source_session_id": session.source_session_id,
        "session_path": _session_relative_path(session),
        "target_start_line": session.target_start_line,
        "target_end_line": session.target_end_line,
        "subagent_path": _subagent_relative_path(session),
        "turns": [_turn_index_json(turn) for turn in session.turns],
    }


def _turn_index_json(turn: ParsedTurn) -> JsonObject:
    return {
        "turn_ref": turn.turn_ref,
        "turn_start_line": turn.turn_start_line,
        "turn_end_line": turn.turn_end_line,
        "target_subagents": [
            _target_subagent_index_json(subagent) for subagent in turn.target_subagents
        ],
    }


def _target_subagent_index_json(subagent: TargetSubagent) -> JsonObject:
    return {
        "session_file": subagent.session_file,
        "source_session_id": subagent.source_session_id,
        "agent_role": subagent.agent_role,
        "parent_spawn_line": subagent.parent_spawn_line,
        "parent_result_line": subagent.parent_result_line,
        "association": "spawned_or_returned_in_target_span",
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


def _json_object_from_string(value: str | None) -> JsonObject | None:
    if value is None:
        return None
    try:
        raw = cast("object", json.loads(value))
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


def _bool_value(record: JsonObject, key: str) -> bool:
    return record.get(key) is True


def _object_value(record: JsonObject, key: str) -> JsonObject | None:
    value = record.get(key)
    if isinstance(value, dict):
        return cast("JsonObject", value)
    return None


def _object_list_value(record: JsonObject, key: str) -> tuple[JsonObject, ...]:
    value = record.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(cast("JsonObject", item) for item in value if isinstance(item, dict))


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
