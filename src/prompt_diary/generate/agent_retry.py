"""Artifact-aware retry helper for agent-backed generation turns."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from prompt_diary.agent import AgentRunner


TProgress = TypeVar("TProgress")


@dataclass(frozen=True)
class AgentRetryPolicy:
    """Retry limits for one artifact-producing agent assignment."""

    max_no_progress_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    turn_timeout_seconds: float = 600.0


DEFAULT_AGENT_RETRY_POLICY = AgentRetryPolicy()


@dataclass(frozen=True)
class AgentArtifactStatus(Generic[TProgress]):
    """Durable artifact state after an agent turn attempt."""

    complete: bool
    progress_marker: TProgress


@dataclass(frozen=True)
class AgentRetryResult:
    """Result of retrying one agent assignment against durable artifacts."""

    attempts: int
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether the artifact reached the assignment's completion state."""
        return not self.errors


async def run_agent_turn_with_resume(
    *,
    runner: AgentRunner,
    initial_prompt: str,
    resume_prompt: Callable[[], str],
    inspect_artifacts: Callable[[], AgentArtifactStatus[TProgress]],
    progress_made: Callable[[TProgress, TProgress], bool],
    action: str,
    retry_policy: AgentRetryPolicy = DEFAULT_AGENT_RETRY_POLICY,
    output_schema: Mapping[str, object] | None = None,
) -> AgentRetryResult:
    """Run one artifact-producing agent assignment, resuming on the same runner."""

    previous = inspect_artifacts()
    if previous.complete:
        return AgentRetryResult(attempts=0)

    prompt = initial_prompt
    attempts = 0
    no_progress_attempts = 0
    backoff_seconds = retry_policy.initial_backoff_seconds
    last_turn_error: str | None = None

    while True:
        attempts += 1
        turn_error: Exception | None = None
        try:
            await runner.turn(
                prompt,
                timeout_seconds=retry_policy.turn_timeout_seconds,
                output_schema=output_schema,
            )
        except Exception as exc:  # noqa: BLE001
            turn_error = exc

        current = inspect_artifacts()
        if current.complete:
            return AgentRetryResult(attempts=attempts)

        if progress_made(previous.progress_marker, current.progress_marker):
            no_progress_attempts = 0
            backoff_seconds = retry_policy.initial_backoff_seconds
            last_turn_error = None
        else:
            no_progress_attempts += 1
            last_turn_error = _turn_error_message(turn_error)
            if no_progress_attempts >= retry_policy.max_no_progress_attempts:
                return AgentRetryResult(
                    attempts=attempts,
                    errors=(
                        _no_progress_message(
                            action=action,
                            attempts=no_progress_attempts,
                            last_turn_error=last_turn_error,
                        ),
                    ),
                )
            if backoff_seconds > 0:
                await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, retry_policy.max_backoff_seconds)

        previous = current
        prompt = resume_prompt()


def _turn_error_message(exc: Exception | None) -> str | None:
    if exc is None:
        return None
    return f"{type(exc).__name__}: {exc}"


def _no_progress_message(*, action: str, attempts: int, last_turn_error: str | None) -> str:
    message = f"agent made no progress {action} after {attempts} consecutive attempt(s)"
    if last_turn_error is None:
        return message
    return f"{message}; last agent turn error: {last_turn_error}"
