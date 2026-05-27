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
