"""Read exact human text from the indexed trigger's original source record."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from prompt_diary.source_records import (
    InputPart,
    UnavailableInput,
    parse_claude_input,
    parse_codex_input,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from prompt_diary.models import SourceName


@dataclass(frozen=True)
class TriggerText:
    """Human text blocks in source order; a recognized nontext action has no messages."""

    messages: tuple[str, ...]


@dataclass(frozen=True)
class TriggerReadError:
    """An unavailable or unrecognized trigger must not be committed as an empty quotation."""

    message: str


def read_trigger_text(
    *, session_path: Path, source: SourceName, line: int
) -> TriggerText | TriggerReadError:
    """Extract exact human text from one JSONL line; opaque human attachments add no text."""
    try:
        lines = session_path.read_bytes().decode("utf-8", errors="replace").splitlines()
    except OSError as exc:
        return TriggerReadError(f"could not read the copied session file: {exc.strerror}")
    if line < 1 or line > len(lines):
        return TriggerReadError(f"trigger line {line} is outside the copied session file")
    record = _json_object(lines[line - 1])
    if record is None:
        return TriggerReadError(f"trigger line {line} is not a JSON object")
    previous = _json_object(lines[line - 2]) if line > 1 else None
    parsed = (
        parse_codex_input(record) if source == "codex" else parse_claude_input(record, previous)
    )
    if parsed is None or not parsed.is_human_trigger:
        return TriggerReadError(f"trigger line {line} is not a recognized human input or action")
    if any(
        isinstance(part, UnavailableInput)
        or (isinstance(part, InputPart) and part.kind == "unknown")
        for part in parsed.parts
    ):
        return TriggerReadError(f"trigger line {line} contains unsupported or malformed input")
    return TriggerText(
        tuple(
            part.text
            for part in parsed.parts
            if isinstance(part, InputPart) and part.kind in ("human_input", "human_command")
        )
    )


def _json_object(raw: str) -> Mapping[str, object] | None:
    try:
        value = cast("object", json.loads(raw))
    except json.JSONDecodeError:
        return None
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else None
