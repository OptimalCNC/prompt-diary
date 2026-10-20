from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from prompt_diary.generate.evidence_extraction.mcp import (
    WriteEvidenceAppendedResult,
    write_evidence,
)
from prompt_diary.generate.evidence_extraction.session_reader import (
    ReadSessionLinesCompactResult,
    read_session_lines,
    serialize_read_result,
)
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.models import JsonObject, JsonValue, SourceName, SourceSpec
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target
from tests.support.evidence_extraction import build_evidence_chain

if TYPE_CHECKING:
    from typing import IO


def _prepare(tmp_path: Path, source: SourceName, content: list[JsonValue]) -> tuple[Path, Path]:
    sources = tmp_path / "sources"
    sources.mkdir()
    project = tmp_path / "synthetic-project"
    project.mkdir()
    timestamp = "2026-05-12T09:00:00Z"
    if source == "codex":
        records: list[JsonObject] = [
            {
                "timestamp": timestamp,
                "type": "session_meta",
                "payload": {"id": "synthetic-opaque-input", "cwd": str(project)},
            },
            {
                "timestamp": timestamp,
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "UserMessage", "content": content},
                },
            },
            {
                "timestamp": timestamp,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Explained the request."}],
                },
            },
        ]
    else:
        records = [
            {
                "timestamp": timestamp,
                "type": "system",
                "sessionId": "synthetic-opaque-input",
                "cwd": str(project),
            },
            {
                "timestamp": timestamp,
                "type": "user",
                "message": {"role": "user", "content": content},
            },
            {
                "timestamp": timestamp,
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Explained the request."}],
                },
            },
        ]
    source_path = sources / "synthetic.jsonl"
    source_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    now = datetime(2026, 5, 13, tzinfo=timezone.utc)
    target = resolve_report_target(date="2026-05-12", today=False, timezone_name="UTC", now=now)
    prepared = prepare_workspace(
        target,
        reports_root=tmp_path / "reports",
        source_specs=(SourceSpec(source=source, root=sources, fallback_project_root=project),),
        prepared_at=now,
    )
    return prepared.workspace_path, source_path


def _assert_chain(
    workspace_path: Path, source_path: Path, messages: tuple[str, ...], hidden: tuple[str, ...]
) -> None:
    workspace = load_prepared_workspace(workspace_path)
    assert len(workspace.projects) == 1
    project = workspace.projects[0]
    assert len(project.sessions) == 1
    session = project.sessions[0]
    assert len(session.turns) == 1
    turn = session.turns[0]
    assert (turn.span.start, turn.span.end) == (2, 3)
    copied_path = workspace_path / "projects" / project.project_key / session.session_path
    assert copied_path.read_bytes() == source_path.read_bytes()
    compact = read_session_lines(
        workspace_path=workspace_path,
        project_key=project.project_key,
        session_ref=session.session_ref,
        start_line=turn.span.start,
        end_line=turn.span.end,
        mode="compact",
    )
    assert isinstance(compact, ReadSessionLinesCompactResult)
    compact_json = serialize_read_result(compact)
    for value in hidden:
        assert value not in compact_json
    result = write_evidence(
        workspace_path=workspace_path,
        project_key=project.project_key,
        session_ref=session.session_ref,
        evidence_chain=build_evidence_chain(
            turn_ref=turn.turn_ref, span=(turn.span.start, turn.span.end), kind="no_material"
        ),
    )
    assert isinstance(result, WriteEvidenceAppendedResult)
    card_path = (
        workspace_path
        / "projects"
        / project.project_key
        / "evidence"
        / f"{session.session_ref}.json"
    )
    card = json.loads(card_path.read_text(encoding="utf-8"))
    assert card["evidence_chains"][0]["trigger"] == {
        "citations": [{"lines": "2-2"}],
        "quoted_messages": [{"text": text} for text in messages],
    }
    for value in hidden:
        assert value not in json.dumps(card)


@pytest.mark.parametrize("source", ["codex", "claude-code"])
@pytest.mark.parametrize(
    ("attachment_type", "image_exists"),
    [
        ("local_image", False),
        ("local_image", True),
        ("future_attachment", False),
        ("input_image", False),
        (None, False),
    ],
)
def test_opaque_content_preserves_exact_text_through_preparation_reader_and_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: SourceName,
    attachment_type: str | None,
    *,
    image_exists: bool,
) -> None:
    attachment_path = tmp_path / "private-synthetic-attachment.png"
    if image_exists:
        attachment_path.write_bytes(b"opaque placeholder, deliberately not a valid image")
    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        assert path != attachment_path, "preparation and evidence must not open attachments"
        return cast("IO[Any]", original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", guarded_open)
    messages = ("  请检查这张截图。\nKeep exact whitespace.  ", "\nSecond instruction.\t")
    content: list[JsonValue] = [{"type": "text", "text": messages[0]}]
    if attachment_type is not None:
        content.append(
            {"type": attachment_type, "path": str(attachment_path), "data": "private-opaque-data"}
        )
    content.append({"type": "text", "text": messages[1]})

    workspace_path, source_path = _prepare(tmp_path, source, content)

    _assert_chain(
        workspace_path, source_path, messages, (str(attachment_path), "private-opaque-data")
    )


@pytest.mark.parametrize("source", ["codex", "claude-code"])
@pytest.mark.parametrize("attachment_type", ["local_image", "future_attachment"])
def test_opaque_only_human_action_has_empty_quotations(
    tmp_path: Path, source: SourceName, attachment_type: str
) -> None:
    content: list[JsonValue] = [{"type": attachment_type, "data": "private-opaque-data"}]

    workspace_path, source_path = _prepare(tmp_path, source, content)

    _assert_chain(workspace_path, source_path, (), ("private-opaque-data",))
