# coverage: ignore file
"""Async wrapper for the optional OpenAI Codex Python SDK."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal, Protocol, TypeGuard, TypeVar, cast

from prompt_diary.agent import AgentTurnEvent, AgentTurnResult
from prompt_diary.errors import PromptDiaryError
from prompt_diary.source_records import CODEX_REPORT_ORIGINATOR

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from prompt_diary.agent import AgentConfig, AgentRunner
    from prompt_diary.models import JsonObject


class CodexRunnerError(PromptDiaryError):
    """Raised when the Codex SDK runner cannot execute a requested operation."""


_TURN_CLEANUP_TIMEOUT_SECONDS = 5.0
_T = TypeVar("_T")


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
        client_name: str,
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
    async def turn(
        self,
        prompt: str,
        *,
        output_schema: Mapping[str, object] | None = None,
    ) -> _AsyncTurnHandle: ...


class _AsyncTurnHandle(Protocol):
    id: str

    def stream(self) -> AsyncGenerator[object, None]: ...

    async def interrupt(self) -> object: ...


class _StringEnumFactory(Protocol):
    def __call__(self, value: str) -> object: ...


class _ModelDump(Protocol):
    def __call__(self, *, mode: str, exclude_none: bool) -> object: ...


@dataclass(frozen=True)
class _CompletedTurn:
    """A terminal notification plus the items observed before it."""

    status: Literal["completed", "interrupted", "failed"]
    error: str | None
    result: AgentTurnResult


@dataclass
class _TurnState:
    started: asyncio.Event = field(default_factory=asyncio.Event)
    handle: _AsyncTurnHandle | None = None
    completed: _CompletedTurn | None = None


class CodexBackend:
    """Async context manager for a Codex SDK app-server process."""

    def __init__(self, config: CodexBackendConfig) -> None:
        self.config = config
        self._sdk_module: _CodexSdkModule | None = None
        self._context: _AsyncCodexContext | None = None
        self._codex: _AsyncCodex | None = None
        self._close_task: asyncio.Task[object] | None = None
        self._invalidated = False

    async def __aenter__(self) -> CodexBackend:
        """Start and return the SDK backend."""
        if self._invalidated:
            raise CodexRunnerError(_invalidated_backend_message())
        sdk_module = _load_openai_codex()
        codex_config = sdk_module.CodexConfig(
            codex_bin=str(self.config.codex_bin) if self.config.codex_bin is not None else None,
            config_overrides=self.config.mcp_config_overrides,
            env=dict(self.config.env_overrides) or None,
            client_name=CODEX_REPORT_ORIGINATOR,
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
        close_task = self._start_close(exc_type, exc, traceback)
        if close_task is not None:
            try:
                await _finish_cleanup(asyncio.create_task(_wait_for_cleanup(close_task)))
            except Exception as error:
                self._invalidated = True
                if isinstance(exc, (TimeoutError, asyncio.CancelledError)):
                    raise exc from error
                raise CodexRunnerError(_shutdown_unconfirmed_message()) from error

    async def invalidate(self) -> None:
        """Permanently disable this backend and close its transport."""
        self._invalidated = True
        close_task = self._start_close(None, None, None)
        if close_task is not None:
            await asyncio.shield(close_task)

    def _start_close(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> asyncio.Task[object] | None:
        context = self._context
        self._codex = None
        self._context = None
        self._sdk_module = None
        if context is not None:
            self._close_task = asyncio.create_task(context.__aexit__(exc_type, exc, traceback))
            self._close_task.add_done_callback(_consume_task_exception)
        return self._close_task

    @property
    def codex(self) -> _AsyncCodex:
        """Return the active SDK backend, or fail if the backend is not started."""
        if self._invalidated:
            raise CodexRunnerError(_invalidated_backend_message())
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
            state = _TurnState()
            operation = asyncio.create_task(self._run_turn(thread, state, prompt, output_schema))
            operation.add_done_callback(_consume_task_exception)
            try:
                completed = await asyncio.wait_for(asyncio.shield(operation), timeout_seconds)
            except asyncio.TimeoutError as exc:
                try:
                    await _finish_cleanup(asyncio.create_task(self._stop_turn(state, operation)))
                except Exception as cleanup_error:
                    raise TimeoutError(
                        _timeout_cleanup_failed_message(timeout_seconds)
                    ) from cleanup_error
                raise TimeoutError(_turn_timeout_message(timeout_seconds)) from exc
            except asyncio.CancelledError as exc:
                try:
                    await _finish_cleanup(asyncio.create_task(self._stop_turn(state, operation)))
                finally:
                    raise exc
            except Exception:
                await _finish_cleanup(asyncio.create_task(self._abort_turn(operation)))
                raise
            if completed.status == "failed":
                raise CodexRunnerError(completed.error or "Codex agent turn failed.")
            return completed.result
        finally:
            self._turn_running = False

    async def _run_turn(
        self,
        thread: _AsyncThread,
        state: _TurnState,
        prompt: str,
        output_schema: Mapping[str, object] | None,
    ) -> _CompletedTurn:
        try:
            _ = self.backend.codex  # Cached threads must not lazily restart a stopped backend.
            state.handle = await thread.turn(prompt, output_schema=output_schema)
        finally:
            state.started.set()
        _ = self.backend.codex
        completed = await _read_turn(state.handle)
        state.completed = completed
        return completed

    async def _stop_turn(self, state: _TurnState, operation: asyncio.Task[_CompletedTurn]) -> None:
        stopping = asyncio.create_task(self._interrupt_and_drain(state, operation))
        stopping.add_done_callback(_consume_task_exception)
        try:
            await asyncio.wait_for(asyncio.shield(stopping), _TURN_CLEANUP_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 — uncertain SDK shutdown requires transport invalidation.
            await self._abort_turn(operation, stopping)

    async def _interrupt_and_drain(
        self, state: _TurnState, operation: asyncio.Task[_CompletedTurn]
    ) -> None:
        await state.started.wait()
        if state.handle is not None and state.completed is None:
            try:
                _ = self.backend.codex
                await state.handle.interrupt()
            except Exception:  # noqa: BLE001, S110 — terminal drain settles an interrupt race.
                pass
        await asyncio.shield(operation)

    async def _abort_turn(self, *tasks: asyncio.Task[object]) -> None:
        # Keep SDK consumers registered until transport closure can wake their worker threads.
        closing = asyncio.create_task(self.backend.invalidate())
        closing.add_done_callback(_consume_task_exception)
        try:
            await asyncio.wait_for(asyncio.shield(closing), _TURN_CLEANUP_TIMEOUT_SECONDS)
            await asyncio.wait_for(
                asyncio.shield(asyncio.gather(*tasks, return_exceptions=True)),
                _TURN_CLEANUP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise CodexRunnerError(_shutdown_unconfirmed_message()) from exc

    async def _ensure_thread_started(self) -> _AsyncThread:
        _ = self.backend.codex
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
    overrides: JsonObject = {}
    if config.reasoning_effort is not None:
        overrides["model_reasoning_effort"] = config.reasoning_effort
    if config.mcp_tools:
        # Code-mode models otherwise defer these schemas behind tool discovery.
        overrides["features.code_mode.direct_only_tool_namespaces"] = ["mcp__prompt_diary"]
        overrides["mcp_servers.prompt_diary.enabled_tools"] = list(config.mcp_tools)
    return overrides or None


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


async def _finish_cleanup(task: asyncio.Task[_T]) -> _T:
    """Finish cleanup even when the caller is cancelled again while waiting."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:  # noqa: PERF203 — repeated cancellation must finish cleanup.
            cancelled = True
    if cancelled:
        # Retrieve any error before propagating cancellation so no task is abandoned.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def _wait_for_cleanup(task: asyncio.Task[_T]) -> _T:
    return await asyncio.wait_for(asyncio.shield(task), _TURN_CLEANUP_TIMEOUT_SECONDS)


