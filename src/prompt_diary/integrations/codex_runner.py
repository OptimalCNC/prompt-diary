# coverage: ignore file
"""Async wrapper for the optional OpenAI Codex Python SDK."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeGuard, cast

from prompt_diary.agent import AgentTurnEvent, AgentTurnResult
from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from prompt_diary.agent import AgentConfig, AgentRunner
    from prompt_diary.models import JsonObject


class CodexRunnerError(PromptDiaryError):
    """Raised when the Codex SDK runner cannot execute a requested operation."""


def _empty_env_overrides() -> Mapping[str, str]:
    return {}


@dataclass(frozen=True)
class CodexBackendConfig:
    """Backend-level Codex configuration shared by compatible runners."""

    mcp_config_overrides: tuple[str, ...] = ()
    codex_bin: Path | None = None
    env_overrides: Mapping[str, str] = field(default_factory=_empty_env_overrides)


class _CodexConfigFactory(Protocol):
    def __call__(
        self,
        *,
        codex_bin: str | None,
        config_overrides: tuple[str, ...],
        env: dict[str, str] | None,
    ) -> object: ...


class _AsyncCodexFactory(Protocol):
    def __call__(self, *, config: object) -> _AsyncCodexContext: ...


class _CodexSdkModule(Protocol):
    CodexConfig: _CodexConfigFactory
    AsyncCodex: _AsyncCodexFactory


class _AsyncCodexContext(Protocol):
    async def __aenter__(self) -> _AsyncCodex: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> object: ...


class _AsyncCodex(Protocol):
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
        config: JsonObject | None = None,
    ) -> _AsyncThread: ...


class _AsyncThread(Protocol):
    async def run(
        self,
        prompt: str,
        *,
        output_schema: Mapping[str, object] | None = None,
    ) -> object: ...


class _StringEnumFactory(Protocol):
    def __call__(self, value: str) -> object: ...


class _ModelDump(Protocol):
    def __call__(self, *, mode: str, exclude_none: bool) -> object: ...


class CodexBackend:
    """Async context manager for a Codex SDK app-server process."""

    def __init__(self, config: CodexBackendConfig) -> None:
        self.config = config
        self._sdk_module: _CodexSdkModule | None = None
        self._context: _AsyncCodexContext | None = None
        self._codex: _AsyncCodex | None = None

    async def __aenter__(self) -> CodexBackend:
        """Start and return the SDK backend."""
        sdk_module = _load_openai_codex()
        codex_config = sdk_module.CodexConfig(
            codex_bin=str(self.config.codex_bin) if self.config.codex_bin is not None else None,
            config_overrides=self.config.mcp_config_overrides,
            env=dict(self.config.env_overrides) or None,
        )
        context = sdk_module.AsyncCodex(config=codex_config)
        self._sdk_module = sdk_module
        self._context = context
        self._codex = await context.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the SDK backend."""
        context = self._context
        self._codex = None
        self._context = None
        self._sdk_module = None
        if context is not None:
            await context.__aexit__(exc_type, exc, traceback)

    @property
    def codex(self) -> _AsyncCodex:
        """Return the active SDK backend, or fail if the backend is not started."""
        if self._codex is None:
            raise CodexRunnerError(_backend_not_started_message())
        return self._codex

    @property
    def sdk_module(self) -> _CodexSdkModule:
        """Return the imported SDK module for enum coercion."""
        if self._sdk_module is None:
            raise CodexRunnerError(_backend_not_started_message())
        return self._sdk_module


