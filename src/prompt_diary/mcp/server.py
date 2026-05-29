"""Boilerplate MCP stdio server for Prompt Diary."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from prompt_diary.generate.evidence_extraction.mcp import write_evidence as write_evidence_api


def prompt_diary_ping() -> dict[str, str]:
    """Return stable boilerplate data for MCP connectivity checks."""
    return {"name": "prompt-diary", "status": "ok"}


def write_evidence(
    project_key: str,
    session_ref: str,
    evidence_chain: dict[str, object],
) -> object:
    """Validate and append one evidence chain from the current prepared workspace."""
    return write_evidence_api(
        workspace_path=Path.cwd(),
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
