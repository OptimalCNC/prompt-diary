from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from prompt_diary.agent import AgentTurnResult
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.agent_retry import (
    AgentArtifactStatus,
    AgentRetryPolicy,
    run_agent_turn_with_resume,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


@dataclass
class _ScriptedRunner:
    steps: list[Callable[[], None]]
    prompts: list[str] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    close_calls: int = 0
    turn_step: Callable[[], Awaitable[None]] | None = None
    close_step: Callable[[], Awaitable[None]] | None = None

    async def aclose(self) -> None:
        self.close_calls += 1
        self.events.append("close")
        if self.close_step is not None:
            await self.close_step()

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del output_schema
        assert self.close_calls == 0
        self.prompts.append(prompt)
        self.timeouts.append(timeout_seconds)
        self.events.append(f"turn:{prompt}")
        step = self.steps.pop(0)
        step()
        if self.turn_step is not None:
            await self.turn_step()
        return AgentTurnResult(assistant_text="ok", events=())


def _policy(
    *,
    max_no_progress_attempts: int = 3,
    initial_backoff_seconds: float = 0.0,
    max_backoff_seconds: float = 0.0,
    turn_timeout_seconds: float = 0.5,
) -> AgentRetryPolicy:
    return AgentRetryPolicy(
        max_no_progress_attempts=max_no_progress_attempts,
        initial_backoff_seconds=initial_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
        turn_timeout_seconds=turn_timeout_seconds,
    )


def test_retries_transient_turn_failure_on_same_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete = False
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def fail_without_artifact() -> None:
        raise RuntimeError(_transport_dropped_message())

    def complete_artifact() -> None:
        nonlocal complete
        complete = True

    monkeypatch.setattr("prompt_diary.generate.agent_retry.asyncio.sleep", fake_sleep)
    runner = _ScriptedRunner(steps=[fail_without_artifact, complete_artifact])

    async def run() -> None:
        result = await run_agent_turn_with_resume(
            runner=runner,
            initial_prompt="initial",
            resume_prompt=lambda: "resume",
            inspect_artifacts=lambda: AgentArtifactStatus(complete, int(complete)),
            progress_made=lambda before, after: after > before,
            action="while testing retry",
            retry_policy=_policy(initial_backoff_seconds=1.0, max_backoff_seconds=60.0),
        )

        assert result.ok
        assert result.attempts == 2

    asyncio.run(run())

    assert runner.prompts == ["initial", "resume"]
    assert runner.timeouts == [0.5, 0.5]
    assert sleeps == [1.0]
    assert runner.events == ["turn:initial", "turn:resume", "close"]
    assert runner.close_calls == 1


def test_stops_when_failed_turn_completed_the_artifact() -> None:
    complete = False

    def write_then_fail() -> None:
        nonlocal complete
        complete = True
        raise RuntimeError(_lost_response_message())

    runner = _ScriptedRunner(steps=[write_then_fail])

    async def run() -> None:
        result = await run_agent_turn_with_resume(
            runner=runner,
            initial_prompt="initial",
            resume_prompt=lambda: "resume",
            inspect_artifacts=lambda: AgentArtifactStatus(complete, int(complete)),
            progress_made=lambda before, after: after > before,
            action="while testing completed failure",
            retry_policy=_policy(),
        )

        assert result.ok
        assert result.attempts == 1

    asyncio.run(run())

    assert runner.prompts == ["initial"]
    assert runner.events == ["turn:initial", "close"]
    assert runner.close_calls == 1


def test_fails_after_three_consecutive_no_progress_attempts() -> None:
    runner = _ScriptedRunner(steps=[lambda: None, lambda: None, lambda: None])

    async def run() -> None:
        result = await run_agent_turn_with_resume(
            runner=runner,
            initial_prompt="initial",
            resume_prompt=lambda: "resume",
            inspect_artifacts=lambda: AgentArtifactStatus(complete=False, progress_marker=0),
            progress_made=lambda before, after: after > before,
            action="while testing no progress",
            retry_policy=_policy(),
        )

        assert not result.ok
        assert result.attempts == 3
        assert result.errors == (
            "agent made no progress while testing no progress after 3 consecutive attempt(s)",
        )

    asyncio.run(run())

    assert runner.prompts == ["initial", "resume", "resume"]
    assert runner.close_calls == 1


def test_failure_message_includes_last_turn_error() -> None:
    def fail() -> None:
        raise RuntimeError(_still_down_message())

    runner = _ScriptedRunner(steps=[fail])

    async def run() -> None:
        result = await run_agent_turn_with_resume(
            runner=runner,
            initial_prompt="initial",
            resume_prompt=lambda: "resume",
            inspect_artifacts=lambda: AgentArtifactStatus(complete=False, progress_marker=0),
            progress_made=lambda before, after: after > before,
            action="while testing error detail",
            retry_policy=_policy(max_no_progress_attempts=1),
        )

        assert result.errors == (
            "agent made no progress while testing error detail after 1 consecutive attempt(s); "
            "last agent turn error: RuntimeError: still down",
        )

    asyncio.run(run())


def test_returns_without_turn_when_artifact_is_already_complete() -> None:
    runner = _ScriptedRunner(steps=[])

    async def run() -> None:
        result = await run_agent_turn_with_resume(
            runner=runner,
            initial_prompt="initial",
            resume_prompt=lambda: "resume",
            inspect_artifacts=lambda: AgentArtifactStatus(complete=True, progress_marker=1),
            progress_made=lambda before, after: after > before,
            action="while testing pre-complete",
            retry_policy=_policy(),
        )

        assert result.ok
        assert result.attempts == 0

    asyncio.run(run())

    assert runner.prompts == []
    assert runner.events == ["close"]
    assert runner.close_calls == 1


@pytest.mark.parametrize("after_turn", [False, True])
def test_closes_runner_when_artifact_inspection_fails(*, after_turn: bool) -> None:
    failure = ValueError("invalid artifact")
    inspections = 0
    runner = _ScriptedRunner(steps=[lambda: None])

    def inspect() -> AgentArtifactStatus[int]:
        nonlocal inspections
        inspections += 1
        if inspections == (2 if after_turn else 1):
            raise failure
        return AgentArtifactStatus(complete=False, progress_marker=0)

    async def run() -> None:
        with pytest.raises(ValueError, match="invalid artifact") as caught:
            await run_agent_turn_with_resume(
                runner=runner,
                initial_prompt="initial",
                resume_prompt=lambda: "resume",
                inspect_artifacts=inspect,
                progress_made=lambda before, after: after > before,
                action="while testing inspection failure",
                retry_policy=_policy(),
            )
        assert caught.value is failure

    asyncio.run(run())
    assert runner.close_calls == 1
    assert runner.prompts == (["initial"] if after_turn else [])


@pytest.mark.parametrize("complete", [False, True])
def test_close_failure_is_visible_and_preserves_retry_failure(*, complete: bool) -> None:
    failure = RuntimeError("unload failed")
    artifact_complete = False

    def write_artifact() -> None:
        nonlocal artifact_complete
        artifact_complete = complete

    async def fail_close() -> None:
        raise failure

    runner = _ScriptedRunner(steps=[write_artifact], close_step=fail_close)

    async def run() -> None:
        with pytest.raises(PromptDiaryError, match="failed to close agent conversation") as caught:
            await run_agent_turn_with_resume(
                runner=runner,
                initial_prompt="initial",
                resume_prompt=lambda: "resume",
                inspect_artifacts=lambda: AgentArtifactStatus(
                    artifact_complete, int(artifact_complete)
                ),
                progress_made=lambda before, after: after > before,
                action="while testing failed cleanup",
                retry_policy=_policy(max_no_progress_attempts=1),
            )
        assert caught.value.__cause__ is failure
        assert "RuntimeError: unload failed" in str(caught.value)
        if not complete:
            assert "agent made no progress while testing failed cleanup" in str(caught.value)

    asyncio.run(run())
    assert runner.close_calls == 1


def test_successful_assignment_waits_for_close_before_returning() -> None:
    async def run() -> None:
        complete = False
        close_started = asyncio.Event()
        release_close = asyncio.Event()

        def write_artifact() -> None:
            nonlocal complete
            complete = True

        async def block_close() -> None:
            close_started.set()
            await release_close.wait()

        runner = _ScriptedRunner(steps=[write_artifact], close_step=block_close)
        operation = asyncio.create_task(
            run_agent_turn_with_resume(
                runner=runner,
                initial_prompt="initial",
                resume_prompt=lambda: "resume",
                inspect_artifacts=lambda: AgentArtifactStatus(complete, int(complete)),
                progress_made=lambda before, after: after > before,
                action="while testing release ordering",
                retry_policy=_policy(),
            )
        )
        await asyncio.wait_for(close_started.wait(), 1)
        assert complete
        assert not operation.done()
        release_close.set()
        result = await asyncio.wait_for(operation, 1)
        assert result.ok
        assert runner.events == ["turn:initial", "close"]
        assert runner.close_calls == 1

    asyncio.run(run())


def test_close_failure_preserves_originating_inspection_exception() -> None:
    failure = ValueError("invalid artifact")
    close_failure = RuntimeError("unload failed")

    def inspect() -> AgentArtifactStatus[int]:
        raise failure

    async def fail_close() -> None:
        raise close_failure

    runner = _ScriptedRunner(steps=[], close_step=fail_close)

    async def run() -> None:
        with pytest.raises(ValueError, match="invalid artifact") as caught:
            await run_agent_turn_with_resume(
                runner=runner,
                initial_prompt="initial",
                resume_prompt=lambda: "resume",
                inspect_artifacts=inspect,
                progress_made=lambda before, after: after > before,
                action="while testing inspection and cleanup failures",
                retry_policy=_policy(),
            )
        assert caught.value is failure
        assert caught.value.__cause__ is close_failure

    asyncio.run(run())
    assert runner.close_calls == 1


@pytest.mark.parametrize("cancel_during_turn", [False, True])
def test_repeated_cancellation_finishes_close_before_returning(*, cancel_during_turn: bool) -> None:
    async def run() -> None:
        turn_started = asyncio.Event()
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        close_finished = asyncio.Event()

        async def block_turn() -> None:
            turn_started.set()
            await asyncio.Event().wait()

        async def block_close() -> None:
            close_started.set()
            await release_close.wait()
            close_finished.set()

        runner = _ScriptedRunner(
            steps=[lambda: None],
            turn_step=block_turn if cancel_during_turn else None,
            close_step=block_close,
        )
        operation = asyncio.create_task(
            run_agent_turn_with_resume(
                runner=runner,
                initial_prompt="initial",
                resume_prompt=lambda: "resume",
                inspect_artifacts=lambda: AgentArtifactStatus(
                    complete=not cancel_during_turn, progress_marker=0
                ),
                progress_made=lambda before, after: after > before,
                action="while testing cancelled assignment",
                retry_policy=_policy(),
            )
        )
        if cancel_during_turn:
            await asyncio.wait_for(turn_started.wait(), 1)
            operation.cancel("original cancellation")
        await asyncio.wait_for(close_started.wait(), 1)
        operation.cancel("cleanup cancellation")
        await asyncio.sleep(0)
        operation.cancel("repeated cleanup cancellation")
        await asyncio.sleep(0)
        assert not operation.done()
        assert not close_finished.is_set()
        release_close.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(operation, 1)
        expected_message = "original cancellation" if cancel_during_turn else "cleanup cancellation"
        assert str(caught.value) == expected_message
        assert close_finished.is_set()
        assert runner.close_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("cancel_during_turn", [False, True])
def test_cancellation_preserves_cleanup_failure_as_its_cause(*, cancel_during_turn: bool) -> None:
    async def run() -> None:
        turn_started = asyncio.Event()
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        failure = RuntimeError("unload failed")

        async def block_turn() -> None:
            turn_started.set()
            await asyncio.Event().wait()

        async def fail_close() -> None:
            close_started.set()
            await release_close.wait()
            raise failure

        runner = _ScriptedRunner(
            steps=[lambda: None],
            turn_step=block_turn if cancel_during_turn else None,
            close_step=fail_close,
        )
        operation = asyncio.create_task(
            run_agent_turn_with_resume(
                runner=runner,
                initial_prompt="initial",
                resume_prompt=lambda: "resume",
                inspect_artifacts=lambda: AgentArtifactStatus(
                    complete=not cancel_during_turn, progress_marker=0
                ),
                progress_made=lambda before, after: after > before,
                action="while testing cancelled cleanup failure",
                retry_policy=_policy(),
            )
        )
        if cancel_during_turn:
            await asyncio.wait_for(turn_started.wait(), 1)
            operation.cancel("assignment cancellation")
        await asyncio.wait_for(close_started.wait(), 1)
        if not cancel_during_turn:
            operation.cancel("cleanup cancellation")
        await asyncio.sleep(0)
        release_close.set()
        with pytest.raises(asyncio.CancelledError, match="cancellation") as caught:
            await asyncio.wait_for(operation, 1)
        assert caught.value.__cause__ is failure
        assert runner.close_calls == 1

    asyncio.run(run())


def _transport_dropped_message() -> str:
    return "transport dropped"


def _lost_response_message() -> str:
    return "lost response"


def _still_down_message() -> str:
    return "still down"
