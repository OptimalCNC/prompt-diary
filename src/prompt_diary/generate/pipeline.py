"""Artifact-first generation pipeline task planning and scheduling."""

from __future__ import annotations

import asyncio
import time
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal, Protocol, TypeGuard

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.workspace import load_prepared_workspace
from prompt_diary.progress.events import TaskFinished, TaskStarted
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterable, Mapping

    from prompt_diary.generate.workspace import PreparedProject, PreparedWorkspace
    from prompt_diary.progress.reporter import ProgressReporter

TaskKind = Literal["evidence_extraction", "project_synthesis", "daily_synthesis"]
TaskStatus = Literal["success", "failed", "blocked"]

DEFAULT_CONCURRENCY_LIMITS: Mapping[TaskKind, int] = {
    "evidence_extraction": 4,
    "project_synthesis": 2,
    "daily_synthesis": 1,
}


@dataclass(frozen=True)
class ArtifactSpec:
    """Workspace-root-relative durable artifact path."""

    path: PurePosixPath
    description: str

    def __post_init__(self) -> None:
        if self.path.is_absolute() or ".." in self.path.parts:
            raise ValueError(_artifact_path_message(self.path))


@dataclass(frozen=True)
class TaskSpec:
    """One artifact-producing generation phase invocation."""

    task_id: str
    kind: TaskKind
    project_key: str | None = None
    session_ref: str | None = None
    depends_on: tuple[str, ...] = ()
    dependency_failure_blocks: bool = True
    prerequisite_artifacts: tuple[ArtifactSpec, ...] = ()
    output_artifacts: tuple[ArtifactSpec, ...] = ()


@dataclass(frozen=True)
class GenerationPlan:
    """Immutable generation task graph."""

    tasks: tuple[TaskSpec, ...]

    def task_map(self) -> Mapping[str, TaskSpec]:
        """Return tasks keyed by stable task id."""
        result: dict[str, TaskSpec] = {}
        for task in self.tasks:
            if task.task_id in result:
                raise PromptDiaryError(_duplicate_task_id_message(task.task_id))
            result[task.task_id] = task
        return result


@dataclass(frozen=True)
class TaskResult:
    """Result of one generation task invocation."""

    task_id: str
    status: TaskStatus
    output_artifacts: tuple[ArtifactSpec, ...] = ()
    errors: tuple[str, ...] = ()
    message: str = ""

    @property
    def ok(self) -> bool:
        """Return whether the task completed successfully."""
        return self.status == "success"


