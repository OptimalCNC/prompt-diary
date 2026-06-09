from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import tests.conftest as project_conftest

if TYPE_CHECKING:
    import pytest


class _FakeConfig:
    def __init__(self, enabled_options: set[str] | None = None) -> None:
        self._enabled_options = enabled_options or set()

    def getoption(self, name: str) -> bool:
        return name in self._enabled_options


class _FakeItem:
    def __init__(self, markers: set[str]) -> None:
        self._markers = markers
        self.added_markers: list[Any] = []

    def get_closest_marker(self, name: str) -> object | None:
        if name in self._markers:
            return object()
        return None

    def add_marker(self, marker: object) -> None:
        self.added_markers.append(marker)


def test_codex_mcp_tests_skip_by_default() -> None:
    item = _FakeItem({"codex_mcp"})

    project_conftest.pytest_collection_modifyitems(
        cast("pytest.Config", _FakeConfig()), [cast("pytest.Item", item)]
    )

    assert _skip_reasons(item) == ["need --run-codex-mcp option to run"]


def test_notion_published_tests_skip_by_default() -> None:
    item = _FakeItem({"notion_published"})

    project_conftest.pytest_collection_modifyitems(
        cast("pytest.Config", _FakeConfig()), [cast("pytest.Item", item)]
    )

    assert _skip_reasons(item) == ["need --run-notion-published option to run"]


def test_opt_in_markers_run_when_enabled() -> None:
    codex_item = _FakeItem({"codex_mcp"})
    notion_item = _FakeItem({"notion_published"})
    config = _FakeConfig({"--run-codex-mcp", "--run-notion-published"})

    project_conftest.pytest_collection_modifyitems(
        cast("pytest.Config", config),
        [cast("pytest.Item", codex_item), cast("pytest.Item", notion_item)],
    )

    assert _skip_reasons(codex_item) == []
    assert _skip_reasons(notion_item) == []


def _skip_reasons(item: _FakeItem) -> list[str]:
    return [
        cast("str", marker.mark.kwargs["reason"])
        for marker in item.added_markers
        if marker.mark.name == "skip"
    ]
