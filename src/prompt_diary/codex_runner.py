# coverage: ignore file
"""Typed skeleton for future Codex SDK runner integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import TracebackType


@dataclass(frozen=True)
class CodexBackendConfig:
    """Backend-level Codex configuration shared by compatible runners."""

    mcp_config_overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentConfig:
    """Per-conversation Codex agent configuration."""

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
    """Result from one Codex agent turn."""

    assistant_text: str
    events: tuple[AgentTurnEvent, ...]


class CodexBackend:
    """Async context manager for a future Codex SDK backend process."""

    def __init__(self, config: CodexBackendConfig) -> None:
        self.config = config

    async def __aenter__(self) -> CodexBackend:
        """Start and return the SDK backend."""
        raise NotImplementedError("Codex SDK backend startup is not implemented.")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the SDK backend."""
        del exc_type, exc, traceback


class CodexAgentRunner:
    """Owns one future Codex SDK conversation thread."""

    def __init__(self, backend: CodexBackend, config: AgentConfig) -> None:
        self.backend = backend
        self.config = config

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        """Run one prompt turn in the conversation."""
        del prompt, timeout_seconds, output_schema
        raise NotImplementedError("Codex SDK turn execution is not implemented.")
