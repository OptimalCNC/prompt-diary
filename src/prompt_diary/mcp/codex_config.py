"""Codex config overrides that register the Prompt Diary MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SERVER_NAME = "prompt_diary"


def prompt_diary_mcp_overrides(workspace_path: Path) -> tuple[str, ...]:
    """Return Codex config-override strings registering the package MCP server.

    The server is launched as ``report mcp serve`` and is told which prepared workspace to
    write to through the ``PROMPT_DIARY_WORKSPACE`` environment variable, since a Codex-spawned
    stdio server does not inherit the agent thread's working directory.
    """
    workspace = str(workspace_path.resolve())
    prefix = f"mcp_servers.{_SERVER_NAME}"
    return (
        f'{prefix}.command="report"',
        f'{prefix}.args=["mcp","serve"]',
        f'{prefix}.default_tools_approval_mode="approve"',
        f'{prefix}.env.PROMPT_DIARY_WORKSPACE="{workspace}"',
    )
