from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from prompt_diary.generate.evidence_extraction.mcp import (
    WriteEvidenceAppendedResult,
    write_evidence,
)
from prompt_diary.generate.evidence_extraction.session_reader import (
    ReadSessionLinesCompactResult,
    read_session_lines,
)
from prompt_diary.generate.pipeline import build_generation_plan
from prompt_diary.generate.project_synthesis.cards import load_committed_chains
from prompt_diary.generate.project_synthesis.inputs import build_project_synthesis_inputs
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.models import JsonObject, PrepareResult, SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target
from tests.support.evidence_extraction import build_evidence_chain

_THREAD = "01a00b3d-1747-7dd1-9663-35c298360eb3"
_BRANCH = "01a11eff-e687-7410-ba1e-a2be03a4a756"
_NEXT_BRANCH = "01a11eff-e687-7410-ba1e-a2be03a4a757"


def test_shared_thread_rollouts_keep_owned_turns_and_local_context(tmp_path: Path) -> None:
    source = tmp_path / "source"
    original = _rollout(source, _THREAD, None, ("P1", "P2", "A3"))
    replacement = _rollout(source, _THREAD, _BRANCH, ("B1", "B2"), base=(original, 5))
    originals = {path: path.read_bytes() for path in (original, replacement)}

    result = _prepare(tmp_path, source)

    workspace = load_prepared_workspace(result.workspace_path)
    assert result.session_count == 2
    project = workspace.projects[0]
    first, second = project.sessions
    assert first.source_session_id == second.source_session_id == _THREAD
    assert (first.session_ref, second.session_ref) == ("S0001", "S0002")
    assert [turn.span.start for turn in first.turns] == [2, 4, 6]
    assert [turn.span.start for turn in second.turns] == [6, 8]
    prepared = result.workspace_path / "projects" / project.project_key / second.session_path
    records = [json.loads(line) for line in prepared.read_text(encoding="utf-8").splitlines()]
    assert len([record for record in records if record["type"] == "session_meta"]) == 1
    assert _messages(prepared) == ["P1", "P2", "B1", "B2"]
    assert all(path.read_bytes() == content for path, content in originals.items())

    inputs = build_session_extraction_inputs(
        workspace_path=result.workspace_path,
        project_key=project.project_key,
        session_ref=second.session_ref,
    )
    assert len(inputs.turns) == 2
    assert json.loads(inputs.turns[0].target_turn_json)["previous_context"] == {
        "start_line": 4,
        "end_line": 5,
    }
    context = read_session_lines(
        workspace_path=result.workspace_path,
        project_key=project.project_key,
        session_ref=second.session_ref,
        start_line=4,
        end_line=5,
    )
    assert isinstance(context, ReadSessionLinesCompactResult)
    assert context.next_cursor is None
    assert context.records
    plan = build_generation_plan(result.workspace_path)
    assert len([task for task in plan.tasks if task.kind == "evidence_extraction"]) == 2
    project_task = next(task for task in plan.tasks if task.kind == "project_synthesis")
    assert len(project_task.depends_on) == 2

    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    entry = next(item for item in audit["sessions"] if item["source_path"] == str(replacement))
    assert entry["source_checksum_sha256"] == hashlib.sha256(originals[replacement]).hexdigest()
    assert entry["workspace_checksum_sha256"] == hashlib.sha256(prepared.read_bytes()).hexdigest()
    assert entry["source_checksum_sha256"] != entry["workspace_checksum_sha256"]
    for segment in entry["source_segments"]:
        native_lines = Path(segment["source_path"]).read_bytes().splitlines()
        prepared_lines = prepared.read_bytes().splitlines()
        native_start = segment["source_start_line"] - 1
        prepared_start = segment["workspace_start_line"] - 1
        count = segment["line_count"]
        assert (
            native_lines[native_start : native_start + count]
            == prepared_lines[prepared_start : prepared_start + count]
        )
    assert entry["non_monotonic_timestamp_count"] == 0


def test_unselected_ancestor_is_retained_only_as_context(tmp_path: Path) -> None:
    source = tmp_path / "source"
    original = _rollout(source, _THREAD, None, ("P1", "P2", "A3"), day=11)
    _rollout(source, _THREAD, _BRANCH, ("B1",), base=(original, 5))

    result = _prepare(tmp_path, source)

    assert result.session_count == 1
    project = load_prepared_workspace(result.workspace_path).projects[0]
    session = project.sessions[0]
    assert len(session.turns) == 1
    assert session.turns[0].span.start == 6
    assert session.turns[0].previous_context is not None
    assert session.turns[0].previous_context.start == 4
    prepared = result.workspace_path / "projects" / project.project_key / session.session_path
    assert _messages(prepared) == ["P1", "P2", "B1"]