def _consume_task_exception(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()


async def _read_turn(handle: _AsyncTurnHandle) -> _CompletedTurn:
    items: list[object] = []
    async with aclosing(handle.stream()) as stream:
        async for notification in stream:
            payload = _field(notification, "payload")
            method = _string_field(notification, "method")
            if method == "item/completed" and _string_field(payload, "turn_id") == handle.id:
                item = _field(payload, "item")
                if item is not None:
                    items.append(item)
            elif method == "turn/completed":
                turn = _field(payload, "turn")
                if _string_field(turn, "id") == handle.id:
                    return _completed_turn(turn, items)
    raise CodexRunnerError(_missing_terminal_message())


def _completed_turn(turn: object, items: list[object]) -> _CompletedTurn:
    status = _string_field(turn, "status")
    if status not in ("completed", "interrupted", "failed"):
        raise CodexRunnerError(_unknown_terminal_message())
    return _CompletedTurn(
        status=status,
        error=_string_field(_field(turn, "error"), "message"),
        result=AgentTurnResult(
            assistant_text=_final_assistant_text(items),
            events=tuple(_agent_turn_event(item) for item in items),
        ),
    )


def _final_assistant_text(items: list[object]) -> str:
    fallback: str | None = None
    for item in reversed(items):
        unwrapped = _unwrap_root(item)
        if _string_field(unwrapped, "type") != "agentMessage":
            continue
        phase = _string_field(unwrapped, "phase")
        text = _string_field(unwrapped, "text") or ""
        if phase == "final_answer":
            return text
        if phase is None and fallback is None:
            fallback = text
    return fallback or ""


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
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str) and value:
        return value
    return None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _codex_sdk_missing_message() -> str:
    return (
        "The Codex SDK is not importable. Run `uv sync` inside this "
        "project, or reinstall the tool with Codex support: "
        "`uv tool install --force prompt-diary`."
    )


def _backend_not_started_message() -> str:
    return "CodexBackend must be entered before running Codex agent turns."


def _non_positive_timeout_message() -> str:
    return "timeout_seconds must be positive."


def _turn_timeout_message(timeout_seconds: float) -> str:
    return f"Codex agent turn timed out after {timeout_seconds:g} seconds."


def _concurrent_turn_message() -> str:
    return "CodexAgentRunner.turn cannot be called concurrently on the same runner."


def _invalidated_backend_message() -> str:
    return "Codex backend was stopped because a turn's termination could not be confirmed."


def _shutdown_unconfirmed_message() -> str:
    return "Codex backend is disabled; transport shutdown or turn cleanup could not be confirmed."


def _timeout_cleanup_failed_message(timeout_seconds: float) -> str:
    return f"{_turn_timeout_message(timeout_seconds)} {_shutdown_unconfirmed_message()}"


def _missing_terminal_message() -> str:
    return "Codex turn stream ended without a terminal notification."


def _unknown_terminal_message() -> str:
    return "Codex terminal notification has an unknown turn status."
