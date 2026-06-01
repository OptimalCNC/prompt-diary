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
from tests.support.project_synthesis import (
    PROJECT_KEY as PS_PROJECT_KEY,
)
from tests.support.project_synthesis import (
    assert_appended_result as assert_work_item_appended,
)
from tests.support.project_synthesis import (
    assert_invalid_result as assert_work_item_invalid,
)
from tests.support.project_synthesis import (
    call_write_work_item_api,
    copy_basic_project_workspace,
    valid_material_work_item,
    work_item_with_value,
)
from tests.support.project_synthesis import (
    result_to_dict as work_item_result_to_dict,
)
from tests.support.session_reader import (
    PROJECT_KEY as READER_PROJECT_KEY,
)
from tests.support.session_reader import (
    SESSION_REF as READER_SESSION_REF,
)
from tests.support.session_reader import (
    call_read_session_lines,
    copy_session_reader_workspace,
)
from tests.support.session_reader import (
    result_to_dict as read_result_to_dict,
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


def test_write_work_item_is_registered_by_mcp_server() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())

    assert "write_work_item" in [tool.name for tool in tools]


def test_write_work_item_mcp_input_shape_contains_contract_fields() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())
    write_tool = next(tool for tool in tools if tool.name == "write_work_item")

    properties = write_tool.inputSchema["properties"]
    assert {"project_key", "work_item"} <= set(properties)
    assert {"project_key", "work_item"} <= set(write_tool.inputSchema["required"])


def test_write_work_item_mcp_success_returns_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = copy_basic_project_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    server = mcp_server.build_mcp_server()

    result = asyncio.run(
        _call_mcp_tool(
            server,
            "write_work_item",
            {"project_key": PS_PROJECT_KEY, "work_item": valid_material_work_item()},
        )
    )

    assert_work_item_appended(
        result, work_item_ref="W0001", uncovered=[("S0001", "T0003"), ("S0002", "T0001")]
    )


def test_write_work_item_mcp_invalid_result_matches_api_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_workspace = copy_basic_project_workspace(tmp_path / "api")
    mcp_workspace = copy_basic_project_workspace(tmp_path / "mcp")
    invalid = work_item_with_value(("kind",), "material")
    api_result = call_write_work_item_api(workspace_path=api_workspace, work_item=invalid)
    monkeypatch.chdir(mcp_workspace)
    server = mcp_server.build_mcp_server()

    mcp_result = asyncio.run(
        _call_mcp_tool(
            server,
            "write_work_item",
            {"project_key": PS_PROJECT_KEY, "work_item": invalid},
        )
    )

    assert mcp_result == work_item_result_to_dict(api_result)
    assert_work_item_invalid(mcp_result, path="work_item.kind")


def test_write_work_item_uses_workspace_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = copy_basic_project_workspace(tmp_path / "ws")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROMPT_DIARY_WORKSPACE", str(workspace))

    result = mcp_server.write_work_item(PS_PROJECT_KEY, valid_material_work_item())

    assert work_item_result_to_dict(result)["status"] == "appended"


def test_read_session_lines_is_registered_by_mcp_server() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())

    assert "read_session_lines" in [tool.name for tool in tools]


def test_read_session_lines_mcp_input_shape_contains_contract_fields() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())
    read_tool = next(tool for tool in tools if tool.name == "read_session_lines")

    properties = read_tool.inputSchema["properties"]
    assert {"project_key", "session_ref", "start_line", "end_line", "mode"} <= set(properties)
    assert {"project_key", "session_ref", "start_line", "end_line"} <= set(
        read_tool.inputSchema["required"]
    )
    assert "mode" not in read_tool.inputSchema["required"]


def test_read_session_lines_mode_description_warns_about_large_raw_output() -> None:
    server = mcp_server.build_mcp_server()
    tools = asyncio.run(server.list_tools())
    read_tool = next(tool for tool in tools if tool.name == "read_session_lines")

    description = read_tool.inputSchema["properties"]["mode"]["description"]
    assert "large" in description
    assert "raw" in description


def test_read_session_lines_mcp_compact_success_returns_compact_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    server = mcp_server.build_mcp_server()

    result = asyncio.run(
        _call_mcp_tool(
            server,
            "read_session_lines",
            {
                "project_key": READER_PROJECT_KEY,
                "session_ref": READER_SESSION_REF,
                "start_line": 4,
                "end_line": 6,
            },
        )
    )

    assert result == read_result_to_dict(
        call_read_session_lines(workspace_path=workspace, start_line=4, end_line=6)
    )
    assert result["status"] == "ok"
    assert result["mode"] == "compact"
    records = result["records"]
    assert [record["line"] for record in records] == [4, 5, 6]
    assert records[0]["record_type"] == "response_item:function_call"
    assert all("raw_sha256" in record for record in records)


def test_read_session_lines_mcp_full_success_returns_raw_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = copy_session_reader_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    server = mcp_server.build_mcp_server()

    result = asyncio.run(
        _call_mcp_tool(
            server,
            "read_session_lines",
            {
                "project_key": READER_PROJECT_KEY,
                "session_ref": READER_SESSION_REF,
                "start_line": 6,
                "end_line": 6,
                "mode": "full",
            },
        )
    )

    assert result == read_result_to_dict(
        call_read_session_lines(workspace_path=workspace, start_line=6, end_line=6, mode="full")
    )
    assert result["mode"] == "full"
    assert result["records"][0]["raw_line"].startswith('{"payload"')


def test_read_session_lines_mcp_invalid_result_matches_api_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_workspace = copy_session_reader_workspace(tmp_path / "api")
    mcp_workspace = copy_session_reader_workspace(tmp_path / "mcp")
    api_result = call_read_session_lines(
        workspace_path=api_workspace,
        session_ref="S9999",
        start_line=1,
        end_line=1,
    )
    monkeypatch.chdir(mcp_workspace)
    server = mcp_server.build_mcp_server()

    mcp_result = asyncio.run(
        _call_mcp_tool(
            server,
            "read_session_lines",
            {
                "project_key": READER_PROJECT_KEY,
                "session_ref": "S9999",
                "start_line": 1,
                "end_line": 1,
            },
        )
    )

    assert mcp_result == read_result_to_dict(api_result)
    assert mcp_result["status"] == "invalid"
    assert mcp_result["errors"][0]["field"] == "session_ref"


def test_read_session_lines_uses_workspace_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = copy_session_reader_workspace(tmp_path / "ws")
    monkeypatch.chdir(tmp_path)  # cwd is deliberately NOT the workspace
    monkeypatch.setenv("PROMPT_DIARY_WORKSPACE", str(workspace))

    result = mcp_server.read_session_lines(READER_PROJECT_KEY, READER_SESSION_REF, 6, 6)

    assert read_result_to_dict(result)["status"] == "ok"


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
