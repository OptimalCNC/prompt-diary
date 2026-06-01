"""Boilerplate MCP stdio server for Prompt Diary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

import pydantic
from mcp.server.fastmcp import FastMCP

from prompt_diary.generate.evidence_extraction.mcp import write_evidence as write_evidence_api
from prompt_diary.generate.evidence_extraction.session_reader import (
    read_session_lines as read_session_lines_api,
)
from prompt_diary.generate.project_synthesis.mcp import write_work_item as write_work_item_api

_MODE_WARNING = (
    "Output verbosity. 'compact' (default) returns bounded structured records. "
    "'full' returns raw JSONL lines and can be very large, so use it only for a narrow "
    "line range where exact raw content is necessary."
)

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


def write_work_item(
    project_key: str,
    work_item: dict[str, object],
) -> object:
    """Validate and append one work item from the resolved prepared workspace."""
    return write_work_item_api(
        workspace_path=_resolve_workspace(),
        project_key=project_key,
        work_item=work_item,
    )


def read_session_lines(
    project_key: str,
    session_ref: str,
    start_line: int,
    end_line: int,
    mode: Annotated[Literal["compact", "full"], pydantic.Field(description=_MODE_WARNING)] = (
        "compact"
    ),
) -> object:
    """Read a physical line range from one indexed session in the resolved prepared workspace."""
    return read_session_lines_api(
        workspace_path=_resolve_workspace(),
        project_key=project_key,
        session_ref=session_ref,
        start_line=start_line,
        end_line=end_line,
        mode=mode,
    )


def build_mcp_server() -> FastMCP[None]:
    """Build the Prompt Diary MCP server without starting a transport."""
    server: FastMCP[None] = FastMCP("Prompt Diary")
    server.tool()(prompt_diary_ping)
    server.tool()(write_evidence)
    server.tool()(write_work_item)
    server.tool()(read_session_lines)
    return server


def serve_mcp_server() -> None:
    """Serve Prompt Diary MCP tools over stdio."""
    build_mcp_server().run(transport="stdio")