@dataclass(frozen=True)
class PipelineRunResult:
    """Result of running a generation plan."""

    results: tuple[TaskResult, ...]
    terminal_task_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether the terminal deliverables completed successfully."""
        if not self.terminal_task_ids:
            return self.all_tasks_ok
        return all(
            self.result_for(task_id).status == "success" for task_id in self.terminal_task_ids
        )

    @property
    def all_tasks_ok(self) -> bool:
        """Return whether every scheduled task completed successfully."""
        return all(result.ok for result in self.results)

    def result_for(self, task_id: str) -> TaskResult:
        """Return the result for a task id."""
        for result in self.results:
            if result.task_id == task_id:
                return result
        raise KeyError(task_id)


class PhaseRunner(Protocol):
    """Protocol implemented by phase-specific task runners."""

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter
    ) -> TaskResult:
        """Run one phase invocation and return its result."""
        ...


def evidence_task_id(project_key: str, session_ref: str) -> str:
    """Return the stable task id for one session evidence extraction."""
    return f"evidence:{project_key}:{session_ref}"


def project_synthesis_task_id(project_key: str) -> str:
    """Return the stable task id for one project synthesis."""
    return f"project:{project_key}"


def daily_synthesis_task_id() -> str:
    """Return the stable task id for daily synthesis."""
    return "daily"


def evidence_card_artifact(project_key: str, session_ref: str) -> ArtifactSpec:
    """Return the canonical evidence-card artifact for one indexed session."""
    return ArtifactSpec(
        path=PurePosixPath("projects", project_key, "evidence", f"{session_ref}.json"),
        description="session evidence card",
    )


def project_synthesis_artifact(project_key: str) -> ArtifactSpec:
    """Return the project synthesis artifact for one project."""
    return ArtifactSpec(
        path=PurePosixPath("projects", project_key, "project-synthesis.json"),
        description="project synthesis output",
    )


def daily_report_model_artifact() -> ArtifactSpec:
    """Return the semantic daily report model artifact."""
    return ArtifactSpec(path=PurePosixPath("daily-report.json"), description="daily report model")


def markdown_report_artifact() -> ArtifactSpec:
    """Return the rendered Markdown report artifact."""
    return ArtifactSpec(path=PurePosixPath("report.md"), description="Markdown report")


def build_generation_plan(workspace_path: Path) -> GenerationPlan:
    """Build a project-local fan-in generation plan from a prepared workspace."""
    workspace = load_prepared_workspace(workspace_path)
    tasks: list[TaskSpec] = []
    for project in workspace.projects:
        tasks.extend(_evidence_tasks(project))
        tasks.append(_project_synthesis_task(project))
    tasks.append(_daily_synthesis_task(workspace))
    return GenerationPlan(tasks=tuple(tasks))


async def run_generation_task(
    *,
    workspace_path: Path,
    task: TaskSpec,
    phase_runner: PhaseRunner,
    reporter: ProgressReporter = NULL_REPORTER,
) -> TaskResult:
    """Run one task after checking only its declared durable prerequisites."""
    missing_prerequisites = _missing_artifacts(workspace_path, task.prerequisite_artifacts)
    if missing_prerequisites:
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            errors=tuple(
                f"missing prerequisite artifact: {artifact.path}"
                for artifact in missing_prerequisites
            ),
        )

    try:
        result = await phase_runner.run(workspace_path=workspace_path, task=task, reporter=reporter)
    except PromptDiaryError as exc:
        return TaskResult(task_id=task.task_id, status="failed", errors=(str(exc),))
    except Exception as exc:  # noqa: BLE001
        # Phase runners are the isolation boundary; ordinary runner failures become task results.
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            errors=(f"unexpected phase runner error: {type(exc).__name__}: {exc}",),
        )
    if result.status != "success":
        return result

    missing_outputs = _missing_artifacts(workspace_path, task.output_artifacts)
    if missing_outputs:
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            errors=tuple(
                f"missing output artifact after success: {artifact.path}"
                for artifact in missing_outputs
            ),
        )
    return TaskResult(
        task_id=task.task_id,
        status="success",
        output_artifacts=task.output_artifacts,
        message=result.message,
    )


async def run_generation_task_with_lifecycle(
    *,
    workspace_path: Path,
    task: TaskSpec,
    phase_runner: PhaseRunner,
    reporter: ProgressReporter = NULL_REPORTER,
) -> TaskResult:
    """Run one task while honoring an optional phase-runner lifecycle."""
    async with _phase_runner_lifecycle((phase_runner,)):
        return await run_generation_task(
            workspace_path=workspace_path,
            task=task,
            phase_runner=phase_runner,
            reporter=reporter,
        )


@dataclass(frozen=True)
class GeneratePipelineRunner:
    """Run a generation plan with dependency and per-kind concurrency control."""

    phase_runners: Mapping[TaskKind, PhaseRunner]
    concurrency_limits: Mapping[TaskKind, int] = field(
        default_factory=lambda: DEFAULT_CONCURRENCY_LIMITS
    )
    reporter: ProgressReporter = NULL_REPORTER

    async def run(self, *, workspace_path: Path, plan: GenerationPlan) -> PipelineRunResult:
        """Run all tasks in dependency order."""
        tasks = dict(plan.task_map())
        _validate_plan_dependencies(tasks)
        _validate_phase_runners(tasks.values(), self.phase_runners)
        _validate_concurrency_limits(self.concurrency_limits)

        async with _phase_runner_lifecycle(self.phase_runners.values()):
            return await self._run_tasks(workspace_path=workspace_path, tasks=tasks)

    async def _run_tasks(
        self,
        *,
        workspace_path: Path,
        tasks: Mapping[str, TaskSpec],
    ) -> PipelineRunResult:
        remaining = dict(tasks)
        completed: dict[str, TaskResult] = {}
        results: list[TaskResult] = []
        in_flight: dict[asyncio.Task[TaskResult], str] = {}
        semaphores: dict[TaskKind, asyncio.Semaphore] = {
            kind: asyncio.Semaphore(self.concurrency_limits.get(kind, 1))
            for kind in DEFAULT_CONCURRENCY_LIMITS
        }

        while remaining or in_flight:
            self._block_tasks_with_failed_dependencies(remaining, completed, results)
            self._schedule_ready_tasks(
                workspace_path=workspace_path,
                remaining=remaining,
                completed=completed,
                in_flight=in_flight,
                semaphores=semaphores,
            )
            if not in_flight:
                if remaining:
                    unresolved = ", ".join(sorted(remaining))
                    raise PromptDiaryError(_unresolved_dependencies_message(unresolved))
                break

            done_tasks, _pending_tasks = await asyncio.wait(
                set(in_flight),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for done_task in done_tasks:
                task_id = in_flight.pop(done_task)
                result = done_task.result()
                completed[task_id] = result
                results.append(result)

        return PipelineRunResult(
            results=tuple(results),
            terminal_task_ids=_terminal_task_ids(tasks.values()),
        )

    def _schedule_ready_tasks(
        self,
        *,
        workspace_path: Path,
        remaining: dict[str, TaskSpec],
        completed: Mapping[str, TaskResult],
        in_flight: dict[asyncio.Task[TaskResult], str],
        semaphores: Mapping[TaskKind, asyncio.Semaphore],
    ) -> None:
        ready = [
            task
            for task in remaining.values()
            if all(
                dependency in completed
                and (
                    not task.dependency_failure_blocks or completed[dependency].status == "success"
                )
                for dependency in task.depends_on
            )
        ]
        for task in sorted(ready, key=lambda item: item.task_id):
            del remaining[task.task_id]
            scheduled = asyncio.create_task(
                self._run_limited(
                    workspace_path=workspace_path,
                    task=task,
                    semaphore=semaphores[task.kind],
                )
            )
            in_flight[scheduled] = task.task_id

    def _block_tasks_with_failed_dependencies(
        self,
        remaining: dict[str, TaskSpec],
        completed: dict[str, TaskResult],
        results: list[TaskResult],
    ) -> None:
        while True:
            blocked_ids = [
                task_id
                for task_id, task in remaining.items()
                if task.dependency_failure_blocks
                and any(
                    dependency in completed and completed[dependency].status != "success"
                    for dependency in task.depends_on
                )
            ]
            if not blocked_ids:
                return

            for task_id in sorted(blocked_ids):
                task = remaining.pop(task_id)
                failed_dependencies = tuple(
                    dependency
                    for dependency in task.depends_on
                    if dependency in completed and completed[dependency].status != "success"
                )
                result = TaskResult(
                    task_id=task.task_id,
                    status="blocked",
                    errors=tuple(
                        f"dependency did not complete successfully: {dependency}"
                        for dependency in failed_dependencies
                    ),
                )
                self.reporter.emit(
                    TaskFinished(
                        at=time.monotonic(),
                        kind=task.kind,
                        task_id=task.task_id,
                        project_key=task.project_key,
                        session_ref=task.session_ref,
                        status="blocked",
                        error=result.errors[0] if result.errors else None,
                    )
                )
                completed[task.task_id] = result
                results.append(result)

    async def _run_limited(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        semaphore: asyncio.Semaphore,
    ) -> TaskResult:
        async with semaphore:
            self.reporter.emit(
                TaskStarted(
                    at=time.monotonic(),
                    kind=task.kind,
                    task_id=task.task_id,
                    project_key=task.project_key,
                    session_ref=task.session_ref,
                )
            )
            result = await run_generation_task(
                workspace_path=workspace_path,
                task=task,
                phase_runner=self.phase_runners[task.kind],
                reporter=self.reporter,
            )
            self.reporter.emit(
                TaskFinished(
                    at=time.monotonic(),
                    kind=task.kind,
                    task_id=task.task_id,
                    project_key=task.project_key,
                    session_ref=task.session_ref,
                    status=result.status,
                    error=result.errors[0] if result.errors else None,
                )
            )
            return result


def _evidence_tasks(project: PreparedProject) -> tuple[TaskSpec, ...]:
    return tuple(
        TaskSpec(
            task_id=evidence_task_id(project.project_key, session.session_ref),
            kind="evidence_extraction",
            project_key=project.project_key,
            session_ref=session.session_ref,
            prerequisite_artifacts=(
                ArtifactSpec(PurePosixPath("metadata.json"), "workspace metadata"),
                ArtifactSpec(
                    PurePosixPath("projects", project.project_key, "project.json"),
                    "project metadata",
                ),
                ArtifactSpec(
                    PurePosixPath("projects", project.project_key, "sessions.index.jsonl"),
                    "project session index",
                ),
                ArtifactSpec(
                    PurePosixPath("projects", project.project_key, session.session_path),
                    "copied session transcript",
                ),
            ),
            output_artifacts=(evidence_card_artifact(project.project_key, session.session_ref),),
        )
        for session in project.sessions
    )


def _project_synthesis_task(project: PreparedProject) -> TaskSpec:
    return TaskSpec(
        task_id=project_synthesis_task_id(project.project_key),
        kind="project_synthesis",
        project_key=project.project_key,
        depends_on=tuple(
            evidence_task_id(project.project_key, session.session_ref)
            for session in project.sessions
        ),
        dependency_failure_blocks=False,
        prerequisite_artifacts=(
            ArtifactSpec(PurePosixPath("metadata.json"), "workspace metadata"),
            ArtifactSpec(
                PurePosixPath("projects", project.project_key, "project.json"),
                "project metadata",
            ),
            ArtifactSpec(
                PurePosixPath("projects", project.project_key, "sessions.index.jsonl"),
                "project session index",
            ),
            *(
                evidence_card_artifact(project.project_key, session.session_ref)
                for session in project.sessions
            ),
        ),
        output_artifacts=(project_synthesis_artifact(project.project_key),),
    )


def _daily_synthesis_task(workspace: PreparedWorkspace) -> TaskSpec:
    project_artifacts = tuple(
        project_synthesis_artifact(project.project_key) for project in workspace.projects
    )
    return TaskSpec(
        task_id=daily_synthesis_task_id(),
        kind="daily_synthesis",
        depends_on=tuple(
            project_synthesis_task_id(project.project_key) for project in workspace.projects
        ),
        prerequisite_artifacts=(
            ArtifactSpec(PurePosixPath("metadata.json"), "workspace metadata"),
            *project_artifacts,
        ),
        output_artifacts=(daily_report_model_artifact(), markdown_report_artifact()),
    )


def _missing_artifacts(
    workspace_path: Path,
    artifacts: tuple[ArtifactSpec, ...],
) -> tuple[ArtifactSpec, ...]:
    return tuple(
        artifact for artifact in artifacts if not (workspace_path / artifact.path).exists()
    )


@asynccontextmanager
async def _phase_runner_lifecycle(
    phase_runners: Iterable[PhaseRunner],
) -> AsyncGenerator[None]:
    async with AsyncExitStack() as stack:
        entered_runner_ids: set[int] = set()
        for phase_runner in phase_runners:
            runner_id = id(phase_runner)
            if runner_id in entered_runner_ids:
                continue
            entered_runner_ids.add(runner_id)
            if _is_async_context_manager(phase_runner):
                await stack.enter_async_context(phase_runner)
        yield


def _is_async_context_manager(
    value: object,
) -> TypeGuard[AbstractAsyncContextManager[object]]:
    return hasattr(value, "__aenter__") and hasattr(value, "__aexit__")


def _terminal_task_ids(tasks: Iterable[TaskSpec]) -> tuple[str, ...]:
    task_list = tuple(tasks)
    dependencies = {dependency for task in task_list for dependency in task.depends_on}
    return tuple(task.task_id for task in task_list if task.task_id not in dependencies)


def _validate_plan_dependencies(tasks: Mapping[str, TaskSpec]) -> None:
    for task in tasks.values():
        for dependency in task.depends_on:
            if dependency not in tasks:
                raise PromptDiaryError(_unknown_dependency_message(task.task_id, dependency))


def _validate_phase_runners(
    tasks: Iterable[TaskSpec],
    phase_runners: Mapping[TaskKind, PhaseRunner],
) -> None:
    for task in tasks:
        if task.kind not in phase_runners:
            raise PromptDiaryError(_missing_phase_runner_message(task.kind))


def _validate_concurrency_limits(concurrency_limits: Mapping[TaskKind, int]) -> None:
    for kind, limit in concurrency_limits.items():
        if kind not in DEFAULT_CONCURRENCY_LIMITS:
            raise PromptDiaryError(_unknown_concurrency_kind_message(kind))
        if limit < 1:
            raise PromptDiaryError(_nonpositive_concurrency_limit_message(kind))


def _artifact_path_message(path: PurePosixPath) -> str:
    return f"artifact path must be workspace-relative: {path}"


def _duplicate_task_id_message(task_id: str) -> str:
    return f"duplicate generation task id: {task_id}"


def _unresolved_dependencies_message(unresolved: str) -> str:
    return f"generation plan has unresolved dependencies: {unresolved}"


def _unknown_dependency_message(task_id: str, dependency: str) -> str:
    return f"generation task {task_id} depends on unknown task {dependency}"


def _missing_phase_runner_message(kind: TaskKind) -> str:
    return f"missing phase runner for task kind: {kind}"


def _unknown_concurrency_kind_message(kind: TaskKind) -> str:
    return f"unknown generation task kind in concurrency limits: {kind}"


def _nonpositive_concurrency_limit_message(kind: TaskKind) -> str:
    return f"concurrency limit for {kind} must be positive"
