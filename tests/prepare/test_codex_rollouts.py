from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.prepare.codex_rollouts import CodexRolloutStore

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.models import JsonObject

_THREAD = "00000000-0000-0000-0000-000000000001"
_SECOND = "00000000-0000-0000-0000-000000000002"
_THIRD = "00000000-0000-0000-0000-000000000003"


def test_legacy_transcripts_keep_exact_bytes_and_do_not_open_unselected_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.jsonl"
    content = b'not-json\n{"type":"response_item"}\n'
    path.write_bytes(content)
    missing = _path(tmp_path, _SECOND)
    store = CodexRolloutStore((path, missing))

    transcript = store.prepare(path)

    assert transcript.content == content
    assert transcript.source_checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert transcript.rollout_id is None
    assert transcript.owned_start_line == 1
    assert transcript.segments == ()
    assert store.prepare(path) == transcript


def test_standalone_native_rollout_preserves_bytes_and_physical_identity(tmp_path: Path) -> None:
    path = _write(tmp_path, _THREAD, ("P1", "P2"))

    transcript = CodexRolloutStore((path,)).prepare(path)

    assert transcript.content == path.read_bytes()
    assert transcript.rollout_id == _THREAD
    assert transcript.owned_start_line == 1
    assert transcript.segments == ()


def test_replacement_materializes_only_inherited_prefix_and_maps_source_lines(
    tmp_path: Path,
) -> None:
    parent = _write(tmp_path, _THREAD, ("P1", "P2", "A3"))
    child = _write(tmp_path, _SECOND, ("B1", "B2"), base=_base(parent, _THREAD, 3))
    original = {path: path.read_bytes() for path in (parent, child)}

    transcript = CodexRolloutStore((parent, child)).prepare(child)

    records = _records(transcript.content)
    assert records[0] == _records(original[child])[0]
    assert [record["payload"] for record in records[1:]] == ["P1", "P2", "B1", "B2"]
    assert transcript.rollout_id == _SECOND
    assert transcript.owned_start_line == 4
    assert transcript.source_checksum_sha256 == hashlib.sha256(original[child]).hexdigest()
    assert [
        (
            segment.source_path,
            segment.source_start_line,
            segment.prepared_start_line,
            segment.line_count,
        )
        for segment in transcript.segments
    ] == [(child, 1, 1, 1), (parent, 2, 2, 2), (child, 2, 4, 2)]
    for segment in transcript.segments:
        assert (
            segment.source_checksum_sha256
            == hashlib.sha256(original[segment.source_path]).hexdigest()
        )
    assert {path: path.read_bytes() for path in original} == original


def test_nested_base_cutoff_is_in_the_original_delta_not_materialized_lines(tmp_path: Path) -> None:
    first = _write(tmp_path, _THREAD, ("P1", "P2", "A3"))
    middle = _write(tmp_path, _SECOND, ("M1", "M2"), base=_base(first, _THREAD, 3))
    last = _write(tmp_path, _THIRD, ("L1",), base=_base(middle, _SECOND, 2))

    transcript = CodexRolloutStore((first, middle, last)).prepare(last)

    records = _records(transcript.content)
    assert [record["type"] for record in records].count("session_meta") == 1
    assert [record["payload"] for record in records[1:]] == ["P1", "P2", "M1", "L1"]
    assert transcript.owned_start_line == 5
    assert [segment.source_path for segment in transcript.segments] == [last, first, middle, last]


def test_explicit_base_may_belong_to_another_logical_thread(tmp_path: Path) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    child = _write(tmp_path, _SECOND, ("C1",), thread_id=_SECOND, base=_base(parent, _THREAD, 2))

    transcript = CodexRolloutStore((parent, child)).prepare(child)

    assert [record["payload"] for record in _records(transcript.content)[1:]] == ["P1", "C1"]


def test_malformed_source_tail_outside_the_frozen_prefix_does_not_change_inheritance(
    tmp_path: Path,
) -> None:
    parent = _write(tmp_path, _THREAD, ("P1", "discarded"))
    base = _base(parent, _THREAD, 2)
    parent.write_bytes(b"".join(parent.read_bytes().splitlines(keepends=True)[:2]) + b"broken\n")
    child = _write(tmp_path, _SECOND, ("C1",), base=base)
    child.write_bytes(child.read_bytes() + b"malformed-local\n")

    transcript = CodexRolloutStore((parent, child)).prepare(child)

    assert b"discarded" not in transcript.content
    assert b"broken" not in transcript.content
    assert transcript.content.endswith(b"malformed-local\n")


