from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from prompt_diary.mcp.codex_config import prompt_diary_mcp_overrides


def test_overrides_register_server_command_args_and_workspace(tmp_path: Path) -> None:
    overrides = prompt_diary_mcp_overrides(tmp_path)
    joined = "\n".join(overrides)

    assert any("mcp_servers.prompt_diary.command" in item for item in overrides)
    assert any('"mcp"' in item and '"serve"' in item for item in overrides)
    assert str(tmp_path.resolve()) in joined
    assert "PROMPT_DIARY_WORKSPACE" in joined
