from __future__ import annotations

from typing import cast

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-codex-mcp",
        action="store_true",
        default=False,
        help="run opt-in Codex MCP integration contract tests",
    )
    parser.addoption(
        "--run-notion-published",
        action="store_true",
        default=False,
        help="run opt-in live Notion publish tests",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _skip_unless_enabled(
        config=config,
        items=items,
        option="--run-codex-mcp",
        marker="codex_mcp",
        reason="need --run-codex-mcp option to run",
    )
    _skip_unless_enabled(
        config=config,
        items=items,
        option="--run-notion-published",
        marker="notion_published",
        reason="need --run-notion-published option to run",
    )


def _skip_unless_enabled(
    *,
    config: pytest.Config,
    items: list[pytest.Item],
    option: str,
    marker: str,
    reason: str,
) -> None:
    if cast("bool", config.getoption(option)):
        return

    skip_marker = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker(marker) is not None:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _isolate_prompt_diary_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolate Prompt Diary config so tests never read the real config or ambient credentials.

    Points PROMPT_DIARY_CONFIG at a unique temp path and clears the Notion credential env vars so a
    test must opt in to configuration. That keeps credential-resolution tests deterministic and
    prevents an ambient NOTION_API_KEY / NOTION_PAGE_ID in the developer's shell from changing CLI
    behavior.
    """
    config_dir = tmp_path_factory.mktemp("prompt-diary-config")
    # The directory exists but config.json does not, so load_config() defaults to empty until a
    # test writes it; a test's own monkeypatch.setenv overrides these defaults.
    monkeypatch.setenv("PROMPT_DIARY_CONFIG", str(config_dir / "config.json"))
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_PAGE_ID", raising=False)
    monkeypatch.delenv("PROMPT_DIARY_CONTENT_LANGUAGE", raising=False)
