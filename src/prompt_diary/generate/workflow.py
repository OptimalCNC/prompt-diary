"""Generation workflow API built on the artifact-first pipeline."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.pipeline import (
    GeneratePipelineRunner,
    PhaseRunner,
    PipelineRunResult,
    TaskKind,
    TaskResult,
    TaskSpec,
    build_generation_plan,
    daily_synthesis_task_id,
    evidence_task_id,
    project_synthesis_task_id,
    run_generation_task_with_lifecycle,
)
from prompt_diary.progress.events import RunFinished, RunStarted, TaskFinished, TaskStarted
from prompt_diary.progress.reporter import NULL_REPORTER, ProgressReporter

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import GenerationPlan

PhaseName = Literal["evidence", "project", "daily"]


def _kind_totals(plan: GenerationPlan) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for task in plan.tasks:
        counts[task.kind] = counts.get(task.kind, 0) + 1
    return tuple(sorted(counts.items()))


def _run_finished(result: PipelineRunResult) -> RunFinished:
    succeeded = sum(1 for item in result.results if item.status == "success")
    failed = sum(1 for item in result.results if item.status == "failed")
    blocked = sum(1 for item in result.results if item.status == "blocked")
    return RunFinished(at=time.monotonic(), succeeded=succeeded, failed=failed, blocked=blocked)


@dataclass(frozen=True)
class GeneratePipelineWorkflowResult:
    """Result from running the full generation pipeline."""

    workspace_path: Path
    daily_report_path: Path
    report_path: Path
    pipeline_result: PipelineRunResult
    messages: tuple[str, ...]


@dataclass(frozen=True)
class GeneratePhaseWorkflowResult:
    """Result from running one generation phase task."""

    workspace_path: Path
    task: TaskSpec
    task_result: TaskResult
    messages: tuple[str, ...]


@dataclass(frozen=True)
class GenerateWorkspaceWorkflow:
    """Run generation workflows against one prepared workspace."""

    build_agent_factory: Callable[[Path], AgentSessionFactory]
    build_phase_runners: Callable[[AgentSessionFactory], Mapping[TaskKind, PhaseRunner]]

    def run_pipeline(
        self,
        *,
        workspace_path: Path,
        messages: tuple[str, ...] = (),
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> GeneratePipelineWorkflowResult:
        """Run the full generation pipeline from a prepared workspace."""
        _require_workspace(workspace_path)
        factory = self.build_agent_factory(workspace_path)
        phase_runners = self.build_phase_runners(factory)
        plan = build_generation_plan(workspace_path)
        reporter.emit(
            RunStarted(
                at=time.monotonic(),
                label=workspace_path.name,
                kind_totals=_kind_totals(plan),
            )
        )
        pipeline_result = asyncio.run(
            self._run_plan(
                workspace_path=workspace_path,
                plan=plan,
                factory=factory,
                phase_runners=phase_runners,
                reporter=reporter,
            )
        )
        reporter.emit(_run_finished(pipeline_result))
        if not pipeline_result.ok:
            raise PromptDiaryError(_pipeline_failed_message(pipeline_result))

        report_path = workspace_path / "report.md"
        daily_report_path = workspace_path / "daily-report.json"
        return GeneratePipelineWorkflowResult(
            workspace_path=workspace_path,
            daily_report_path=daily_report_path,
            report_path=report_path,
            pipeline_result=pipeline_result,
            messages=(
                *messages,
                f"Wrote daily report model {daily_report_path}.",
                f"Wrote rendered report {report_path}.",
            ),
        )

    def run_phase(
        self,
        *,
        workspace_path: Path,
        phase: PhaseName,
        project_key: str | None = None,
        session_ref: str | None = None,
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> GeneratePhaseWorkflowResult:
        """Run one generation phase task from a prepared workspace."""
        _require_workspace(workspace_path)
        task = _select_task(
            workspace_path=workspace_path,
            phase=phase,
            project_key=project_key,
            session_ref=session_ref,
        )
        factory = self.build_agent_factory(workspace_path)
        phase_runners = self.build_phase_runners(factory)
        reporter.emit(
            RunStarted(
                at=time.monotonic(),
                label=workspace_path.name,
                kind_totals=((task.kind, 1),),
            )
        )
        reporter.emit(
            TaskStarted(
                at=time.monotonic(),
                kind=task.kind,
                task_id=task.task_id,
                project_key=task.project_key,
                session_ref=task.session_ref,
            )
        )
        task_result = asyncio.run(
            self._run_task(
                workspace_path=workspace_path,
                task=task,
                factory=factory,
                phase_runners=phase_runners,
                reporter=reporter,
            )
        )
        reporter.emit(
            TaskFinished(
                at=time.monotonic(),
                kind=task.kind,
                task_id=task.task_id,
                project_key=task.project_key,
                session_ref=task.session_ref,
                status=task_result.status,
                error=task_result.errors[0] if task_result.errors else None,
            )
        )
        reporter.emit(
            RunFinished(
                at=time.monotonic(),
                succeeded=1 if task_result.status == "success" else 0,
                failed=1 if task_result.status == "failed" else 0,
                blocked=1 if task_result.status == "blocked" else 0,
            )
        )
        if not task_result.ok:
            raise PromptDiaryError(_task_failed_message(task_result))
        return GeneratePhaseWorkflowResult(
            workspace_path=workspace_path,
            task=task,
            task_result=task_result,
            messages=(f"Completed generation task {task.task_id}.",),
        )

    async def _run_plan(
        self,
        *,
        workspace_path: Path,
        plan: GenerationPlan,
        factory: AgentSessionFactory,
        phase_runners: Mapping[TaskKind, PhaseRunner],
        reporter: ProgressReporter,
    ) -> PipelineRunResult:
        runner = GeneratePipelineRunner(phase_runners=phase_runners, reporter=reporter)
        async with factory:
            return await runner.run(workspace_path=workspace_path, plan=plan)

    async def _run_task(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        factory: AgentSessionFactory,
        phase_runners: Mapping[TaskKind, PhaseRunner],
        reporter: ProgressReporter,
    ) -> TaskResult:
        phase_runner = phase_runners[task.kind]
        async with factory:
            return await run_generation_task_with_lifecycle(
                workspace_path=workspace_path,
                task=task,
                phase_runner=phase_runner,
                reporter=reporter,
            )


def _select_task(
    *,
    workspace_path: Path,
    phase: PhaseName,
    project_key: str | None,
    session_ref: str | None,
) -> TaskSpec:
    plan = build_generation_plan(workspace_path)
    tasks = plan.task_map()
    task_id = _task_id_for_phase(phase=phase, project_key=project_key, session_ref=session_ref)
    task = tasks.get(task_id)
    if task is None:
        raise PromptDiaryError(_missing_task_message(task_id))
    return task


def _require_workspace(workspace_path: Path) -> None:
    if not workspace_path.exists():
        raise PromptDiaryError(_missing_workspace_message(workspace_path))


def _task_id_for_phase(
    *,
    phase: PhaseName,
    project_key: str | None,
    session_ref: str | None,
) -> str:
    if phase == "evidence":
        if project_key is None or session_ref is None:
            raise PromptDiaryError(_evidence_scope_message())
        return evidence_task_id(project_key, session_ref)
    if phase == "project":
        if project_key is None:
            raise PromptDiaryError(_project_scope_message())
        return project_synthesis_task_id(project_key)
    return daily_synthesis_task_id()


def _pipeline_failed_message(result: PipelineRunResult) -> str:
    failed = [task_result for task_result in result.results if not task_result.ok]
    details = "\n".join(
        f"- {task_result.task_id}: {'; '.join(task_result.errors) or task_result.status}"
        for task_result in failed
    )
    return f"Generation pipeline failed:\n{details}"


def _task_failed_message(result: TaskResult) -> str:
    details = "\n".join(f"- {error}" for error in result.errors)
    if details:
        return f"Generation task {result.task_id} failed:\n{details}"
    return f"Generation task {result.task_id} failed with status {result.status}."


def _missing_workspace_message(workspace_path: Path) -> str:
    return f"prepared workspace is missing: {workspace_path}"


def _missing_task_message(task_id: str) -> str:
    return f"generation task is not present in the prepared workspace: {task_id}"


def _evidence_scope_message() -> str:
    return "evidence phase requires --project-key and --session-ref"


def _project_scope_message() -> str:
    return "project phase requires --project-key"
