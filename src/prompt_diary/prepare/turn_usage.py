"""Read turn output-token counts from source-native usage records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from prompt_diary.models import JsonObject


@dataclass
class CodexTurnUsage:
    """Difference cumulative snapshots across human triggers, without counting echoes twice."""

    total: int | None = None
    baseline: int | None = None
    seen_usage: bool = False
    valid: bool = True
    generated: bool = False
    generated_after_usage: bool = False
    terminal: bool = False

    def start_turn(self) -> None:
        self.baseline = self.total
        self.seen_usage = False
        self.valid = True
        self.generated = False
        self.generated_after_usage = False
        self.terminal = False

    def observe(self, record: JsonObject | None) -> None:
        if record is None:
            self.valid = False
            self.total = None
            return
        payload = _object(record.get("payload"))
        if payload is None:
            return
        if record.get("type") == "event_msg":
            if payload.get("type") == "token_count":
                self._record_usage(_object(payload.get("info")))
            elif payload.get("type") in ("task_complete", "turn_aborted", "task_failed"):
                self.terminal = True
        elif record.get("type") == "response_item" and (
            payload.get("type") in ("reasoning", "function_call", "custom_tool_call")
            or (payload.get("type") == "message" and payload.get("role") == "assistant")
        ):
            self.generated = True
            self.generated_after_usage = True

    def _record_usage(self, info: JsonObject | None) -> None:
        if info is None:
            return
        total = _output_tokens(_object(info.get("total_token_usage")))
        if total is None:
            self.valid = False
            self.total = None
            return
        if not self.seen_usage and self.baseline is None:
            last = _output_tokens(_object(info.get("last_token_usage")))
            if total == last:
                self.baseline = 0
        if self.total is not None and total < self.total:
            self.valid = False
        self.total = total
        self.seen_usage = True
        self.generated_after_usage = False

    def finish_turn(self, *, bounded: bool) -> int | None:
        if (
            not self.valid
            or not self.seen_usage
            or self.baseline is None
            or self.total is None
            or self.generated_after_usage
            or (not bounded and not self.terminal)
        ):
            return None
        output_tokens = self.total - self.baseline
        return output_tokens if output_tokens or not self.generated else None


@dataclass
class ClaudeTurnUsage:
    """Sum native assistant usage once per message, including repeated content blocks."""

    messages: dict[str, int] = field(default_factory=dict)
    valid: bool = True

    def start_turn(self) -> None:
        self.messages.clear()
        self.valid = True

    def observe(self, record: JsonObject | None) -> None:
        if record is None:
            self.valid = False
            return
        if record.get("type") != "assistant":
            return
        message = _object(record.get("message"))
        if message is None:
            self.valid = False
            return
        identity = message.get("id") or record.get("uuid")
        output_tokens = _output_tokens(_object(message.get("usage")))
        if not isinstance(identity, str) or output_tokens is None:
            self.valid = False
            return
        self.messages[identity] = max(self.messages.get(identity, 0), output_tokens)

    def finish_turn(self, *, bounded: bool) -> int | None:
        del bounded
        return sum(self.messages.values()) if self.valid and self.messages else None


def _object(value: object) -> JsonObject | None:
    return cast("JsonObject", value) if isinstance(value, dict) else None


def _output_tokens(usage: JsonObject | None) -> int | None:
    value = usage.get("output_tokens") if usage is not None else None
    return value if type(value) is int and value >= 0 else None
