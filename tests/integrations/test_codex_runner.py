from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import pytest

import prompt_diary.integrations.codex_runner as codex_runner
from prompt_diary.integrations.codex_runner import (
    AgentConfig,
    AgentTurnEvent,
    AgentTurnResult,
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
    CodexRunnerError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass
class FakeTurnResult:
    final_response: str | None
    items: list[object]


class FakeRootItem:
    def __init__(self, root: object) -> None:
        self.root = root


class FakeModelItem:
    type = "model"
    text = "model text"

    def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, object]:
        return {"type": self.type, "text": self.text, "mode": mode, "exclude_none": exclude_none}


class FakeAppServerConfig:
    def __init__(
        self,
        *,
        codex_bin: str | None,
        config_overrides: tuple[str, ...],
        env: dict[str, str] | None,
    ) -> None:
        self.codex_bin = codex_bin
        self.config_overrides = config_overrides
        self.env = env


class FakeThread:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, object]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.delay_seconds = 0.0

    async def run(
        self,
        prompt: str,
        *,
        output_schema: Mapping[str, object] | None = None,
    ) -> FakeTurnResult:
        self.run_calls.append({"prompt": prompt, "output_schema": output_schema})
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return FakeTurnResult(
            final_response=f"response to {prompt}",
            items=[
                {"type": "agent_message", "text": "hello"},
                FakeRootItem({"kind": "tool", "summary": "called tool"}),
                FakeModelItem(),
            ],
        )


class FakeAsyncCodex:
    instances: ClassVar[list[FakeAsyncCodex]] = []
    next_thread: ClassVar[FakeThread | None] = None

    def __init__(self, *, config: object) -> None:
        self.config = config
        self.entered = False
        self.exited = False
        self.thread_start_calls: list[dict[str, object]] = []
        self.thread = FakeAsyncCodex.next_thread or FakeThread()
        FakeAsyncCodex.next_thread = None
        FakeAsyncCodex.instances.append(self)

    async def __aenter__(self) -> FakeAsyncCodex:
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        self.exited = True

    async def thread_start(
        self,
        *,
        cwd: str,
        model: str | None = None,
        model_provider: str | None = None,
        approval_mode: object | None = None,
        sandbox: object | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        personality: object | None = None,
        config: dict[str, object] | None = None,
    ) -> FakeThread:
        self.thread_start_calls.append(
            {
                "cwd": cwd,
                "model": model,
                "model_provider": model_provider,
                "approval_mode": approval_mode,
                "sandbox": sandbox,
                "base_instructions": base_instructions,
                "developer_instructions": developer_instructions,
                "personality": personality,
                "config": config,
            }
        )
        return self.thread


class FakeSdkModule:
    AppServerConfig = FakeAppServerConfig
    AsyncCodex = FakeAsyncCodex


def test_turn_result_contracts_accept_structured_events(tmp_path: Path) -> None:
    event = AgentTurnEvent(kind="tool", summary="called ping", metadata={"tool": "ping"})
    result = AgentTurnResult(assistant_text="done", events=(event,))

    assert AgentConfig(working_directory=tmp_path).working_directory == tmp_path
    assert result.events[0].metadata == {"tool": "ping"}


