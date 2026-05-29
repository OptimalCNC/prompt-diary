"""MCP command registration."""

from __future__ import annotations

import typer

from prompt_diary.mcp.server import serve_mcp_server


def register(app: typer.Typer) -> None:
    """Register MCP commands."""
    mcp_app = typer.Typer(help="Run MCP server commands.")
    mcp_app.command(name="serve")(mcp_serve)
    app.add_typer(mcp_app, name="mcp")


def mcp_serve() -> None:
    """Run the MCP server over stdio."""
    serve_mcp_server()
