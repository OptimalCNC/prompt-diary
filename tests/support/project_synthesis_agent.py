"""Prompt-reading fake agent that synthesizes work items via the real write_work_item API."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prompt_diary.agent import AgentTurnResult
from prompt_diary.generate.project_synthesis.mcp import (
    WriteWorkItemAppendedResult,
    WriteWorkItemResult,
    write_work_item,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prompt_diary.agent import AgentConfig

_PROJECT_KEY_RE = re.compile(r"^- Project key: (.+)$", re.MULTILINE)
_CHAIN_RE = re.compile(r"^\*\*(S\d{4})/(T\d{4})\*\* \[", re.MULTILINE)


@dataclass
class _RefCounter:
    value: int = 0

    def next(self) -> str:
        self.value += 1
        return f"W{self.value:04d}"


@dataclass
class GroupingAgentRunner:
    """One fake conversation that groups committed turns and buckets gap turns."""

    config: AgentConfig
    cover_gaps: bool
    processed: list[str]
    prompts: list[str] = field(default_factory=list)

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del timeout_seconds, output_schema
        self.prompts.append(prompt)
        project_key = _require_project_key(prompt)
        counter = _RefCounter()
        uncovered = self._cover_committed(project_key, _committed_by_session(prompt), counter)
        if self.cover_gaps:
            self._cover_gaps(project_key, uncovered, counter)
        return AgentTurnResult(assistant_text="synthesized", events=())

    def _cover_committed(
        self,
        project_key: str,
        grouped: tuple[tuple[str, tuple[str, ...]], ...],
        counter: _RefCounter,
    ) -> tuple[tuple[str, str], ...]:
        uncovered: tuple[tuple[str, str], ...] = ()
        for session_ref, turn_refs in grouped:
            item = _material_work_item(counter.next(), session_ref, turn_refs)
            result = self._write(project_key, item)
            uncovered = _uncovered_of(result)
        return uncovered

    def _cover_gaps(
        self,
        project_key: str,
        uncovered: tuple[tuple[str, str], ...],
        counter: _RefCounter,
    ) -> None:
        remaining = uncovered
        while remaining:
            result = self._write(project_key, _gap_work_item(counter.next(), remaining))
            remaining = _uncovered_of(result)

    def _write(self, project_key: str, work_item: dict[str, Any]) -> WriteWorkItemResult:
        result = write_work_item(
            workspace_path=self.config.working_directory,
            project_key=project_key,
            work_item=work_item,
        )
        self.processed.append(str(work_item["work_item_ref"]))
        return result


@dataclass
class GroupingAgentSessionFactory:
    """Mints grouping fake runners off a shared record; never starts Codex."""

    cover_gaps: bool = True
    entered: int = 0
    exited: int = 0
    processed: list[str] = field(default_factory=list)
    runners: list[GroupingAgentRunner] = field(default_factory=list)

    async def __aenter__(self) -> GroupingAgentSessionFactory:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None:
        del exc_type, exc, traceback

    async def runner(self, config: AgentConfig) -> GroupingAgentRunner:
        new_runner = GroupingAgentRunner(
            config=config, cover_gaps=self.cover_gaps, processed=self.processed
        )
        self.runners.append(new_runner)
        return new_runner


def _committed_by_session(prompt: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    order: list[str] = []
    turns: dict[str, list[str]] = {}
    for session_ref, turn in _CHAIN_RE.findall(prompt):
        if session_ref not in turns:
            turns[session_ref] = []
            order.append(session_ref)
        turns[session_ref].append(turn)
    return tuple((session_ref, tuple(turns[session_ref])) for session_ref in order)


def _material_work_item(
    work_item_ref: str, session_ref: str, turn_refs: tuple[str, ...]
) -> dict[str, Any]:
    cite = [{"session_ref": session_ref, "turn_ref": turn_refs[0]}]
    return {
        "work_item_ref": work_item_ref,
        "kind": "material_work_item",
        "title": f"Work thread in {session_ref}",
        "covered_turns": [{"session_ref": session_ref, "turn_ref": turn} for turn in turn_refs],
        "trigger": {"summary": f"User drove work in {session_ref}.", "evidence_refs": cite},
        "agent_reaction": {"summary": "Agent acted across the thread.", "main_actions": ["work"]},
        "outcomes": [
            {
                "category": "process_outcome",
                "summary": "Thread progressed.",
                "evidence_refs": cite,
                "confidence": "medium",
            }
        ],
        "terminal_states": [
            {"type": "material_result", "summary": "Thread concluded.", "evidence_refs": cite}
        ],
        "limits": [],
        "confidence": "medium",
    }


def _gap_work_item(work_item_ref: str, uncovered: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {
        "work_item_ref": work_item_ref,
        "kind": "evidence_gap_item",
        "title": "Indexed turns with no extractable evidence",
        "covered_turns": [
            {"session_ref": session_ref, "turn_ref": turn} for session_ref, turn in uncovered
        ],
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def _uncovered_of(result: WriteWorkItemResult) -> tuple[tuple[str, str], ...]:
    if isinstance(result, WriteWorkItemAppendedResult):
        return tuple((ref.session_ref, ref.turn_ref) for ref in result.uncovered_turns)
    raise AssertionError(_rejected_message(result))


def _require_project_key(prompt: str) -> str:
    match = _PROJECT_KEY_RE.search(prompt)
    if match is None:
        raise AssertionError(_missing_project_key_message())
    return match.group(1).strip()


def _rejected_message(result: WriteWorkItemResult) -> str:
    return f"fake agent write_work_item was rejected: {result!r}"


def _missing_project_key_message() -> str:
    return "fake agent could not find the project key in the prompt"
