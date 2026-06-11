from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompt_diary.agent import AgentTurnResult
from prompt_diary.generate.agent_retry import (
    AgentArtifactStatus,
    AgentRetryPolicy,
    run_agent_turn_with_resume,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import pytest


@dataclass
class _ScriptedRunner:
    steps: list[Callable[[], None]]
    prompts: list[str] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del output_schema
        self.prompts.append(prompt)
        self.timeouts.append(timeout_seconds)
        step = self.steps.pop(0)
        step()
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


def _transport_dropped_message() -> str:
    return "transport dropped"


def _lost_response_message() -> str:
    return "lost response"


def _still_down_message() -> str:
    return "still down"
