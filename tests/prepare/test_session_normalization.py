from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.models import JsonObject, PrepareResult, SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from pathlib import Path


def test_identical_session_copies_choose_lexical_absolute_path(tmp_path: Path) -> None:
    first = _write_codex(tmp_path / "a", "same", _turn("work"), filename="original.jsonl")
    second = _write_codex(tmp_path / "z", "same", _turn("work"), filename="backup.jsonl")

    result = _prepare(tmp_path, source_specs=(_spec(second), _spec(first)))

    assert result.session_count == 1
    row = _rows(result)["same"]
    assert row["session_path"] == "sessions/codex/original.jsonl"
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["sessions"][0]["source_path"] == str(first)
    copied = next(result.workspace_path.glob("projects/*/sessions/codex/*.jsonl"))
    assert copied.read_bytes() == first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("conflict", ["content", "project"])
def test_conflicting_session_identity_fails_before_workspace_changes(
    tmp_path: Path, *, existing: bool, conflict: str
) -> None:
    first = _write_codex(tmp_path / "a", "same", _turn("work"))
    second = _write_codex(
        tmp_path / "z", "same", _turn("different" if conflict == "content" else "work")
    )
    specs = (_spec(first), _spec(second))
    if conflict == "project":
        records: list[JsonObject | str] = [
            {"type": "session_meta", "payload": {"id": "same"}},
            *_turn("work"),
        ]
        for path in (first, second):
            _write_records(path, records)
        specs = (
            SourceSpec(source="codex", root=first, fallback_project_root=tmp_path / "one"),
            SourceSpec(source="codex", root=second, fallback_project_root=tmp_path / "two"),
        )
    reports_root = tmp_path / "reports"
    before: dict[str, bytes] = {}
    if existing:
        prepared = _prepare(tmp_path, source_specs=(_spec(first),))
        (prepared.workspace_path / "report.md").write_text(
            "Keep my existing report", encoding="utf-8"
        )
        before = _file_bytes(reports_root)

    with pytest.raises(PromptDiaryError, match="Conflicting source session codex/same"):
        _prepare(tmp_path, source_specs=specs, force=existing)

    assert _file_bytes(reports_root) == before
    if not existing:
        assert not reports_root.exists()


def test_equal_turns_with_different_unlinked_ids_remain_separate(tmp_path: Path) -> None:
    _write_codex(tmp_path, "one", _turn("work"))
    _write_codex(tmp_path, "two", _turn("work"))

    assert set(_rows(_prepare(tmp_path))) == {"one", "two"}


def test_same_id_in_different_sources_remains_separate(tmp_path: Path) -> None:
    codex = _write_codex(tmp_path / "codex", "same", _turn("work"))
    claude = tmp_path / "claude" / "same.jsonl"
    _write_records(
        claude,
        [
            {
                "type": "user",
                "timestamp": "2026-05-12T09:00:00Z",
                "message": {"role": "user", "content": "work"},
            }
        ],
    )

    result = _prepare(
        tmp_path,
        source_specs=(_spec(codex), SourceSpec(source="claude-code", root=claude)),
    )

    assert result.session_count == 2


def test_fully_inherited_fork_is_omitted_with_parent_selected_later(tmp_path: Path) -> None:
    _write_codex(tmp_path, "a-child", _turn("work"), parent="z-parent")
    _write_codex(tmp_path, "z-parent", _turn("work"))

    assert set(_rows(_prepare(tmp_path))) == {"z-parent"}


def test_only_leading_inherited_turns_are_removed_and_source_lines_stay_intact(
    tmp_path: Path,
) -> None:
    inherited = _turn("work")
    _write_codex(tmp_path, "parent", inherited)
    child = _write_codex(
        tmp_path,
        "child",
        [*inherited, *_turn("review", hour=10), *inherited],
        parent="parent",
    )

    result = _prepare(tmp_path)

    assert _turns(_rows(result)["child"]) == [
        {"turn_ref": "T0001", "turn_start_line": 4, "turn_end_line": 5},
        {"turn_ref": "T0002", "turn_start_line": 6, "turn_end_line": 7},
    ]
    copied = next(result.workspace_path.glob("projects/*/sessions/codex/child.jsonl"))
    assert copied.read_bytes() == child.read_bytes()
    assert _rows(result)["child"]["target_start_line"] == 4


