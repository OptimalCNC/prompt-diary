"""Language-norm wrapper for generation agent factories."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from prompt_diary.language import (
    LanguageNorm,
    render_language_instructions,
    write_generated_agents_file,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from prompt_diary.agent import AgentConfig, AgentRunner, AgentSessionFactory


@dataclass
class LanguageNormAgentSessionFactory:
    """Inject the configured Prompt Diary content-language norm into every agent runner."""

    inner: AgentSessionFactory
    workspace_path: Path
    language: LanguageNorm
    _agents_written: bool = field(default=False, init=False)

    async def __aenter__(self) -> LanguageNormAgentSessionFactory:
        """Start the wrapped shared backend."""
        self.inner = await self.inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Stop the wrapped shared backend."""
        return await self.inner.__aexit__(exc_type, exc, traceback)

    async def runner(self, config: AgentConfig) -> AgentRunner:
        """Return a runner with the workspace AGENTS.md and developer instructions prepared."""
        if not self._agents_written:
            write_generated_agents_file(self.workspace_path, self.language)
            self._agents_written = True
        return await self.inner.runner(_with_language_instructions(config, self.language))


def _with_language_instructions(config: AgentConfig, language: LanguageNorm) -> AgentConfig:
    rendered = render_language_instructions(language)
    existing = config.developer_instructions
    developer_instructions = f"{existing}\n\n{rendered}" if existing else rendered
    return replace(config, developer_instructions=developer_instructions)