def test_backend_enter_exit_and_runner_config_pass_through(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_sdk(monkeypatch)
    codex_bin = tmp_path / "codex"
    output_schema = {"type": "object"}

    async def exercise() -> None:
        async with CodexBackend(
            CodexBackendConfig(
                mcp_config_overrides=("mcp.prompt_diary={}",),
                codex_bin=codex_bin,
                env_overrides={"PROMPT_DIARY": "1"},
            )
        ) as backend:
            runner = CodexAgentRunner(
                backend,
                AgentConfig(
                    working_directory=tmp_path,
                    model="codex-test",
                    model_provider="openai",
                    reasoning_effort="low",
                    approval_mode="deny_all",
                    sandbox="workspace-write",
                    base_instructions="base",
                    developer_instructions="developer",
                    personality="concise",
                ),
            )
            result = await runner.turn("Generate the report.", output_schema=output_schema)
            assert result.assistant_text == "response to Generate the report."
            assert [event.kind for event in result.events] == ["agent_message", "tool", "model"]
            assert result.events[0].summary == "hello"
            assert result.events[1].metadata == {"kind": "tool", "summary": "called tool"}
            assert result.events[2].metadata["mode"] == "json"

        fake_codex = FakeAsyncCodex.instances[0]
        assert fake_codex.exited

    asyncio.run(exercise())

    fake_codex = FakeAsyncCodex.instances[0]
    app_config = fake_codex.config
    assert isinstance(app_config, FakeAppServerConfig)
    assert app_config.codex_bin == str(codex_bin)
    assert app_config.config_overrides == ("mcp.prompt_diary={}",)
    assert app_config.env == {"PROMPT_DIARY": "1"}
    assert fake_codex.thread_start_calls == [
        {
            "cwd": str(tmp_path),
            "model": "codex-test",
            "model_provider": "openai",
            "approval_mode": "deny_all",
            "sandbox": "workspace-write",
            "base_instructions": "base",
            "developer_instructions": "developer",
            "personality": "concise",
            "config": {"model_reasoning_effort": "low"},
        }
    ]
    assert fake_codex.thread.run_calls == [
        {"prompt": "Generate the report.", "output_schema": output_schema}
    ]
    assert fake_codex.thread.run_calls[0]["output_schema"] is output_schema


def test_runner_reuses_thread_after_first_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_sdk(monkeypatch)

    async def exercise() -> None:
        async with CodexBackend(CodexBackendConfig()) as backend:
            runner = CodexAgentRunner(backend, AgentConfig(working_directory=tmp_path))
            await runner.turn("first")
            await runner.turn("second")

    asyncio.run(exercise())

    fake_codex = FakeAsyncCodex.instances[0]
    assert len(fake_codex.thread_start_calls) == 1
    assert [call["prompt"] for call in fake_codex.thread.run_calls] == ["first", "second"]


def test_runner_requires_positive_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_sdk(monkeypatch)

    async def exercise() -> None:
        async with CodexBackend(CodexBackendConfig()) as backend:
            runner = CodexAgentRunner(backend, AgentConfig(working_directory=tmp_path))
            with pytest.raises(ValueError, match="timeout_seconds must be positive"):
                await runner.turn("prompt", timeout_seconds=0)

    asyncio.run(exercise())


def test_runner_rejects_concurrent_turns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_thread = FakeThread()
    fake_thread.block = True
    FakeAsyncCodex.next_thread = fake_thread
    _patch_sdk(monkeypatch)

    async def exercise() -> None:
        async with CodexBackend(CodexBackendConfig()) as backend:
            runner = CodexAgentRunner(backend, AgentConfig(working_directory=tmp_path))
            task = asyncio.create_task(runner.turn("first"))
            await fake_thread.started.wait()
            with pytest.raises(CodexRunnerError, match="cannot be called concurrently"):
                await runner.turn("second")
            fake_thread.release.set()
            await task

    asyncio.run(exercise())


def test_runner_applies_asyncio_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_thread = FakeThread()
    fake_thread.delay_seconds = 10.0
    FakeAsyncCodex.next_thread = fake_thread
    _patch_sdk(monkeypatch)

    async def exercise() -> None:
        async with CodexBackend(CodexBackendConfig()) as backend:
            runner = CodexAgentRunner(backend, AgentConfig(working_directory=tmp_path))
            with pytest.raises(TimeoutError):
                await runner.turn("slow", timeout_seconds=0.01)

    asyncio.run(exercise())


def test_backend_reports_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_missing_sdk(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(codex_runner.importlib, "import_module", raise_missing_sdk)

    async def exercise() -> None:
        async with CodexBackend(CodexBackendConfig()):
            pass

    with pytest.raises(CodexRunnerError, match="Codex SDK is not importable"):
        asyncio.run(exercise())


def test_runner_requires_started_backend(tmp_path: Path) -> None:
    runner = CodexAgentRunner(
        CodexBackend(CodexBackendConfig()),
        AgentConfig(working_directory=tmp_path),
    )

    async def exercise() -> None:
        await runner.turn("prompt")

    with pytest.raises(CodexRunnerError, match="must be entered"):
        asyncio.run(exercise())


def _patch_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncCodex.instances = []

    def fake_import_module(name: str) -> object:
        if name == "openai_codex":
            return FakeSdkModule()
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        codex_runner.importlib,
        "import_module",
        fake_import_module,
    )
