"""Pinned-SDK lifecycle interoperability with in-memory RPC and notification queues."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import openai_codex
import pytest
from openai_codex import AsyncCodex, AsyncThread
from openai_codex._message_router import MessageRouter, NotificationQueueItem
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
from prompt_diary.integrations.codex_runner import (
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
)

if TYPE_CHECKING:
    from pathlib import Path
    from queue import Queue


class _ObservedRouter(MessageRouter):
    """Observe real SDK queue consumption and retain emergency handles for bounded test cleanup."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self.loop = loop
        self.consumer_started = asyncio.Event()
        self.waiting: set[str] = set()
        self.consumed_terminal: list[str] = []
        self.unregistered: list[str] = []
        self.queues: list[Queue[NotificationQueueItem]] = []

    def register_turn(self, turn_id: str) -> None:
        super().register_turn(turn_id)
        with self._lock:
            turn_queue = self._turn_notifications[turn_id]
            if turn_queue not in self.queues:
                self.queues.append(turn_queue)

    def next_turn_notification(self, turn_id: str) -> Notification:
        self.waiting.add(turn_id)
        self.loop.call_soon_threadsafe(self.consumer_started.set)
        try:
            notification = super().next_turn_notification(turn_id)
            if isinstance(notification.payload, TurnCompletedNotification):
                self.consumed_terminal.append(notification.payload.turn.id)
            return notification
        finally:
            self.waiting.remove(turn_id)

    def unregister_turn(self, turn_id: str) -> None:
        self.unregistered.append(turn_id)
        super().unregister_turn(turn_id)

    def release_waiters(self) -> None:
        # A regression must not leave asyncio.run waiting forever for an orphaned to_thread worker.
        for turn_queue in self.queues:
            turn_queue.put(RuntimeError("test transport closed"))


class _MemoryTransport(CodexClient):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self.router = _ObservedRouter(loop)
        self._router = self.router
        self.loop = loop
        self.interrupt_received = asyncio.Event()
        self.requests: list[str] = []
        self.close_calls = 0

    def _write_message(self, payload: JsonObject) -> None:
        method = payload["method"]
        assert isinstance(method, str)
        self.requests.append(method)
        params = payload["params"]
        assert isinstance(params, dict)
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

    def complete_followup(self) -> None:
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
                        completed_at_ms=0, thread_id="thread-1", turn_id="turn-2", item=item
                    ),
                )
            )
        self.router.route_notification(
            Notification(
                method="turn/completed",
                payload=TurnCompletedNotification(
                    thread_id="thread-1",
                    turn=Turn(id="turn-2", items=items, status=TurnStatus.completed),
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
        transport = _MemoryTransport(asyncio.get_running_loop())
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
                assert transport.router.unregistered == []

                transport.complete_interruption()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=2)

                assert task.done()
                assert transport.requests == ["turn/start", "turn/interrupt"]
                assert transport.router.consumed_terminal == ["turn-1"]
                assert transport.router.unregistered == ["turn-1"]
                assert transport.router.waiting == set()
                assert transport.close_calls == 0
                assert backend.codex is codex

                transport.router.consumer_started.clear()
                task = asyncio.create_task(runner.turn("Follow-up task.", timeout_seconds=2))
                await asyncio.wait_for(transport.router.consumer_started.wait(), timeout=2)
                transport.complete_followup()
                result = await asyncio.wait_for(asyncio.shield(task), timeout=2)

                assert result.assistant_text == "Final answer."
                assert tuple(event.summary for event in result.events) == (
                    "Final answer.",
                    "Later commentary.",
                    "Later message without a phase.",
                )
                assert transport.requests == ["turn/start", "turn/interrupt", "turn/start"]
                assert transport.router.consumed_terminal == ["turn-1", "turn-2"]
                assert transport.router.unregistered == ["turn-1", "turn-2"]
                assert transport.router.waiting == set()
                assert transport.close_calls == 0
            assert transport.close_calls == 1
        finally:
            transport.router.release_waiters()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))