def test_base_can_inherit_only_metadata_and_child_can_have_no_body(tmp_path: Path) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    child = _write(tmp_path, _SECOND, (), base=_base(parent, _THREAD, 1))

    transcript = CodexRolloutStore((parent, child)).prepare(child)

    assert transcript.content == child.read_bytes()
    assert transcript.owned_start_line == 2
    assert len(transcript.segments) == 1


def test_contiguous_original_ranges_are_compacted(tmp_path: Path) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    child = _write(tmp_path, _SECOND, ("C1",), base=_base(parent, _THREAD, 1))

    transcript = CodexRolloutStore((parent, child)).prepare(child)

    assert len(transcript.segments) == 1
    assert transcript.segments[0].line_count == 2


def test_identical_physical_base_copies_use_the_first_lexical_path(tmp_path: Path) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    duplicate = tmp_path / "backup" / parent.name
    duplicate.parent.mkdir()
    duplicate.write_bytes(parent.read_bytes())
    child = _write(tmp_path, _SECOND, ("C1",), base=_base(parent, _THREAD, 2))

    transcript = CodexRolloutStore((parent, duplicate, child)).prepare(child)

    assert [record["payload"] for record in _records(transcript.content)[1:]] == ["P1", "C1"]
    assert transcript.segments[1].source_path == duplicate


@pytest.mark.parametrize("count", [0, 2])
def test_missing_and_ambiguous_physical_bases_fail(tmp_path: Path, count: int) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    child = _write(tmp_path, _SECOND, ("C1",), base=_base(parent, _THREAD, 2))
    paths = [child]
    if count:
        duplicate = tmp_path / "backup" / parent.name
        duplicate.parent.mkdir()
        duplicate.write_bytes(parent.read_bytes() + b"different tail\n")
        paths.extend((parent, duplicate))
    message = "missing source rollout" if count == 0 else "ambiguous source rollout"

    with pytest.raises(PromptDiaryError, match=message):
        CodexRolloutStore(tuple(paths)).prepare(child)


def test_base_cycles_fail_without_recursing_forever(tmp_path: Path) -> None:
    first = _write(tmp_path, _THREAD, ("A",))
    second = _write(tmp_path, _SECOND, ("B",), base=_base(first, _THREAD, 2))
    first = _write(tmp_path, _THREAD, ("A",), base=_base(second, _SECOND, 2))
    first_base = _base(first, _THREAD, 2)
    _write(tmp_path, _SECOND, ("B",), base=first_base)

    with pytest.raises(PromptDiaryError, match="cycle in history_base"):
        CodexRolloutStore((first, second)).prepare(second)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thread_id", "invalid-id"),
        ("end_ordinal_exclusive", -1),
        ("end_ordinal_exclusive", True),
        ("end_byte_offset", "10"),
    ],
)
def test_invalid_base_fields_fail(tmp_path: Path, field: str, value: object) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    base = _base(parent, _THREAD, 2)
    base[field] = value
    child = _write(tmp_path, _SECOND, ("C1",), base=base)

    with pytest.raises(PromptDiaryError, match="malformed history_base"):
        CodexRolloutStore((parent, child)).prepare(child)


def test_nonobject_base_fails(tmp_path: Path) -> None:
    child = _write(tmp_path, _SECOND, (), base="invalid")

    with pytest.raises(PromptDiaryError, match="malformed history_base"):
        CodexRolloutStore((child,)).prepare(child)


@pytest.mark.parametrize("difference", ["past-end", "mid-record", "ordinal", "empty"])
def test_cutoff_must_agree_in_bytes_and_ordinals(tmp_path: Path, difference: str) -> None:
    parent = _write(tmp_path, _THREAD, ("P1", "P2"))
    base = _base(parent, _THREAD, 2)
    if difference == "past-end":
        base["end_byte_offset"] = len(parent.read_bytes()) + 1
    elif difference == "mid-record":
        base["end_byte_offset"] = int(str(base["end_byte_offset"])) - 1
    elif difference == "ordinal":
        base["end_ordinal_exclusive"] = 3
    else:
        base["end_byte_offset"] = 0
    child = _write(tmp_path, _SECOND, ("C1",), base=base)

    with pytest.raises(PromptDiaryError, match=r"history_base (byte offset|ordinal)"):
        CodexRolloutStore((parent, child)).prepare(child)


@pytest.mark.parametrize("difference", ["json", "ordinal", "metadata"])
def test_inherited_prefix_records_must_have_continuous_native_ordinals(
    tmp_path: Path, difference: str
) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    lines = parent.read_bytes().splitlines(keepends=True)
    if difference == "json":
        lines[1] = b"broken\n"
    else:
        records = _records(parent.read_bytes())
        if difference == "ordinal":
            records[1]["ordinal"] = 9
        else:
            records[1]["type"] = "session_meta"
        lines[1] = _line(records[1])
    parent.write_bytes(b"".join(lines))
    base: dict[str, object] = {
        "thread_id": _THREAD,
        "end_ordinal_exclusive": 2,
        "end_byte_offset": len(parent.read_bytes()),
    }
    child = _write(tmp_path, _SECOND, ("C1",), base=base)

    with pytest.raises(PromptDiaryError, match="malformed or discontinuous ordinals"):
        CodexRolloutStore((parent, child)).prepare(child)


