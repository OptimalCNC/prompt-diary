"""Reusable in-memory fakes for the agent seam, used by phase and seam tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from prompt_diary.agent import AgentConfig, AgentRunner, AgentTurnResult


@dataclass
class FakeAgentRunner:
    """Records prompts and returns a scripted result, optionally with side effects."""

    config: AgentConfig
    script: Callable[[str, AgentConfig], AgentTurnResult]
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
        return self.script(prompt, self.config)


@dataclass
class FakeAgentSessionFactory:
    """Counts lifecycle entries and mints recording runners; never starts Codex."""

    script: Callable[[str, AgentConfig], AgentTurnResult]
    entered: int = 0
    exited: int = 0
    runners: list[FakeAgentRunner] = field(default_factory=list)

    async def __aenter__(self) -> FakeAgentSessionFactory:
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

    async def runner(self, config: AgentConfig) -> AgentRunner:
        new_runner = FakeAgentRunner(config=config, script=self.script)
        self.runners.append(new_runner)
        return new_runner
