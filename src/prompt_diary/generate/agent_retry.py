"""Artifact-aware retry helper for agent-backed generation turns."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from prompt_diary.errors import PromptDiaryError

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
    """Consume one assignment's runner, preserving it across retries and then closing it."""

    result: AgentRetryResult | None = None
    primary_error: BaseException | None = None
    try:
        result = await _run_agent_turn_with_resume(
            runner=runner,
            initial_prompt=initial_prompt,
            resume_prompt=resume_prompt,
            inspect_artifacts=inspect_artifacts,
            progress_made=progress_made,
            action=action,
            retry_policy=retry_policy,
            output_schema=output_schema,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    else:
        return result
    finally:
        try:
            await _finish_runner_close(runner)
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise primary_error from cleanup_error
            if isinstance(cleanup_error, asyncio.CancelledError):
                raise
            raise PromptDiaryError(
                _cleanup_failure_message(result, cleanup_error)
            ) from cleanup_error


async def _finish_runner_close(runner: AgentRunner) -> None:
    """Retain cleanup through repeated caller cancellation, then propagate it."""
    task = asyncio.create_task(runner.aclose())
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:  # noqa: PERF203 — cancellation must finish cleanup.
            if cancellation is None:
                cancellation = exc
        except Exception:  # noqa: BLE001 — retrieve the cleanup error from the retained task below.
            break
    try:
        task.result()
    except BaseException as cleanup_error:
        if cancellation is not None:
            raise cancellation from cleanup_error
        raise
    if cancellation is not None:
        raise cancellation


async def _run_agent_turn_with_resume(
    *,
    runner: AgentRunner,
    initial_prompt: str,
    resume_prompt: Callable[[], str],
    inspect_artifacts: Callable[[], AgentArtifactStatus[TProgress]],
    progress_made: Callable[[TProgress, TProgress], bool],
    action: str,
    retry_policy: AgentRetryPolicy,
    output_schema: Mapping[str, object] | None,
) -> AgentRetryResult:
    """Run an assignment while its caller keeps the conversation alive."""

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


def _cleanup_failure_message(result: AgentRetryResult | None, error: BaseException) -> str:
    message = f"failed to close agent conversation: {type(error).__name__}: {error}"
    if result is not None and not result.ok:
        return f"{'; '.join(result.errors)}; {message}"
    return message


def _no_progress_message(*, action: str, attempts: int, last_turn_error: str | None) -> str:
    message = f"agent made no progress {action} after {attempts} consecutive attempt(s)"
    if last_turn_error is None:
        return message
    return f"{message}; last agent turn error: {last_turn_error}"
