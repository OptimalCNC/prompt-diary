"""Codex config overrides that register the Prompt Diary MCP server."""

from __future__ import annotations

import os
import re
from pathlib import Path

_SERVER_NAME = "prompt_diary"

# Match a single-table TOML header for a global MCP server or plugin and capture its first key
# segment, e.g. ``[mcp_servers.playwright.env]`` -> table ``mcp_servers`` key ``playwright`` and
# ``[plugins."github@openai-curated"]`` -> table ``plugins`` key ``"github@openai-curated"``.
# The first key segment is either a quoted key (preserving its quotes) or a bare key; any further
# ``.subkey`` segments and trailing whitespace/comment after ``]`` are ignored. The leading ``\[``
# only matches single ``[`` headers, so ``[[skills.config]]`` array-of-tables never match.
_GLOBAL_EXTRA_HEADER = re.compile(
    r"""^\[(?P<table>mcp_servers|plugins)\.(?P<key>"[^"]*"|'[^']*'|[A-Za-z0-9_-]+)"""
)


def default_codex_home() -> Path:
    """Resolve Codex's home dir: env ``CODEX_HOME`` if set (non-empty), else ``~/.codex``."""
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home)
    return Path.home() / ".codex"


def codex_global_extras_disable_overrides(codex_home: Path) -> tuple[str, ...]:
    """Return ``-c`` overrides that disable every global MCP server and plugin in
    ``<codex_home>/config.toml``, so our wrapped agent runs with only the prompt_diary MCP and no
    global plugins/skills. Returns ``()`` if the config file is absent.

    Each distinct ``(table, first-key)`` pair becomes ``f"{table}.{key}.enabled=false"`` in config
    order, with the key's original quoting preserved (e.g.
    ``plugins."github@openai-curated".enabled=false``). The prompt_diary MCP server is never
    disabled. ``[[skills.config]]`` array-of-tables entries are out of scope and not handled.
    """
    try:
        contents = (codex_home / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return ()

    overrides: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in contents.splitlines():
        match = _GLOBAL_EXTRA_HEADER.match(line.strip())
        if match is None:
            continue
        table = match.group("table")
        key = match.group("key")
        if table == "mcp_servers" and key == _SERVER_NAME:
            continue
        pair = (table, key)
        if pair in seen:
            continue
        seen.add(pair)
        overrides.append(f"{table}.{key}.enabled=false")
    return tuple(overrides)


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
