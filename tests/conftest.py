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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if cast("bool", config.getoption("--run-codex-mcp")):
        return

    skip_codex_mcp = pytest.mark.skip(reason="need --run-codex-mcp option to run")
    for item in items:
        if item.get_closest_marker("codex_mcp") is not None:
            item.add_marker(skip_codex_mcp)


@pytest.fixture(autouse=True)
def _isolate_prompt_diary_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolate Prompt Diary config so tests never read the real config or ambient credentials.

    Points PROMPT_DIARY_CONFIG at a unique temp path and clears the Notion credential env vars so a
    test must opt in to configuration. Without the env clearing, an ambient NOTION_API_KEY /
    NOTION_PAGE_ID in the developer's shell would make ``report generate``'s config-aware default
    publish — even firing a real network publish from the end-to-end tests.
    """
    config_dir = tmp_path_factory.mktemp("prompt-diary-config")
    # The directory exists but config.json does not, so load_config() defaults to empty until a
    # test writes it; a test's own monkeypatch.setenv overrides these defaults.
    monkeypatch.setenv("PROMPT_DIARY_CONFIG", str(config_dir / "config.json"))
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_PAGE_ID", raising=False)
