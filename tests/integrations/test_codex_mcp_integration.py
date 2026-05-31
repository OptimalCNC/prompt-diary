from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from prompt_diary.agent import AgentConfig
from prompt_diary.integrations.codex_runner import (
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
)
from prompt_diary.mcp.codex_config import prompt_diary_mcp_overrides

pytestmark = pytest.mark.codex_mcp


def test_codex_runner_live_replies_pong() -> None:
    pytest.importorskip("openai_codex")
    codex_path = shutil.which("codex")

    async def exercise() -> None:
        async with CodexBackend(
            CodexBackendConfig(codex_bin=Path(codex_path) if codex_path is not None else None)
        ) as backend:
            runner = CodexAgentRunner(
                backend,
                AgentConfig(
                    working_directory=Path.cwd(),
                    approval_mode="deny_all",
                    sandbox="workspace-write",
                ),
            )
            result = await runner.turn("Reply exactly with PONG.", timeout_seconds=30.0)
            assert result.assistant_text.strip() == "PONG"

    asyncio.run(exercise())


def test_codex_runner_live_approved_prompt_diary_mcp_tool_under_auto_review(
    tmp_path: Path,
) -> None:
    pytest.importorskip("openai_codex")
    codex_path = shutil.which("codex")

    async def exercise() -> None:
        async with CodexBackend(
            CodexBackendConfig(
                codex_bin=Path(codex_path) if codex_path is not None else None,
                mcp_config_overrides=prompt_diary_mcp_overrides(tmp_path),
            )
        ) as backend:
            runner = CodexAgentRunner(
                backend,
                AgentConfig(
                    working_directory=tmp_path,
                    approval_mode="auto_review",
                    sandbox="workspace-write",
                ),
            )
            result = await runner.turn(
                "Call the prompt_diary_ping MCP tool exactly once. "
                "If it returns status ok, reply exactly MCP_OK.",
                timeout_seconds=90.0,
            )
            assert result.assistant_text.strip() == "MCP_OK"

    asyncio.run(exercise())