@pytest.mark.parametrize("difference", ["reaction", "timestamp", "extra", "truncated"])
def test_fork_requires_exact_complete_turn_records(tmp_path: Path, difference: str) -> None:
    inherited = _turn("work")
    changed = _turn("work")
    if difference == "reaction":
        changed[1] = _message("a different outcome", role="assistant")
    elif difference == "timestamp":
        changed[1]["timestamp"] = "2026-05-12T09:00:01Z"
    elif difference == "extra":
        changed[1]["new_evidence"] = "Retain this field"
    else:
        changed.pop()
    _write_codex(tmp_path, "parent", inherited)
    _write_codex(tmp_path, "child", changed, parent="parent")

    assert set(_rows(_prepare(tmp_path))) == {"parent", "child"}


def test_fork_fingerprints_ignore_json_key_order_and_spacing(tmp_path: Path) -> None:
    parent = _write_codex(tmp_path, "parent", _turn("work"))
    _write_codex(tmp_path, "child", _turn("work"), parent="parent")
    records = [json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines()]
    parent.write_text(
        "\n".join(json.dumps(record, sort_keys=True, separators=(",", ":")) for record in records),
        encoding="utf-8",
    )

    assert set(_rows(_prepare(tmp_path))) == {"parent"}


@pytest.mark.parametrize("lineage", ["missing", "self", "cycle", "cycle-descendant"])
def test_unproven_or_cyclic_ancestry_preserves_forks(tmp_path: Path, lineage: str) -> None:
    parent_id = "missing" if lineage == "missing" else "child"
    if lineage in {"cycle", "cycle-descendant"}:
        parent_id = "parent"
        _write_codex(tmp_path, "parent", _turn("work"), parent="child")
    _write_codex(tmp_path, "child", _turn("work"), parent=parent_id)
    if lineage == "cycle-descendant":
        _write_codex(tmp_path, "descendant", _turn("work"), parent="child")

    result = _prepare(tmp_path)

    assert result.session_count == len(list(tmp_path.glob("*.jsonl")))


def test_known_parent_proves_inheritance_when_grandparent_is_not_selected(tmp_path: Path) -> None:
    _write_codex(tmp_path, "parent", _turn("work"), parent="missing")
    _write_codex(tmp_path, "child", _turn("work"), parent="parent")

    assert set(_rows(_prepare(tmp_path))) == {"parent"}


def test_transitive_ancestry_uses_original_parent_and_ancestor_turns(tmp_path: Path) -> None:
    first, second = _turn("work"), _turn("review", hour=10)
    _write_codex(tmp_path, "ancestor", first)
    _write_codex(tmp_path, "parent", [*first, *second], parent="ancestor")
    _write_codex(tmp_path, "empty-parent", first, parent="ancestor")
    _write_codex(tmp_path, "child", [*first, *second, *_turn("new", hour=11)], parent="parent")
    _write_codex(tmp_path, "other-child", [*first, *_turn("new", hour=11)], parent="empty-parent")

    rows = _rows(_prepare(tmp_path))

    assert set(rows) == {"ancestor", "parent", "child", "other-child"}
    assert [turn["turn_start_line"] for turn in _turns(rows["parent"])] == [4]
    assert [turn["turn_start_line"] for turn in _turns(rows["child"])] == [6]
    assert [turn["turn_start_line"] for turn in _turns(rows["other-child"])] == [4]


@pytest.mark.parametrize("copied_previous_day", [False, True])
def test_inherited_prefix_proof_includes_off_day_human_turns(
    tmp_path: Path, *, copied_previous_day: bool
) -> None:
    today = _turn("today")
    yesterday = _turn("yesterday", day=11)
    _write_codex(tmp_path, "parent", [*yesterday, *today])
    previous_day = yesterday if copied_previous_day else _turn("different yesterday", day=11)
    _write_codex(tmp_path, "child", [*previous_day, *today], parent="parent")

    rows = _rows(_prepare(tmp_path))

    assert ("child" not in rows) is copied_previous_day
    assert _turns(rows["parent"]) == [
        {"turn_ref": "T0001", "turn_start_line": 4, "turn_end_line": 5}
    ]


