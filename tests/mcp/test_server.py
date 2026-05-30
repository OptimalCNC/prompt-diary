from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest

from prompt_diary.mcp import server as mcp_server
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    assert_appended_result,
    assert_invalid_result,
    call_write_evidence_api,
    chain_with_value,
    copy_basic_evidence_workspace,
    result_to_dict,
    valid_material_doc_chain,
)

if TYPE_CHECKING:
    from pathlib import Path


class CallableMcpServer(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Awaitable[object]: ...


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
    assert "prompt_diary_ping" in [tool.name for tool in tools]


def test_write_evidence_is_registered_by_mcp_server() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())

    assert "write_evidence" in [tool.name for tool in tools]


def test_write_evidence_mcp_input_shape_contains_contract_fields() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())
    write_tool = next(tool for tool in tools if tool.name == "write_evidence")

    properties = write_tool.inputSchema["properties"]
    assert {"project_key", "session_ref", "evidence_chain"} <= set(properties)
    assert {"project_key", "session_ref", "evidence_chain"} <= set(
        write_tool.inputSchema["required"]
    )


def test_write_evidence_mcp_success_returns_committed_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    server = mcp_server.build_mcp_server()

    result = asyncio.run(
        _call_mcp_tool(
            server,
            "write_evidence",
            {
                "project_key": PROJECT_KEY,
                "session_ref": SESSION_REF,
                "evidence_chain": valid_material_doc_chain(),
            },
        )
    )

    assert_appended_result(result, turn_ref="T0001")


def test_write_evidence_mcp_invalid_result_matches_api_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_workspace = copy_basic_evidence_workspace(tmp_path / "api")
    mcp_workspace = copy_basic_evidence_workspace(tmp_path / "mcp")
    invalid_chain = chain_with_value(("outcomes", 0, "category"), "documentation")
    api_result = call_write_evidence_api(
        workspace_path=api_workspace,
        evidence_chain=invalid_chain,
    )
    monkeypatch.chdir(mcp_workspace)
    server = mcp_server.build_mcp_server()

    mcp_result = asyncio.run(
        _call_mcp_tool(
            server,
            "write_evidence",
            {
                "project_key": PROJECT_KEY,
                "session_ref": SESSION_REF,
                "evidence_chain": invalid_chain,
            },
        )
    )

    assert mcp_result == result_to_dict(api_result)
    assert_invalid_result(mcp_result, path="evidence_chain.outcomes[0].category")


def test_serve_mcp_server_runs_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = FakeServer()

    def build_fake_server() -> Any:
        return fake_server

    monkeypatch.setattr(mcp_server, "build_mcp_server", build_fake_server)

    mcp_server.serve_mcp_server()

    assert fake_server.run_calls == ["stdio"]


def test_write_evidence_uses_workspace_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path / "ws")
    monkeypatch.chdir(tmp_path)  # cwd is deliberately NOT the workspace
    monkeypatch.setenv("PROMPT_DIARY_WORKSPACE", str(workspace))

    result = mcp_server.write_evidence(PROJECT_KEY, SESSION_REF, valid_material_doc_chain())

    assert result_to_dict(result)["status"] == "appended"


async def _call_mcp_tool(
    server: CallableMcpServer,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = await server.call_tool(name, arguments)
    if isinstance(response, tuple):
        response_tuple = cast("tuple[object, ...]", response)
        structured_result = response_tuple[1]
        return result_to_dict(structured_result)
    if isinstance(response, Mapping):
        return dict(cast("Mapping[str, Any]", response))
    if isinstance(response, list):
        for content_block in cast("list[object]", response):
            text = getattr(content_block, "text", None)
            if isinstance(text, str):
                parsed = json.loads(text)
                return result_to_dict(parsed)
    pytest.fail(f"unexpected MCP tool response shape: {response!r}")
