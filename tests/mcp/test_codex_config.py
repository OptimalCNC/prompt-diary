from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from prompt_diary.mcp.codex_config import (
    codex_clean_startup_overrides,
    codex_global_mcp_disable_overrides,
    default_codex_home,
    prompt_diary_mcp_overrides,
)

if TYPE_CHECKING:
    import pytest


def test_overrides_register_server_command_args_and_workspace(tmp_path: Path) -> None:
    overrides = prompt_diary_mcp_overrides(tmp_path)
    joined = "\n".join(overrides)

    assert any("mcp_servers.prompt_diary.command" in item for item in overrides)
    assert any('"mcp"' in item and '"serve"' in item for item in overrides)
    assert str(tmp_path.resolve()) in joined
    assert "PROMPT_DIARY_WORKSPACE" in joined


def test_overrides_approve_prompt_diary_mcp_tools_by_default(tmp_path: Path) -> None:
    overrides = prompt_diary_mcp_overrides(tmp_path)

    assert 'mcp_servers.prompt_diary.default_tools_approval_mode="approve"' in overrides


def test_disable_overrides_cover_global_mcp_servers_only(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            (
                "[mcp_servers.playwright]",
                'command = "npx"',
                "[mcp_servers.playwright.env]",
                'KEY = "value"',
                "[mcp_servers.agents-runner-workflow]",
                "enabled = false",
                '[plugins."github@openai-curated"]',
                "enabled = true",
                "[mcp_servers.prompt_diary]",
                'command = "report"',
            )
        ),
        encoding="utf-8",
    )

    overrides = codex_global_mcp_disable_overrides(tmp_path)

    assert "mcp_servers.playwright.enabled=false" in overrides
    assert "mcp_servers.agents-runner-workflow.enabled=false" in overrides
    assert overrides.count("mcp_servers.playwright.enabled=false") == 1
    # prompt_diary (our own server) is never disabled.
    assert not any("prompt_diary" in item for item in overrides)
    # Codex does not honor plugins.*.enabled=false overrides, so plugins are intentionally NOT
    # emitted (emitting them would be a misleading no-op).
    assert not any("plugins" in item for item in overrides)


def test_disable_overrides_without_config_file_returns_empty(tmp_path: Path) -> None:
    assert codex_global_mcp_disable_overrides(tmp_path) == ()


def test_clean_startup_overrides_disable_plugins_feature_and_global_mcp(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.playwright]\ncommand = "npx"\n', encoding="utf-8")

    overrides = codex_clean_startup_overrides(tmp_path)

    # Disabling the plugins feature drops the global skills catalog + skill auto-loading; it works
    # via a bare key, unlike the per-plugin enabled override Codex silently ignores.
    assert "features.plugins=false" in overrides
    assert "mcp_servers.playwright.enabled=false" in overrides


def test_clean_startup_overrides_without_config_still_disable_plugins(tmp_path: Path) -> None:
    assert codex_clean_startup_overrides(tmp_path) == ("features.plugins=false",)


def test_default_codex_home_uses_codex_home_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert default_codex_home() == tmp_path


def test_default_codex_home_falls_back_to_home_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)

    assert default_codex_home() == Path.home() / ".codex"


def test_default_codex_home_treats_empty_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "")

    assert default_codex_home() == Path.home() / ".codex"
