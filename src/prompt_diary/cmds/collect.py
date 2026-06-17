"""Collect prepared workspace support bundles."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Literal

import typer

from prompt_diary import __version__
from prompt_diary.cmds.common import (
    CliWorkspaceTargetOptions,
    QuietOption,
    ResolvedCliDirectWorkspaceTarget,
    echo_messages,
    exit_with_error,
    workspace_target_command,
)
from prompt_diary.cmds.generate import workspace_for_existing_target
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.workspace import PreparedWorkspace, load_prepared_workspace
from prompt_diary.prepare.workspace import audit_path_for_target

if TYPE_CHECKING:
    from prompt_diary.models import JsonObject, JsonValue

OutputOption = Annotated[
    Path | None,
    typer.Option("--output", help="Path for the support bundle zip."),
]
IncludeRawSessionsOption = Annotated[
    bool,
    typer.Option(
        "--include-raw-sessions",
        help="Include copied raw assistant transcript files from projects/*/sessions/**.",
    ),
]


@dataclass(frozen=True)
class _CollectTarget:
    mode: Literal["date-root", "workspace"]
    workspace_path: Path
    workspace_archive_root: PurePosixPath
    prepared_workspace: PreparedWorkspace
    default_output_path: Path
    reports_root: Path | None = None
    audit_manifest: _ArchiveMember | None = None


@dataclass(frozen=True)
class _ArchiveMember:
    source_path: Path
    archive_path: PurePosixPath


@dataclass(frozen=True)
class _ArchivePlan:
    included: tuple[_ArchiveMember, ...]
    excluded_paths: tuple[PurePosixPath, ...]


def register(app: typer.Typer) -> None:
    """Register collect commands."""
    workspace_target_command(app, collect, include_workspace=True)


def collect(
    *,
    target_options: CliWorkspaceTargetOptions,
    output: OutputOption = None,
    include_raw_sessions: IncludeRawSessionsOption = False,
    quiet: QuietOption = False,
) -> None:
    """Package an existing prepared workspace for support/debug upload."""
    del quiet
    try:
        target = _resolve_collect_target(target_options)
        output_path = output if output is not None else target.default_output_path
        _reject_workspace_output(target.workspace_path, output_path)
        archive_path = write_collection_bundle(
            target=target,
            output_path=output_path,
            include_raw_sessions=include_raw_sessions,
        )
    except PromptDiaryError as exc:
        exit_with_error(exc)
    if include_raw_sessions:
        typer.echo(
            "Warning: collection bundle contains raw assistant transcript content.",
            err=True,
        )
    echo_messages((f"Wrote collection bundle {archive_path}",))


def write_collection_bundle(
    *,
    target: _CollectTarget,
    output_path: Path,
    include_raw_sessions: bool,
) -> Path:
    """Write a support bundle zip for an already-resolved collect target."""
    plan = _build_archive_plan(target, include_raw_sessions=include_raw_sessions)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _collection_manifest(
        target=target,
        plan=plan,
        include_raw_sessions=include_raw_sessions,
    )
    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in plan.included:
            archive.write(member.source_path, member.archive_path.as_posix())
        archive.writestr("collection.manifest.json", _json_bytes(manifest))
    return output_path


def _resolve_collect_target(target_options: CliWorkspaceTargetOptions) -> _CollectTarget:
    resolved = target_options.resolve_generation_target()
    workspace_path = workspace_for_existing_target(target_options=target_options)
    prepared_workspace = load_prepared_workspace(workspace_path)
    if isinstance(resolved, ResolvedCliDirectWorkspaceTarget):
        archive_root = PurePosixPath(workspace_path.name)
        return _CollectTarget(
            mode="workspace",
            workspace_path=workspace_path,
            workspace_archive_root=archive_root,
            prepared_workspace=prepared_workspace,
            default_output_path=(
                workspace_path.parent / "collections" / f"{workspace_path.name}.zip"
            ),
            audit_manifest=_direct_audit_manifest(workspace_path, prepared_workspace),
        )

    audit_path = audit_path_for_target(resolved.target, reports_root=resolved.reports_root)
    archive_date = resolved.target.workspace_name
    return _CollectTarget(
        mode="date-root",
        workspace_path=workspace_path,
        workspace_archive_root=PurePosixPath("work") / archive_date,
        prepared_workspace=prepared_workspace,
        default_output_path=resolved.reports_root / "collections" / f"{archive_date}.zip",
        reports_root=resolved.reports_root,
        audit_manifest=_existing_archive_member(
            source_path=audit_path,
            archive_path=PurePosixPath("private") / archive_date / "audit.manifest.json",
        ),
    )


def _direct_audit_manifest(
    workspace_path: Path, prepared_workspace: PreparedWorkspace
) -> _ArchiveMember | None:
    if workspace_path.parent.name != "work":
        return None
    reports_root = workspace_path.parent.parent
    audit_path = reports_root / "private" / prepared_workspace.report_date / "audit.manifest.json"
    return _existing_archive_member(
        source_path=audit_path,
        archive_path=PurePosixPath("private")
        / prepared_workspace.report_date
        / "audit.manifest.json",
    )


def _existing_archive_member(
    *, source_path: Path, archive_path: PurePosixPath
) -> _ArchiveMember | None:
    if not source_path.exists():
        return None
    return _ArchiveMember(source_path=source_path, archive_path=archive_path)


def _reject_workspace_output(workspace_path: Path, output_path: Path) -> None:
    resolved_workspace = workspace_path.resolve()
    resolved_output = output_path.resolve()
    if _path_is_relative_to(resolved_output, resolved_workspace):
        raise PromptDiaryError(_workspace_output_message(workspace_path))


def _workspace_output_message(workspace_path: Path) -> str:
    return f"--output must be outside the prepared workspace being collected: {workspace_path}"


def _build_archive_plan(target: _CollectTarget, *, include_raw_sessions: bool) -> _ArchivePlan:
    included: list[_ArchiveMember] = []
    excluded: list[PurePosixPath] = []
    for source_path in _workspace_files(target.workspace_path):
        relative_path = source_path.relative_to(target.workspace_path)
        archive_path = target.workspace_archive_root / PurePosixPath(relative_path.as_posix())
        if _is_raw_session_path(relative_path) and not include_raw_sessions:
            excluded.append(archive_path)
            continue
        included.append(_ArchiveMember(source_path=source_path, archive_path=archive_path))
    if target.audit_manifest is not None:
        included.append(target.audit_manifest)
    return _ArchivePlan(
        included=tuple(sorted(included, key=lambda member: member.archive_path.as_posix())),
        excluded_paths=tuple(sorted(excluded, key=lambda path: path.as_posix())),
    )


def _workspace_files(workspace_path: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (path for path in workspace_path.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(workspace_path).as_posix(),
        )
    )


def _is_raw_session_path(relative_path: Path) -> bool:
    parts = relative_path.parts
    return len(parts) >= 4 and parts[0] == "projects" and parts[2] == "sessions"


def _collection_manifest(
    *,
    target: _CollectTarget,
    plan: _ArchivePlan,
    include_raw_sessions: bool,
) -> JsonObject:
    return {
        "schema_version": 1,
        "collected_at": _utc_now_text(),
        "tool": {
            "name": "prompt-diary",
            "version": __version__,
        },
        "target": _manifest_target(target),
        "include_raw_sessions": include_raw_sessions,
        "included_paths": [member.archive_path.as_posix() for member in plan.included],
        "excluded_paths": [path.as_posix() for path in plan.excluded_paths],
        "generated_paths": ["collection.manifest.json"],
        "file_checksums_sha256": _checksums(plan.included),
    }


def _manifest_target(target: _CollectTarget) -> JsonObject:
    value: JsonObject = {
        "mode": target.mode,
        "workspace_path": str(target.workspace_path),
        "workspace_archive_root": target.workspace_archive_root.as_posix(),
        "report_date": target.prepared_workspace.report_date,
        "timezone": target.prepared_workspace.timezone,
        "status": target.prepared_workspace.status,
    }
    if target.reports_root is not None:
        value["reports_root"] = str(target.reports_root)
    if target.audit_manifest is not None:
        value["audit_manifest_path"] = str(target.audit_manifest.source_path)
    return value


def _checksums(members: tuple[_ArchiveMember, ...]) -> JsonObject:
    return {member.archive_path.as_posix(): _sha256_file(member.source_path) for member in members}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(value: JsonValue) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8")


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