def test_project_synthesis_receives_owned_chains_from_both_shared_thread_rollouts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    original = _rollout(source, _THREAD, None, ("P1", "P2", "A3"))
    _rollout(source, _THREAD, _BRANCH, ("B1", "B2"), base=(original, 5))
    result = _prepare(tmp_path, source)
    project = load_prepared_workspace(result.workspace_path).projects[0]

    for session in project.sessions:
        for turn in session.turns:
            chain = build_evidence_chain(
                turn_ref=turn.turn_ref, span=(turn.span.start, turn.span.end)
            )
            written = write_evidence(
                workspace_path=result.workspace_path,
                project_key=project.project_key,
                session_ref=session.session_ref,
                evidence_chain=chain,
            )
            assert isinstance(written, WriteEvidenceAppendedResult)

    chains = load_committed_chains(result.workspace_path, project.project_key)
    assert [(chain.session_ref, chain.turn_ref) for chain in chains] == [
        ("S0001", "T0001"),
        ("S0001", "T0002"),
        ("S0001", "T0003"),
        ("S0002", "T0001"),
        ("S0002", "T0002"),
    ]
    paste = build_project_synthesis_inputs(
        workspace_path=result.workspace_path, project_key=project.project_key
    ).evidence_chains
    for message in ("P1", "P2", "A3", "B1", "B2"):
        assert paste.count(f'user_messages:\n- "{message}"\n') == 1
    assert "#### Session S0001 (3 chains)" in paste
    assert "#### Session S0002 (2 chains)" in paste


def test_nested_rollout_uses_physical_base_and_excludes_both_abandoned_suffixes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    original = _rollout(source, _THREAD, None, ("P1", "P2", "A3"), day=11)
    branch = _rollout(source, _THREAD, _BRANCH, ("B1", "B2"), day=11, base=(original, 5))
    latest = _rollout(source, _THREAD, _NEXT_BRANCH, ("C1",), base=(branch, 3))

    result = _prepare(tmp_path, source)

    assert result.session_count == 1
    project = load_prepared_workspace(result.workspace_path).projects[0]
    session = project.sessions[0]
    assert session.session_path.name == latest.name
    assert session.turns[0].span.start == 8
    assert session.turns[0].previous_context is not None
    assert session.turns[0].previous_context.start == 6
    prepared = result.workspace_path / "projects" / project.project_key / session.session_path
    assert _messages(prepared) == ["P1", "P2", "B1", "C1"]


def test_invalid_history_preserves_existing_workspace_on_force(tmp_path: Path) -> None:
    source = tmp_path / "source"
    original = _rollout(source, _THREAD, None, ("P1", "P2"))
    result = _prepare(tmp_path, source)
    report = result.workspace_path / "report.md"
    report.write_text("Keep this report", encoding="utf-8")
    before = {
        path: path.read_bytes() for path in (tmp_path / "reports").rglob("*") if path.is_file()
    }
    child = _rollout(source, _THREAD, _BRANCH, ("B1",), base=(original, 3))
    records = [json.loads(line) for line in child.read_text(encoding="utf-8").splitlines()]
    records[0]["payload"]["history_base"]["end_byte_offset"] = 1
    _records(child, cast("list[JsonObject]", records))

    with pytest.raises(PromptDiaryError):
        _prepare(tmp_path, source, force=True)

    assert {path: path.read_bytes() for path in before} == before


def _rollout(
    root: Path,
    thread_id: str,
    branch_id: str | None,
    messages: tuple[str, ...],
    *,
    day: int = 12,
    base: tuple[Path, int] | None = None,
) -> Path:
    suffix = thread_id if branch_id is None else f"{thread_id}_{branch_id}"
    path = root / f"rollout-2026-05-{day:02d}T09-00-00-{suffix}.jsonl"
    payload: JsonObject = {"id": thread_id, "cwd": "/rollout-project", "history_mode": "paginated"}
    ordinal = 0
    if base is not None:
        base_path, count = base
        prefix = base_path.read_bytes().splitlines(keepends=True)[:count]
        ordinal = cast("int", json.loads(prefix[-1])["ordinal"]) + 1
        base_id = base_path.stem.rsplit("_", 1)[-1] if "_" in base_path.stem else thread_id
        payload["history_base"] = {
            "thread_id": base_id,
            "end_ordinal_exclusive": ordinal,
            "end_byte_offset": len(b"".join(prefix)),
        }
    first_hour = 10 if base is None else 11 + ordinal // 2
    records: list[JsonObject] = [
        {
            "type": "session_meta",
            "timestamp": f"2026-05-{day:02d}T{first_hour - 1:02d}:00:00Z",
            "ordinal": ordinal,
            "payload": payload,
        }
    ]
    for index, text in enumerate(messages):
        for role, message in (("user", text), ("assistant", f"Completed {text}")):
            ordinal += 1
            records.append(
                {
                    "type": "response_item",
                    "timestamp": f"2026-05-{day:02d}T{first_hour + index:02d}:00:00Z",
                    "ordinal": ordinal,
                    "payload": {
                        "type": "message",
                        "role": role,
                        "content": [{"type": "input_text", "text": message}],
                    },
                }
            )
    _records(path, records)
    return path


def _records(path: Path, records: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _messages(path: Path) -> list[str]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [
        record["payload"]["content"][0]["text"]
        for record in records
        if record["type"] == "response_item" and record["payload"]["role"] == "user"
    ]


def _prepare(tmp_path: Path, source: Path, *, force: bool = False) -> PrepareResult:
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="UTC",
        now=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    return prepare_workspace(
        target,
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source="codex", root=source),),
        force=force,
    )
