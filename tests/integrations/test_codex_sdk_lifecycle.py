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
from prompt_diary.integrations.codex_runner import (
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
)

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

        def observed_next() -> Notification:
            self.waiting.add(turn_id)
            self.loop.call_soon_threadsafe(self.consumer_started.set)
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
                assert transport.router.closed == ["turn-1", "turn-2"]
                assert transport.router.waiting == set()
                assert transport.close_calls == 0
            assert transport.close_calls == 1
        finally:
            transport.router.release_waiters()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))
