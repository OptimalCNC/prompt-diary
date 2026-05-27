from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from prompt_diary.codex_runner import (
    AgentConfig,
    AgentTurnEvent,
    AgentTurnResult,
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.codex_mcp


def test_codex_runner_contracts_are_typed_boilerplate(tmp_path: Path) -> None:
    backend_config = CodexBackendConfig(mcp_config_overrides=("mcp.prompt_diary",))
    agent_config = AgentConfig(
        working_directory=tmp_path,
        model="codex-test",
        model_provider="openai",
        reasoning_effort="low",
        approval_mode="never",
        sandbox="workspace-write",
        base_instructions="base",
        developer_instructions="developer",
        personality="concise",
    )
    event = AgentTurnEvent(kind="tool", summary="called ping", metadata={"tool": "ping"})
    result = AgentTurnResult(assistant_text="done", events=(event,))

    assert backend_config.mcp_config_overrides == ("mcp.prompt_diary",)
    assert agent_config.working_directory == tmp_path
    assert agent_config.model_provider == "openai"
    assert result.events[0].metadata == {"tool": "ping"}


def test_codex_backend_start_is_not_implemented() -> None:
    backend = CodexBackend(CodexBackendConfig())

    async def exercise() -> None:
        async with backend:
            pass

    with pytest.raises(NotImplementedError, match="backend startup"):
        asyncio.run(exercise())


def test_codex_agent_runner_turn_is_not_implemented(tmp_path: Path) -> None:
    backend = CodexBackend(CodexBackendConfig())
    runner = CodexAgentRunner(backend, AgentConfig(working_directory=tmp_path))

    async def exercise() -> None:
        await runner.turn(
            "Generate the report.",
            timeout_seconds=1.0,
            output_schema={"type": "object"},
        )

    with pytest.raises(NotImplementedError, match="turn execution"):
        asyncio.run(exercise())
