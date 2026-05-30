"""Boilerplate MCP stdio server for Prompt Diary."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from prompt_diary.generate.evidence_extraction.mcp import write_evidence as write_evidence_api

_WORKSPACE_ENV = "PROMPT_DIARY_WORKSPACE"


def _resolve_workspace() -> Path:
    """Resolve the prepared workspace root for MCP tool calls."""
    override = os.environ.get(_WORKSPACE_ENV)
    return Path(override) if override else Path.cwd()


def prompt_diary_ping() -> dict[str, str]:
    """Return stable boilerplate data for MCP connectivity checks."""
    return {"name": "prompt-diary", "status": "ok"}


def write_evidence(
    project_key: str,
    session_ref: str,
    evidence_chain: dict[str, object],
) -> object:
    """Validate and append one evidence chain from the resolved prepared workspace."""
    return write_evidence_api(
        workspace_path=_resolve_workspace(),
        project_key=project_key,
        session_ref=session_ref,
        evidence_chain=evidence_chain,
    )


def build_mcp_server() -> FastMCP[None]:
    """Build the Prompt Diary MCP server without starting a transport."""
    server: FastMCP[None] = FastMCP("Prompt Diary")
    server.tool()(prompt_diary_ping)
    server.tool()(write_evidence)
    return server


def serve_mcp_server() -> None:
    """Serve Prompt Diary MCP tools over stdio."""
    build_mcp_server().run(transport="stdio")
