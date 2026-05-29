"""Neutral agent execution contract shared by generation phases and runner adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import TracebackType


@dataclass(frozen=True)
class AgentConfig:
    """Per-conversation agent configuration."""

    working_directory: Path
    model: str | None = None
    model_provider: str | None = None
    reasoning_effort: str | None = None
    approval_mode: str | None = None
    sandbox: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    personality: str | None = None


@dataclass(frozen=True)
class AgentTurnEvent:
    """Structured event summary emitted while running one agent turn."""

    kind: str
    summary: str
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class AgentTurnResult:
    """Result from one agent turn."""

    assistant_text: str
    events: tuple[AgentTurnEvent, ...]


class AgentRunner(Protocol):
    """One agent conversation. Turns run sequentially on a single instance."""

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        """Run one prompt turn and return its structured result."""
        ...


class AgentSessionFactory(Protocol):
    """Owns one shared backend and mints a fresh agent conversation per call."""

    async def __aenter__(self) -> AgentSessionFactory:
        """Start the shared backend."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Stop the shared backend."""
        ...

    async def runner(self, config: AgentConfig) -> AgentRunner:
        """Return a fresh agent conversation bound to the shared backend."""
        ...
