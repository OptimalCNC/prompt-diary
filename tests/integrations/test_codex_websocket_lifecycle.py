"""Real bundled Codex runtime against a loopback Responses WebSocket server.

These tests run in the default suite without credentials or model traffic. The server keeps
connections open until Codex disconnects, and closure assertions run before server teardown.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

import msgspec
import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from prompt_diary.agent import AgentConfig
from prompt_diary.generate.agent_retry import (
    AgentArtifactStatus,
    AgentRetryPolicy,
    AgentRetryResult,
    run_agent_turn_with_resume,
)
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig
from prompt_diary.mcp.codex_config import codex_clean_startup_overrides
from tests.support.codex import SDK_PROBE_SETTINGS

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from websockets.asyncio.server import ServerConnection

    from prompt_diary.agent import AgentRunner, AgentTurnResult
    from prompt_diary.models import JsonObject


class _ResponseCreate(msgspec.Struct):
    type: Literal["response.create"]
    generate: bool = True


@dataclass
class _ObservedSocket:
    outcome: Literal["complete", "hold", "fail"]
    opened: asyncio.Event = field(default_factory=asyncio.Event)
    requested: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    requests: int = 0

    async def wait_closed(self) -> None:
        await asyncio.wait_for(self.closed.wait(), timeout=5)


@dataclass
class _Upstream:
    url: str = ""
    sockets: list[_ObservedSocket] = field(default_factory=list)
    expected: deque[_ObservedSocket] = field(default_factory=deque)
    unexpected_connections: int = 0
    handler_errors: list[Exception] = field(default_factory=list)
    response_count: int = 0

    def expect(self, outcome: Literal["complete", "hold", "fail"] = "complete") -> _ObservedSocket:
        observed = _ObservedSocket(outcome)
        self.sockets.append(observed)
        self.expected.append(observed)
        return observed

    @property
    def live(self) -> list[_ObservedSocket]:
        return [item for item in self.sockets if item.opened.is_set() and not item.closed.is_set()]

    async def handle(self, connection: ServerConnection) -> None:
        if not self.expected:
            self.unexpected_connections += 1
            return
        observed = self.expected.popleft()
        observed.opened.set()
        try:
            async for message in connection:
                request = msgspec.json.decode(message, type=_ResponseCreate)
                self.response_count += 1
                response_id = f"resp_{self.response_count}"
                await connection.send(
                    json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": response_id, "status": "in_progress", "output": []},
                        }
                    )
                )
                if not request.generate:
                    await _complete_response(connection, response_id, warmup=True)
                    continue
                observed.requests += 1
                observed.requested.set()
                if observed.outcome == "hold":
                    # Keep reading so client EOF is observable even without a terminal response.
                    continue
                if observed.outcome == "fail":
                    await connection.send(
                        json.dumps(
                            {
                                "type": "response.failed",
                                "response": {
                                    "id": response_id,
                                    "status": "failed",
                                    "error": {
                                        "type": "invalid_request_error",
                                        "code": "invalid_prompt",
                                        "message": "Synthetic terminal failure",
                                    },
                                },
                            }
                        )
                    )
                    continue
                await _complete_response(connection, response_id, warmup=False)
        except ConnectionClosed:
            # Codex may drop TCP without a WebSocket close frame. Both release the connection.
            pass
        except Exception as error:
            self.handler_errors.append(error)
            raise
        finally:
            observed.closed.set()


async def _complete_response(
    connection: ServerConnection, response_id: str, *, warmup: bool
) -> None:
    item: JsonObject = {
        "id": f"message_{response_id}",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "PONG", "annotations": []}],
    }
    if not warmup:
        await connection.send(
            json.dumps({"type": "response.output_item.done", "output_index": 0, "item": item})
        )
    await connection.send(
        json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "status": "completed",
                    "output": [] if warmup else [item],
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                },
            }
        )
    )


@asynccontextmanager
async def _serve_upstream() -> AsyncGenerator[_Upstream]:
    upstream = _Upstream()
    # No server idle timeout or heartbeat may close the sockets on Codex's behalf.
    async with serve(
        upstream.handle, "127.0.0.1", 0, ping_interval=None, close_timeout=1
    ) as server:
        port = cast("tuple[str, int]", server.sockets[0].getsockname())[1]
        upstream.url = f"http://127.0.0.1:{port}/v1"
        yield upstream
        assert upstream.unexpected_connections == 0
        assert upstream.handler_errors == []


def _factory(tmp_path: Path, upstream: _Upstream) -> CodexAgentSessionFactory:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    return CodexAgentSessionFactory(
        CodexBackendConfig(
            # Omit codex_bin to use the SDK's pinned bundled runtime, independent of PATH.
            mcp_config_overrides=(
                *codex_clean_startup_overrides(codex_home),
                'model_provider="websocket_test"',
                "model_providers.websocket_test={"
                'name="Local WebSocket test",'
                f'base_url="{upstream.url}",'
                'wire_api="responses",supports_websockets=true,requires_openai_auth=false,'
                'env_key="PROMPT_DIARY_TEST_API_KEY"}',
                "analytics.enabled=false",
            ),
            env_overrides={
                "CODEX_HOME": str(codex_home),
                "PROMPT_DIARY_TEST_API_KEY": "local-test-token",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
            },
        )
    )


def _agent_config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        working_directory=tmp_path,
        model=SDK_PROBE_SETTINGS.model,
        reasoning_effort=SDK_PROBE_SETTINGS.reasoning_effort,
        approval_mode="deny_all",
        sandbox="read-only",
    )


@dataclass
class _RecordingRunner:
    """Model a caller's completed artifact using results from the real runner."""

    inner: AgentRunner
    replies: int = 0

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        result = await self.inner.turn(
            prompt, timeout_seconds=timeout_seconds, output_schema=output_schema
        )
        assert result.assistant_text == "PONG"
        self.replies += 1
        return result

    async def aclose(self) -> None:
        await self.inner.aclose()


