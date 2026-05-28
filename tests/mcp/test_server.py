from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from prompt_diary.mcp import server as mcp_server

if TYPE_CHECKING:
    import pytest


class FakeServer:
    def __init__(self) -> None:
        self.run_calls: list[str] = []

    def run(self, *, transport: str) -> None:
        self.run_calls.append(transport)


def test_prompt_diary_ping_returns_stable_boilerplate() -> None:
    assert mcp_server.prompt_diary_ping() == {
        "name": "prompt-diary",
        "status": "ok",
    }


def test_build_mcp_server_constructs_without_running() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())

    assert server.name == "Prompt Diary"
    assert [tool.name for tool in tools] == ["prompt_diary_ping"]


def test_serve_mcp_server_runs_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = FakeServer()

    def build_fake_server() -> Any:
        return fake_server

    monkeypatch.setattr(mcp_server, "build_mcp_server", build_fake_server)

    mcp_server.serve_mcp_server()

    assert fake_server.run_calls == ["stdio"]