@pytest.mark.parametrize("difference", ["legacy", "missing-header", "header-ordinal", "filename"])
def test_native_history_requires_valid_source_metadata(tmp_path: Path, difference: str) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    base = _base(parent, _THREAD, 2)
    records = _records(parent.read_bytes())
    if difference == "legacy":
        payload = records[0]["payload"]
        assert isinstance(payload, dict)
        payload["history_mode"] = "legacy"
    elif difference == "missing-header":
        records[0]["payload"] = None
    elif difference == "header-ordinal":
        records[0]["ordinal"] = 5
    else:
        payload = records[0]["payload"]
        assert isinstance(payload, dict)
        payload["id"] = _THIRD
    parent.write_bytes(b"".join(_line(record) for record in records))
    child = _write(tmp_path, _SECOND, ("C1",), base=base)

    with pytest.raises(PromptDiaryError, match=r"metadata|ordinal|thread ID"):
        CodexRolloutStore((parent, child)).prepare(child)


def test_noncanonical_delta_cannot_claim_a_physical_history(tmp_path: Path) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    child = _write(tmp_path, _SECOND, ("C1",), base=_base(parent, _THREAD, 2))
    renamed = child.with_name("child.jsonl")
    child.rename(renamed)

    with pytest.raises(PromptDiaryError, match="canonical rollout filename"):
        CodexRolloutStore((parent, renamed)).prepare(renamed)


def test_invalid_filename_timestamp_is_treated_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / f"rollout-2026-99-01T12-00-00-{_THREAD}.jsonl"
    path.write_bytes(b"[]\n")

    assert CodexRolloutStore((path,)).prepare(path).rollout_id is None


def test_source_read_errors_are_actionable(tmp_path: Path) -> None:
    path = _path(tmp_path, _THREAD)

    with pytest.raises(PromptDiaryError, match="cannot read source rollout"):
        CodexRolloutStore((path,)).prepare(path)


def test_reconstructed_metadata_must_be_a_complete_jsonl_record(tmp_path: Path) -> None:
    parent = _write(tmp_path, _THREAD, ("P1",))
    child = _write(tmp_path, _SECOND, (), base=_base(parent, _THREAD, 2))
    child.write_bytes(child.read_bytes().rstrip(b"\n"))

    with pytest.raises(PromptDiaryError, match="metadata is not a complete JSONL record"):
        CodexRolloutStore((parent, child)).prepare(child)


def _path(directory: Path, rollout_id: str, *, thread_id: str = _THREAD) -> Path:
    ids = thread_id if rollout_id == thread_id else f"{thread_id}_{rollout_id}"
    return directory / f"rollout-2026-10-09T12-00-00-{ids}.jsonl"


def _write(
    directory: Path,
    rollout_id: str,
    body: tuple[str, ...],
    *,
    thread_id: str = _THREAD,
    base: object = None,
) -> Path:
    raw_start: object = (
        cast("dict[str, object]", base)["end_ordinal_exclusive"] if isinstance(base, dict) else 0
    )
    start = raw_start if isinstance(raw_start, int) else 0
    path = _path(directory, rollout_id, thread_id=thread_id)
    payload: dict[str, object] = {"id": thread_id, "history_mode": "paginated"}
    if base is not None:
        payload["history_base"] = base
    records: list[dict[str, object]] = [
        {"type": "session_meta", "ordinal": start, "payload": payload}
    ]
    records.extend(
        {"type": "event_msg", "ordinal": start + index, "payload": text}
        for index, text in enumerate(body, start=1)
    )
    path.write_bytes(b"".join(_line(record) for record in records))
    return path


def _base(path: Path, rollout_id: str, record_count: int) -> dict[str, object]:
    prefix = path.read_bytes().splitlines(keepends=True)[:record_count]
    ordinal = int(str(_records(prefix[-1])[0]["ordinal"])) + 1
    return {
        "thread_id": rollout_id,
        "end_ordinal_exclusive": ordinal,
        "end_byte_offset": sum(len(line) for line in prefix),
    }


def _line(record: object) -> bytes:
    return (json.dumps(record, ensure_ascii=False) + "\n").encode()


def _records(content: bytes) -> list[JsonObject]:
    return [json.loads(line) for line in content.splitlines()]
