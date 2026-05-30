"""Prompt-reading fake agent that performs the real write_evidence side effect."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentTurnResult
from prompt_diary.generate.evidence_extraction.mcp import write_evidence
from tests.support.evidence_extraction import build_evidence_chain

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prompt_diary.agent import AgentConfig

_JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_PROJECT_KEY_RE = re.compile(r"^- Project key: (.+)$", re.MULTILINE)
_SESSION_REF_RE = re.compile(r"^- Session reference: (.+)$", re.MULTILINE)


@dataclass
class EvidenceWritingAgentRunner:
    """One per-session fake conversation that writes evidence via the real API."""

    config: AgentConfig
    processed: list[tuple[str, str]]
    fail_turns: frozenset[str]
    prompts: list[str] = field(default_factory=list)
    project_key: str | None = None
    session_ref: str | None = None

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del timeout_seconds, output_schema
        self.prompts.append(prompt)
        target_turn = _last_json_block(prompt)
        turn_ref = cast("str", target_turn["turn_ref"])
        self.project_key = _parse_first(_PROJECT_KEY_RE, prompt) or self.project_key
        self.session_ref = _parse_first(_SESSION_REF_RE, prompt) or self.session_ref
        project_key = _require(self.project_key, "project_key")
        session_ref = _require(self.session_ref, "session_ref")
        if turn_ref not in self.fail_turns:
            span = (int(target_turn["turn_start_line"]), int(target_turn["turn_end_line"]))
            write_evidence(
                workspace_path=self.config.working_directory,
                project_key=project_key,
                session_ref=session_ref,
                evidence_chain=build_evidence_chain(turn_ref=turn_ref, span=span),
            )
        self.processed.append((session_ref, turn_ref))
        return AgentTurnResult(assistant_text=f"processed {turn_ref}", events=())


@dataclass
class EvidenceWritingAgentSessionFactory:
    """Mints per-session evidence-writing fake runners off a shared record."""

    fail_turns: frozenset[str] = frozenset()
    entered: int = 0
    exited: int = 0
    processed: list[tuple[str, str]] = field(default_factory=list)
    runners: list[EvidenceWritingAgentRunner] = field(default_factory=list)

    async def __aenter__(self) -> EvidenceWritingAgentSessionFactory:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None:
        del exc_type, exc, traceback
        self.exited += 1

    async def runner(self, config: AgentConfig) -> EvidenceWritingAgentRunner:
        new_runner = EvidenceWritingAgentRunner(
            config=config, processed=self.processed, fail_turns=self.fail_turns
        )
        self.runners.append(new_runner)
        return new_runner


def _last_json_block(prompt: str) -> dict[str, Any]:
    blocks = _JSON_BLOCK_RE.findall(prompt)
    if not blocks:
        raise AssertionError(_no_json_block_message())
    raw: object = json.loads(blocks[-1])
    return cast("dict[str, Any]", raw)


def _parse_first(pattern: re.Pattern[str], prompt: str) -> str | None:
    match = pattern.search(prompt)
    return match.group(1).strip() if match else None


def _require(value: str | None, label: str) -> str:
    if value is None:
        raise AssertionError(_missing_context_message(label))
    return value


def _no_json_block_message() -> str:
    return "prompt has no ```json block to read the target turn from"


def _missing_context_message(label: str) -> str:
    return f"fake agent could not determine {label} from the prompt"
