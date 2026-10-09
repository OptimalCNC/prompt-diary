"""Pinned-SDK lifecycle interoperability with in-memory RPC and turn subscriptions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import openai_codex
import pytest
from openai_codex import AsyncCodex, AsyncThread
from openai_codex._message_router import MessageRouter
from openai_codex.async_client import AsyncCodexClient
from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ItemCompletedNotification,
    MessagePhase,
    ThreadItem,
    Turn,
    TurnCompletedNotification,
    TurnStatus,
)
from openai_codex.models import JsonObject, Notification

from prompt_diary.agent import AgentConfig
from prompt_diary.generate.agent_retry import (
    AgentArtifactStatus,
    AgentRetryPolicy,
    run_agent_turn_with_resume,
)
from prompt_diary.generate.agent_settings import AgentSettings, load_agent_settings
from prompt_diary.integrations.codex_runner import (
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
)
from tests.support.codex import SDK_PROBE_SETTINGS

if TYPE_CHECKING:
    from pathlib import Path

    from openai_codex._message_router import (
        _TurnSubscription,  # pyright: ignore[reportPrivateUsage]
    )


class _ObservedRouter(MessageRouter):
    """Observe real SDK subscription consumption and release blocked consumers during cleanup."""

    def __init__(self, loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__()
        self.loop = loop
        self.monkeypatch = monkeypatch
        self.consumer_started = asyncio.Event()
        self.consumer_started_turns: asyncio.Queue[str] = asyncio.Queue()
        self.waiting: set[str] = set()
        self.consumed_terminal: list[str] = []
        self.closed: list[str] = []

    def prepare_turn(
        self, turn_id: str, thread_id: str, cursors: dict[str, int], *, for_handle: bool
    ) -> _TurnSubscription | None:
        subscription = super().prepare_turn(turn_id, thread_id, cursors, for_handle=for_handle)
        assert subscription is not None
        next_notification = subscription.next
        close = subscription.close
        first_read = True

        def observed_next() -> Notification:
            nonlocal first_read
            self.waiting.add(turn_id)
            self.loop.call_soon_threadsafe(self.consumer_started.set)
            if first_read:
                first_read = False
                self.loop.call_soon_threadsafe(self.consumer_started_turns.put_nowait, turn_id)
            try:
                notification = next_notification()
                if isinstance(notification.payload, TurnCompletedNotification):
                    self.consumed_terminal.append(notification.payload.turn.id)
                return notification
            finally:
                self.waiting.remove(turn_id)

        def observed_close() -> None:
            self.closed.append(turn_id)
            close()

        self.monkeypatch.setattr(subscription, "next", observed_next)
        self.monkeypatch.setattr(subscription, "close", observed_close)
        return subscription

    def release_waiters(self) -> None:
        # A regression must not leave asyncio.run waiting forever for an orphaned to_thread worker.
        self.fail_all(RuntimeError("test transport closed"))


class _MemoryTransport(CodexClient):
    def __init__(self, loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__()
        self.router = _ObservedRouter(loop, monkeypatch)
        self._router = self.router
        self.loop = loop
        self.interrupt_received = asyncio.Event()
        self.requests: list[str] = []
        self.payloads: list[JsonObject] = []
        self.close_calls = 0

    def _write_message(self, payload: JsonObject) -> None:
        method = payload["method"]
        assert isinstance(method, str)
        self.requests.append(method)
        self.payloads.append(payload)
        params = payload["params"]
        assert isinstance(params, dict)
        if method == "thread/start":
            self.router.route_response(
                {
                    "id": payload["id"],
                    "result": {
                        "model": params["model"],
                        "modelProvider": "openai",
                        "cwd": params["cwd"],
                        "approvalPolicy": params["approvalPolicy"],
                        "approvalsReviewer": params["approvalsReviewer"],
                        "sandbox": {"type": "workspaceWrite"},
                        "thread": {
                            "id": "thread-1",
                            "sessionId": "thread-1",
                            "cliVersion": "0.162.0",
                            "createdAt": 0,
                            "updatedAt": 0,
                            "cwd": params["cwd"],
                            "ephemeral": False,
                            "modelProvider": "openai",
                            "preview": "",
                            "source": "appServer",
                            "status": {"type": "idle"},
                            "turns": [],
                        },
                    },
                }
            )
            return
        assert params["threadId"] == "thread-1"
        result: JsonObject = {}
        if method == "turn/start":
            turn_id = f"turn-{self.requests.count('turn/start')}"
            result = {"turn": {"id": turn_id, "status": "inProgress", "items": []}}
        else:
            assert method == "turn/interrupt"
            assert params["turnId"] == "turn-1"
            self.loop.call_soon_threadsafe(self.interrupt_received.set)
        self.router.route_response({"id": payload["id"], "result": result})

    def complete_interruption(self) -> None:
        self.router.route_notification(
            Notification(
                method="turn/completed",
                payload=TurnCompletedNotification(
                    thread_id="thread-1",
                    turn=Turn(id="turn-1", items=[], status=TurnStatus.interrupted),
                ),
            )
        )

    def complete_turn(self, turn_id: str) -> None:
        items = [
            ThreadItem(
                root=AgentMessageThreadItem(
                    id=f"message-{index}", type="agentMessage", text=text, phase=phase
                )
            )
            for index, (text, phase) in enumerate(
                (
                    ("Final answer.", MessagePhase.final_answer),
                    ("Later commentary.", MessagePhase.commentary),
                    ("Later message without a phase.", None),
                )
            )
        ]
        for item in items:
            self.router.route_notification(
                Notification(
                    method="item/completed",
                    payload=ItemCompletedNotification(
                        completed_at_ms=0, thread_id="thread-1", turn_id=turn_id, item=item
                    ),
                )
            )
        self.router.route_notification(
            Notification(
                method="turn/completed",
                payload=TurnCompletedNotification(
                    thread_id="thread-1",
                    turn=Turn(id=turn_id, items=items, status=TurnStatus.completed),
                ),
            )
        )

    def close(self) -> None:
        self.close_calls += 1
        self.router.release_waiters()


def test_timeout_interrupts_and_drains_real_sdk_turn_before_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        transport = _MemoryTransport(asyncio.get_running_loop(), monkeypatch)
        client = AsyncCodexClient()
        monkeypatch.setattr(client, "_sync", transport)
        codex = AsyncCodex()
        monkeypatch.setattr(codex, "_client", client)
        monkeypatch.setattr(codex, "_initialized", True)

        async def thread_start(**_kwargs: object) -> AsyncThread:
            return AsyncThread(codex, "thread-1")

        def context_factory(*, config: object) -> AsyncCodex:
            del config
            return codex

        monkeypatch.setattr(codex, "thread_start", thread_start)
        monkeypatch.setattr(openai_codex, "AsyncCodex", context_factory)
        task: asyncio.Task[object] | None = None
        try:
            async with CodexBackend(CodexBackendConfig()) as backend:
                runner = CodexAgentRunner(backend, AgentConfig(working_directory=tmp_path))
                task = asyncio.create_task(runner.turn("Synthetic task.", timeout_seconds=0.05))
                await asyncio.wait_for(transport.router.consumer_started.wait(), timeout=2)
                await asyncio.wait_for(transport.interrupt_received.wait(), timeout=2)
                await asyncio.sleep(0)
                assert not task.done(), "interrupt acknowledgement must not release the runner"
                assert transport.router.waiting == {"turn-1"}
                assert transport.router.closed == []

                transport.complete_interruption()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=2)

                assert task.done()
                assert transport.requests == ["turn/start", "turn/interrupt"]
                assert transport.router.consumed_terminal == ["turn-1"]
                assert transport.router.closed == ["turn-1"]
                assert transport.router.waiting == set()
                assert transport.close_calls == 0
                assert backend.codex is codex

                transport.router.consumer_started.clear()
                task = asyncio.create_task(runner.turn("Follow-up task.", timeout_seconds=2))
                await asyncio.wait_for(transport.router.consumer_started.wait(), timeout=2)
                transport.complete_turn("turn-2")
                result = await asyncio.wait_for(asyncio.shield(task), timeout=2)

                assert result.assistant_text == "Final answer."
                assert tuple(event.summary for event in result.events) == (
                    "Final answer.",
                    "Later commentary.",
                    "Later message without a phase.",
                )
                assert transport.requests == ["turn/start", "turn/interrupt", "turn/start"]
                assert transport.router.consumed_terminal == ["turn-1", "turn-2"]
                assert transport.router.closed == ["turn-1", "turn-2"]
                assert transport.router.waiting == set()
                assert transport.close_calls == 0
            assert transport.close_calls == 1
        finally:
            transport.router.release_waiters()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


@pytest.mark.parametrize(
    ("settings", "expected_model", "expected_effort"),
    [
        pytest.param(
            load_agent_settings().evidence_extraction,
            "gpt-6.1-sol",
            "medium",
            id="evidence_extraction",
        ),
        pytest.param(
            load_agent_settings().project_synthesis,
            "gpt-6.1-sol",
            "high",
            id="project_synthesis",
        ),
        pytest.param(
            load_agent_settings().daily_synthesis.project_summary,
            "gpt-6.1-sol",
            "low",
            id="project_summary",
        ),
        pytest.param(
            load_agent_settings().daily_synthesis.report_title,
            "gpt-6-luna",
            "low",
            id="report_title",
        ),
        pytest.param(
            load_agent_settings().daily_synthesis.engagement,
            "gpt-6.1-sol",
            "high",
            id="engagement",
        ),
        pytest.param(
            load_agent_settings().daily_synthesis.team_learning,
            "gpt-6.1-sol",
            "high",
            id="team_learning",
        ),
        pytest.param(SDK_PROBE_SETTINGS, "gpt-6-luna", "low", id="sdk_probe"),
    ],
)
def test_assignment_model_and_effort_reach_real_sdk_and_survive_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: AgentSettings,
    expected_model: str,
    expected_effort: str,
) -> None:
    async def exercise() -> None:
        transport = _MemoryTransport(asyncio.get_running_loop(), monkeypatch)
        client = AsyncCodexClient()
        monkeypatch.setattr(client, "_sync", transport)
        codex = AsyncCodex()
        monkeypatch.setattr(codex, "_client", client)
        monkeypatch.setattr(codex, "_initialized", True)

        def context_factory(*, config: object) -> AsyncCodex:
            del config
            return codex

        monkeypatch.setattr(openai_codex, "AsyncCodex", context_factory)
        operation: asyncio.Task[object] | None = None
        try:
            async with CodexBackend(CodexBackendConfig()) as backend:
                runner = CodexAgentRunner(
                    backend,
                    AgentConfig(
                        working_directory=tmp_path,
                        model=settings.model,
                        reasoning_effort=settings.reasoning_effort,
                        approval_mode="auto_review",
                        sandbox="workspace-write",
                    ),
                )
                operation = asyncio.create_task(
                    run_agent_turn_with_resume(
                        runner=runner,
                        initial_prompt="Initial assignment.",
                        resume_prompt=lambda: "Finish the same assignment.",
                        inspect_artifacts=lambda: AgentArtifactStatus(
                            complete=transport.requests.count("turn/start") == 2,
                            progress_marker=False,
                        ),
                        progress_made=lambda _before, _after: False,
                        action="while testing model selection",
                        retry_policy=AgentRetryPolicy(initial_backoff_seconds=0),
                    )
                )
                for turn_id in ("turn-1", "turn-2"):
                    assert (
                        await asyncio.wait_for(
                            transport.router.consumer_started_turns.get(), timeout=2
                        )
                        == turn_id
                    )
                    transport.complete_turn(turn_id)
                result = await asyncio.wait_for(operation, timeout=2)
                assert result.ok
                assert result.attempts == 2

            assert transport.requests == ["thread/start", "turn/start", "turn/start"]
            params = transport.payloads[0]["params"]
            assert isinstance(params, dict)
            assert params["model"] == expected_model
            assert params["config"] == {"model_reasoning_effort": expected_effort}
            for payload in transport.payloads[1:]:
                turn_params = payload["params"]
                assert isinstance(turn_params, dict)
                assert turn_params["threadId"] == "thread-1"
                assert "model" not in turn_params
                assert "effort" not in turn_params
        finally:
            transport.router.release_waiters()
            if operation is not None:
                await asyncio.gather(operation, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))