async def _assignment(
    factory: CodexAgentSessionFactory,
    tmp_path: Path,
    *,
    required_replies: int = 1,
    timeout_seconds: float = 30,
) -> AgentRetryResult:
    runner = _RecordingRunner(await factory.runner(_agent_config(tmp_path)))
    return await run_agent_turn_with_resume(
        runner=runner,
        initial_prompt="Reply PONG.",
        resume_prompt=lambda: "Reply PONG again.",
        inspect_artifacts=lambda: AgentArtifactStatus(
            complete=runner.replies >= required_replies, progress_marker=runner.replies
        ),
        progress_made=lambda before, after: after > before,
        action="while testing WebSocket closure",
        retry_policy=AgentRetryPolicy(
            max_no_progress_attempts=1,
            initial_backoff_seconds=0,
            turn_timeout_seconds=timeout_seconds,
        ),
    )


def test_completed_assignments_close_websockets_while_another_turn_runs(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _serve_upstream() as upstream, _factory(tmp_path, upstream) as factory:
            held = upstream.expect("hold")
            active = asyncio.create_task(_assignment(factory, tmp_path))
            try:
                await asyncio.wait_for(held.requested.wait(), timeout=10)
                for index in range(12):
                    observed = upstream.expect()
                    required_replies = 2 if index == 0 else 1
                    result = await _assignment(factory, tmp_path, required_replies=required_replies)
                    assert result.ok
                    assert result.attempts == required_replies
                    assert observed.requests == required_replies
                    await observed.wait_closed()
                    assert upstream.live == [held]
                    assert not active.done()
            finally:
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            await held.wait_closed()
            assert upstream.live == []

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))


def test_concurrent_assignments_close_all_websockets_before_backend_exit(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _serve_upstream() as upstream, _factory(tmp_path, upstream) as factory:
            for _ in range(2):
                sockets = [upstream.expect() for _ in range(6)]
                results = await asyncio.gather(*(_assignment(factory, tmp_path) for _ in sockets))
                assert all(result.ok for result in results)
                await asyncio.gather(*(observed.wait_closed() for observed in sockets))
                assert all(observed.requests == 1 for observed in sockets)
                assert upstream.live == []

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))


@pytest.mark.parametrize("outcome", ["fail", "hold"])
def test_failed_or_timed_out_assignment_closes_websocket(
    tmp_path: Path, outcome: Literal["fail", "hold"]
) -> None:
    async def exercise() -> None:
        async with _serve_upstream() as upstream, _factory(tmp_path, upstream) as factory:
            observed = upstream.expect(outcome)
            result = await _assignment(factory, tmp_path, timeout_seconds=3)
            assert not result.ok
            assert result.attempts == 1
            assert observed.requests == 1
            expected_error = "Synthetic terminal failure" if outcome == "fail" else "timed out"
            assert expected_error in result.errors[0]
            await observed.wait_closed()
            assert upstream.live == []
            # Cleanup must leave the backend usable for the next assignment.
            following = upstream.expect()
            assert (await _assignment(factory, tmp_path)).ok
            await following.wait_closed()

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))


def test_cancelled_assignment_closes_websocket_before_backend_exit(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _serve_upstream() as upstream, _factory(tmp_path, upstream) as factory:
            observed = upstream.expect("hold")
            assignment = asyncio.create_task(_assignment(factory, tmp_path))
            try:
                await asyncio.wait_for(observed.requested.wait(), timeout=10)
                assignment.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await assignment
                await observed.wait_closed()
                assert upstream.live == []
                following = upstream.expect()
                assert (await _assignment(factory, tmp_path)).ok
                await following.wait_closed()
            finally:
                assignment.cancel()
                await asyncio.gather(assignment, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))


def test_backend_exit_closes_retained_conversation_websocket(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _serve_upstream() as upstream:
            observed = upstream.expect()
            async with _factory(tmp_path, upstream) as factory:
                runner = await factory.runner(_agent_config(tmp_path))
                result = await runner.turn("Reply PONG.", timeout_seconds=30)
                assert result.assistant_text == "PONG"
                assert upstream.live == [observed]
            # Keep the server running so only backend shutdown can account for the disconnect.
            await observed.wait_closed()
            assert upstream.live == []

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))
