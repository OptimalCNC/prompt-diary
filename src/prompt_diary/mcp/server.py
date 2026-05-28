"""Boilerplate MCP stdio server for Prompt Diary."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def prompt_diary_ping() -> dict[str, str]:
    """Return stable boilerplate data for MCP connectivity checks."""
    return {"name": "prompt-diary", "status": "ok"}


def build_mcp_server() -> FastMCP[None]:
    """Build the Prompt Diary MCP server without starting a transport."""
    server: FastMCP[None] = FastMCP("Prompt Diary")
    server.tool()(prompt_diary_ping)
    return server


def serve_mcp_server() -> None:
    """Serve Prompt Diary MCP tools over stdio."""
    build_mcp_server().run(transport="stdio")