@pytest.mark.parametrize("ambiguous", ["malformed", "untimed-human", "metadata", "parent", "no-id"])
@pytest.mark.parametrize("location", ["parent", "child"])
def test_ambiguous_fork_evidence_is_preserved(
    tmp_path: Path, ambiguous: str, location: str
) -> None:
    _write_codex(tmp_path, "parent", _turn("work"))
    _write_codex(tmp_path, "child", _turn("work"), parent="parent")
    path = tmp_path / f"{location}.jsonl"
    records = cast(
        "list[JsonObject | str]",
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()],
    )
    metadata = cast("JsonObject", cast("JsonObject", records[0])["payload"])
    if ambiguous == "malformed":
        records.insert(1, "invalid JSON")
    elif ambiguous == "untimed-human":
        untimed = _message("ambiguous human")
        del untimed["timestamp"]
        records.insert(1, untimed)
    elif ambiguous == "metadata":
        records.insert(1, records[0])
    elif ambiguous == "parent":
        metadata["forked_from_id"] = 42
    else:
        del metadata["id"]
    _write_records(path, records)

    assert set(_rows(_prepare(tmp_path))) == {"parent", "child"}


def test_fork_keeps_new_project_work_without_recounting_inherited_history(tmp_path: Path) -> None:
    inherited = _turn("work")
    _write_codex(tmp_path, "parent", inherited)
    child = tmp_path / "child.jsonl"
    _write_records(
        child,
        [
            {
                "type": "session_meta",
                "payload": {"id": "child", "cwd": "/new-project", "forked_from_id": "parent"},
            },
            *inherited,
            *_turn("new project work", hour=10),
        ],
    )

    result = _prepare(tmp_path)

    assert result.project_count == 2
    assert _turns(_rows(result)["child"]) == [
        {"turn_ref": "T0001", "turn_start_line": 4, "turn_end_line": 5}
    ]


def _message(text: str, *, role: str = "user", day: int = 12, hour: int = 9) -> JsonObject:
    return {
        "type": "response_item",
        "timestamp": f"2026-05-{day:02d}T{hour:02d}:00:00Z",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        },
    }


def _turn(text: str, *, day: int = 12, hour: int = 9) -> list[JsonObject]:
    return [
        _message(text, day=day, hour=hour),
        _message(f"Completed {text}", role="assistant", day=day, hour=hour),
    ]


def _write_codex(
    root: Path,
    session_id: str,
    turns: list[JsonObject],
    *,
    parent: str | None = None,
    filename: str | None = None,
) -> Path:
    metadata: JsonObject = {"id": session_id, "cwd": "/normalization-project"}
    if parent is not None:
        metadata["forked_from_id"] = parent
    path = root / (filename or f"{session_id}.jsonl")
    _write_records(path, [{"type": "session_meta", "payload": metadata}, *turns])
    return path


def _write_records(path: Path, records: list[JsonObject | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            (record if isinstance(record, str) else json.dumps(record)) + "\n" for record in records
        ),
        encoding="utf-8",
    )


def _prepare(
    tmp_path: Path, *, source_specs: tuple[SourceSpec, ...] | None = None, force: bool = False
) -> PrepareResult:
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="UTC",
        now=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    return prepare_workspace(
        target,
        reports_root=tmp_path / "reports",
        source_specs=source_specs or (_spec(tmp_path),),
        force=force,
    )


def _spec(path: Path) -> SourceSpec:
    return SourceSpec(source="codex", root=path)


def _rows(result: PrepareResult) -> dict[str, JsonObject]:
    rows: dict[str, JsonObject] = {}
    for index in result.workspace_path.glob("projects/*/sessions.index.jsonl"):
        for line in index.read_text(encoding="utf-8").splitlines():
            row = cast("JsonObject", json.loads(line))
            rows[cast("str", row["source_session_id"])] = row
    return rows


def _turns(row: JsonObject) -> list[JsonObject]:
    return cast("list[JsonObject]", row["turns"])


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
