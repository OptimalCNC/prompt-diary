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
    """Point PROMPT_DIARY_CONFIG at a unique temp path so tests never touch the real config."""
    config_dir = tmp_path_factory.mktemp("prompt-diary-config")
    # The directory exists but config.json does not, so load_config() defaults to empty until a
    # test writes it; a test's own monkeypatch.setenv overrides this default.
    monkeypatch.setenv("PROMPT_DIARY_CONFIG", str(config_dir / "config.json"))
