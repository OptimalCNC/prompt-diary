"""Typed prepared-workspace inputs for generation pipeline planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from prompt_diary.models import JsonObject

_TURN_REF_RE = re.compile(r"^T\d{4}$")


@dataclass(frozen=True)
class LineSpan:
    """Inclusive 1-based line span inside a copied session file."""

    start: int
    end: int


@dataclass(frozen=True)
class IndexedTurn:
    """One trigger-owned work unit from a project session index row."""

    turn_ref: str
    span: LineSpan


@dataclass(frozen=True)
class IndexedSession:
    """One copied root session indexed for generation."""

    session_ref: str
    source: str
    source_session_id: str
    session_path: PurePosixPath
    target_span: LineSpan
    turns: tuple[IndexedTurn, ...]


@dataclass(frozen=True)
class PreparedProject:
    """One prepared project scope."""

    project_key: str
    project_label: str
    sessions: tuple[IndexedSession, ...]


@dataclass(frozen=True)
class PreparedWorkspace:
    """Prepared report workspace metadata needed by generation planning."""

    workspace_path: Path
    report_date: str
    status: str
    timezone: str
    projects: tuple[PreparedProject, ...]


def load_prepared_workspace(workspace_path: Path) -> PreparedWorkspace:
    """Parse prepared workspace indexes into typed generation planning inputs."""
    metadata_path = workspace_path / "metadata.json"
    metadata = _load_json_object(metadata_path)
    return PreparedWorkspace(
        workspace_path=workspace_path,
        report_date=_required_string(metadata, "report_date", path=metadata_path),
        status=_required_string(metadata, "status", path=metadata_path),
        timezone=_required_string(metadata, "timezone", path=metadata_path),
        projects=_load_projects(workspace_path),
    )


def _load_projects(workspace_path: Path) -> tuple[PreparedProject, ...]:
    projects_root = workspace_path / "projects"
    if not projects_root.exists():
        return ()

    return tuple(
        _load_project(project_dir)
        for project_dir in sorted(projects_root.iterdir(), key=lambda path: path.name)
        if project_dir.is_dir()
    )


def _load_project(project_dir: Path) -> PreparedProject:
    project_path = project_dir / "project.json"
    project_json = _load_json_object(project_path)
    project_key = _required_string(project_json, "project_key", path=project_path)
    if project_key != project_dir.name:
        raise PromptDiaryError(
            _project_key_mismatch_message(project_path, project_key, project_dir)
        )
    return PreparedProject(
        project_key=project_key,
        project_label=_required_string(project_json, "project_label", path=project_path),
        sessions=_load_sessions_index(project_dir / "sessions.index.jsonl", project_dir),
    )


def _load_sessions_index(index_path: Path, project_dir: Path) -> tuple[IndexedSession, ...]:
    if not index_path.exists():
        return ()

    sessions: list[IndexedSession] = []
    seen_session_refs: set[str] = set()
    lines = index_path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        raw = _json_object_from_text(line, path=index_path, line_number=line_number)
        session = _indexed_session_from_json(
            raw,
            project_dir=project_dir,
            path=index_path,
            line_number=line_number,
        )
        if session.session_ref in seen_session_refs:
            raise PromptDiaryError(
                _duplicate_session_ref_message(index_path, line_number, session.session_ref)
            )
        seen_session_refs.add(session.session_ref)
        sessions.append(session)
    return tuple(sessions)


def _indexed_session_from_json(
    value: JsonObject,
    *,
    project_dir: Path,
    path: Path,
    line_number: int,
) -> IndexedSession:
    target_start = _required_int(value, "target_start_line", path=path, line_number=line_number)
    target_end = _required_int(value, "target_end_line", path=path, line_number=line_number)
    return IndexedSession(
        session_ref=_required_string(value, "session_ref", path=path, line_number=line_number),
        source=_required_string(value, "source", path=path, line_number=line_number),
        source_session_id=_required_string(
            value,
            "source_session_id",
            path=path,
            line_number=line_number,
        ),
        session_path=_required_session_path(
            value,
            project_dir=project_dir,
            path=path,
            line_number=line_number,
        ),
        target_span=_line_span(
            start=target_start,
            end=target_end,
            path=path,
            line_number=line_number,
            label="target span",
        ),
        turns=_required_turns(value, path=path, line_number=line_number),
    )


def _required_turns(
    value: JsonObject,
    *,
    path: Path,
    line_number: int,
) -> tuple[IndexedTurn, ...]:
    raw_turns = value.get("turns")
    if not isinstance(raw_turns, list):
        raise PromptDiaryError(_field_message(path, line_number, "turns", "array"))

    turns: list[IndexedTurn] = []
    seen_turn_refs: set[str] = set()
    for position, raw_turn in enumerate(raw_turns, start=1):
        if not isinstance(raw_turn, dict):
            raise PromptDiaryError(_turn_object_message(path, line_number, position))
        turn = cast("JsonObject", raw_turn)
        turn_ref = _required_string(
            turn,
            "turn_ref",
            path=path,
            line_number=line_number,
        )
        if _TURN_REF_RE.fullmatch(turn_ref) is None:
            raise PromptDiaryError(
                _malformed_turn_ref_message(path, line_number, position, turn_ref)
            )
        if turn_ref in seen_turn_refs:
            raise PromptDiaryError(_duplicate_turn_ref_message(path, line_number, turn_ref))
        seen_turn_refs.add(turn_ref)
        turn_start = _required_int(
            turn,
            "turn_start_line",
            path=path,
            line_number=line_number,
        )
        turn_end = _required_int(
            turn,
            "turn_end_line",
            path=path,
            line_number=line_number,
        )
        turns.append(
            IndexedTurn(
                turn_ref=turn_ref,
                span=_line_span(
                    start=turn_start,
                    end=turn_end,
                    path=path,
                    line_number=line_number,
                    label=f"turn {turn_ref}",
                ),
            )
        )
    return tuple(turns)


def _required_session_path(
    value: JsonObject,
    *,
    project_dir: Path,
    path: Path,
    line_number: int,
) -> PurePosixPath:
    text = _required_string(value, "session_path", path=path, line_number=line_number)
    session_path = PurePosixPath(text)
    if (
        session_path.is_absolute()
        or ".." in session_path.parts
        or session_path.parts[:1] != ("sessions",)
    ):
        raise PromptDiaryError(_session_path_message(path, line_number))
    session_file = (project_dir / session_path).resolve()
    sessions_root = (project_dir / "sessions").resolve()
    if not _path_is_relative_to(session_file, sessions_root):
        raise PromptDiaryError(_session_path_escape_message(path, line_number, sessions_root))
    return session_path


def _line_span(
    *,
    start: int,
    end: int,
    path: Path,
    line_number: int,
    label: str,
) -> LineSpan:
    if start < 1:
        raise PromptDiaryError(_positive_line_span_message(path, line_number, label))
    if end < start:
        raise PromptDiaryError(_ordered_line_span_message(path, line_number, label))
    return LineSpan(start=start, end=end)


def _load_json_object(path: Path) -> JsonObject:
    if not path.exists():
        raise PromptDiaryError(_missing_json_file_message(path))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptDiaryError(_invalid_json_message(path, exc.msg)) from exc
    if not isinstance(raw, dict):
        raise PromptDiaryError(_json_object_message(path))
    return cast("JsonObject", raw)


def _json_object_from_text(text: str, *, path: Path, line_number: int) -> JsonObject:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PromptDiaryError(_invalid_jsonl_message(path, line_number, exc.msg)) from exc
    if not isinstance(raw, dict):
        raise PromptDiaryError(_jsonl_object_message(path, line_number))
    return cast("JsonObject", raw)


def _required_string(
    value: JsonObject,
    field: str,
    *,
    path: Path,
    line_number: int | None = None,
) -> str:
    raw = value.get(field)
    if isinstance(raw, str):
        return raw
    raise PromptDiaryError(_field_message(path, line_number, field, "string"))


def _required_int(
    value: JsonObject,
    field: str,
    *,
    path: Path,
    line_number: int | None = None,
) -> int:
    raw = value.get(field)
    if isinstance(raw, int):
        return raw
    raise PromptDiaryError(_field_message(path, line_number, field, "integer"))


def _field_message(
    path: Path,
    line_number: int | None,
    field: str,
    expected_type: str,
) -> str:
    location = str(path) if line_number is None else f"{path}:{line_number}"
    return f"{location} missing {expected_type} field {field!r}"


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _project_key_mismatch_message(
    project_path: Path,
    project_key: str,
    project_dir: Path,
) -> str:
    return f"{project_path} project_key {project_key!r} must match directory {project_dir.name!r}"


def _turn_object_message(path: Path, line_number: int, position: int) -> str:
    return f"{path}:{line_number} turns[{position}] must be a JSON object"


def _session_path_message(path: Path, line_number: int) -> str:
    return f"{path}:{line_number} session_path must be a relative sessions/ path"


def _session_path_escape_message(path: Path, line_number: int, sessions_root: Path) -> str:
    return f"{path}:{line_number} session_path must resolve under {sessions_root}"


def _positive_line_span_message(path: Path, line_number: int, label: str) -> str:
    return f"{path}:{line_number} {label} start line must be positive"


def _ordered_line_span_message(path: Path, line_number: int, label: str) -> str:
    return f"{path}:{line_number} {label} end line must be >= start line"


def _duplicate_session_ref_message(path: Path, line_number: int, session_ref: str) -> str:
    return f"{path}:{line_number} duplicate session_ref {session_ref!r}"


def _malformed_turn_ref_message(
    path: Path,
    line_number: int,
    position: int,
    turn_ref: str,
) -> str:
    return f"{path}:{line_number} turns[{position}] turn_ref {turn_ref!r} must match T0001"


def _duplicate_turn_ref_message(path: Path, line_number: int, turn_ref: str) -> str:
    return f"{path}:{line_number} duplicate turn_ref {turn_ref!r}"


def _missing_json_file_message(path: Path) -> str:
    return f"required JSON file is missing: {path}"


def _invalid_json_message(path: Path, message: str) -> str:
    return f"{path} contains invalid JSON: {message}"


def _json_object_message(path: Path) -> str:
    return f"{path} must contain a JSON object"


def _invalid_jsonl_message(path: Path, line_number: int, message: str) -> str:
    return f"{path}:{line_number} contains invalid JSON: {message}"


def _jsonl_object_message(path: Path, line_number: int) -> str:
    return f"{path}:{line_number} must contain a JSON object"