class CodexAgentRunner:
    """Owns one Codex SDK conversation thread."""

    def __init__(self, backend: CodexBackend, config: AgentConfig) -> None:
        self.backend = backend
        self.config = config
        self._thread: _AsyncThread | None = None
        self._turn_running = False

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        """Run one prompt turn in the conversation."""
        if timeout_seconds <= 0:
            raise ValueError(_non_positive_timeout_message())
        if self._turn_running:
            raise CodexRunnerError(_concurrent_turn_message())

        self._turn_running = True
        try:
            thread = await self._ensure_thread_started()
            try:
                result = await asyncio.wait_for(
                    thread.run(prompt, output_schema=output_schema),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(_turn_timeout_message(timeout_seconds)) from exc
            return _agent_turn_result(result)
        finally:
            self._turn_running = False

    async def _ensure_thread_started(self) -> _AsyncThread:
        if self._thread is not None:
            return self._thread

        sdk_module = self.backend.sdk_module
        thread_config = _thread_config(self.config)
        approval_mode = _coerce_sdk_enum(
            sdk_module,
            enum_name="ApprovalMode",
            value=self.config.approval_mode,
        )
        if approval_mode is None:
            self._thread = await self.backend.codex.thread_start(
                cwd=str(self.config.working_directory),
                model=self.config.model,
                model_provider=self.config.model_provider,
                sandbox=_coerce_sdk_enum(
                    sdk_module,
                    enum_name="Sandbox",
                    value=self.config.sandbox,
                ),
                base_instructions=self.config.base_instructions,
                developer_instructions=self.config.developer_instructions,
                personality=_coerce_sdk_enum(
                    sdk_module,
                    enum_name="Personality",
                    value=self.config.personality,
                ),
                config=thread_config,
            )
            return self._thread

        self._thread = await self.backend.codex.thread_start(
            cwd=str(self.config.working_directory),
            model=self.config.model,
            model_provider=self.config.model_provider,
            approval_mode=approval_mode,
            sandbox=_coerce_sdk_enum(
                sdk_module,
                enum_name="Sandbox",
                value=self.config.sandbox,
            ),
            base_instructions=self.config.base_instructions,
            developer_instructions=self.config.developer_instructions,
            personality=_coerce_sdk_enum(
                sdk_module,
                enum_name="Personality",
                value=self.config.personality,
            ),
            config=thread_config,
        )
        return self._thread


class CodexAgentSessionFactory:
    """Own one shared Codex backend and mint a fresh conversation per call."""

    def __init__(self, backend_config: CodexBackendConfig) -> None:
        self._backend_config = backend_config
        self._stack: AsyncExitStack | None = None
        self._backend: CodexBackend | None = None

    async def __aenter__(self) -> CodexAgentSessionFactory:
        """Start the shared backend."""
        stack = AsyncExitStack()
        await stack.__aenter__()
        self._backend = await stack.enter_async_context(CodexBackend(self._backend_config))
        self._stack = stack
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Stop the shared backend."""
        stack = self._stack
        self._stack = None
        self._backend = None
        if stack is None:
            return None
        return await stack.__aexit__(exc_type, exc, traceback)

    async def runner(self, config: AgentConfig) -> AgentRunner:
        """Return a fresh conversation bound to the shared backend."""
        if self._backend is None:
            raise CodexRunnerError(_backend_not_started_message())
        return CodexAgentRunner(self._backend, config)


def _load_openai_codex() -> _CodexSdkModule:
    try:
        module = importlib.import_module("openai_codex")
    except ModuleNotFoundError as exc:
        raise CodexRunnerError(_codex_sdk_missing_message()) from exc
    return cast("_CodexSdkModule", module)


def _thread_config(config: AgentConfig) -> JsonObject | None:
    if config.reasoning_effort is None:
        return None
    return {"model_reasoning_effort": config.reasoning_effort}


def _coerce_sdk_enum(
    sdk_module: object,
    *,
    enum_name: str,
    value: str | None,
) -> object | None:
    if value is None:
        return None
    enum_type = getattr(sdk_module, enum_name, None)
    if enum_type is None:
        return value
    if callable(enum_type):
        try:
            return cast("_StringEnumFactory", enum_type)(value)
        except (TypeError, ValueError):
            pass
    enum_value = getattr(enum_type, value.replace("-", "_"), None)
    if enum_value is not None:
        return enum_value
    return value


def _agent_turn_result(result: object) -> AgentTurnResult:
    return AgentTurnResult(
        assistant_text=_string_field(result, "final_response") or "",
        events=tuple(_agent_turn_event(item) for item in _sequence_field(result, "items")),
    )


def _agent_turn_event(item: object) -> AgentTurnEvent:
    unwrapped = _unwrap_root(item)
    kind = (
        _string_field(unwrapped, "type")
        or _string_field(unwrapped, "kind")
        or type(unwrapped).__name__
    )
    return AgentTurnEvent(
        kind=kind,
        summary=_event_summary(unwrapped, kind),
        metadata=_metadata(unwrapped),
    )


def _event_summary(item: object, kind: str) -> str:
    for field_name in ("summary", "text", "message", "name", "command"):
        value = _field(item, field_name)
        if isinstance(value, str) and value:
            return value
        if _is_sequence(value):
            return " ".join(str(part) for part in value)
    return kind


def _metadata(item: object) -> Mapping[str, object]:
    if isinstance(item, dict):
        return dict(cast("Mapping[str, object]", item))

    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        dumped = cast("_ModelDump", model_dump)(mode="json", exclude_none=True)
        if isinstance(dumped, dict):
            return dict(cast("Mapping[str, object]", dumped))

    return {"repr": repr(item)}


def _unwrap_root(item: object) -> object:
    return _field(item, "root") or item


def _field(item: object, name: str) -> object | None:
    if isinstance(item, dict):
        return cast("Mapping[str, object]", item).get(name)
    return getattr(item, name, None)


def _string_field(item: object, name: str) -> str | None:
    value = _field(item, name)
    if isinstance(value, str) and value:
        return value
    return None


def _sequence_field(item: object, name: str) -> Sequence[object]:
    value = _field(item, name)
    if _is_sequence(value):
        return value
    return ()


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _codex_sdk_missing_message() -> str:
    return (
        "The Codex SDK is not importable. Run `uv sync --prerelease=allow` inside this "
        "project, or reinstall the tool with Codex support: "
        "`uv tool install --force --prerelease=allow prompt-diary`."
    )


def _backend_not_started_message() -> str:
    return "CodexBackend must be entered before running Codex agent turns."


def _non_positive_timeout_message() -> str:
    return "timeout_seconds must be positive."


def _turn_timeout_message(timeout_seconds: float) -> str:
    return f"Codex agent turn timed out after {timeout_seconds:g} seconds."


def _concurrent_turn_message() -> str:
    return "CodexAgentRunner.turn cannot be called concurrently on the same runner."
