"""Codex config overrides that register the Prompt Diary MCP server."""

from __future__ import annotations

import os
import re
from pathlib import Path

_SERVER_NAME = "prompt_diary"

# Match a single-table TOML header for a global MCP server and capture its first key segment,
# e.g. ``[mcp_servers.playwright.env]`` -> ``playwright`` and
# ``[mcp_servers.agents-runner-workflow]`` -> ``agents-runner-workflow``. The first key segment is
# either a quoted key (preserving its quotes) or a bare key; any further ``.subkey`` segments and
# trailing whitespace/comment after ``]`` are ignored. The leading ``\[`` only matches single
# ``[`` headers, so ``[[skills.config]]`` array-of-tables never match.
#
# Only ``mcp_servers`` is handled: Codex (verified against codex 0.135.0) honors
# ``mcp_servers.<name>.enabled=false`` config overrides, but it silently IGNORES
# ``plugins.<name>.enabled=false`` overrides in every form, so global plugins/skills cannot be
# disabled this way and we deliberately do not emit misleading no-op plugin overrides.
_GLOBAL_MCP_HEADER = re.compile(r"""^\[mcp_servers\.(?P<key>"[^"]*"|'[^']*'|[A-Za-z0-9_-]+)""")


def default_codex_home() -> Path:
    """Resolve Codex's home dir: env ``CODEX_HOME`` if set (non-empty), else ``~/.codex``."""
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home)
    return Path.home() / ".codex"


def codex_global_mcp_disable_overrides(codex_home: Path) -> tuple[str, ...]:
    """Return ``-c`` overrides that disable every global MCP server in ``<codex_home>/config.toml``
    except prompt_diary, so our wrapped agent connects to only the prompt_diary MCP server.

    Each distinct server becomes ``f"mcp_servers.{key}.enabled=false"`` in config order (the key's
    original quoting preserved). Returns ``()`` if the config file is absent. Global plugins/skills
    are left untouched because Codex does not honor plugin-disable overrides (see the header note).
    """
    try:
        contents = (codex_home / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return ()

    overrides: list[str] = []
    seen: set[str] = set()
    for line in contents.splitlines():
        match = _GLOBAL_MCP_HEADER.match(line.strip())
        if match is None:
            continue
        key = match.group("key")
        if key == _SERVER_NAME or key in seen:
            continue
        seen.add(key)
        overrides.append(f"mcp_servers.{key}.enabled=false")
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
