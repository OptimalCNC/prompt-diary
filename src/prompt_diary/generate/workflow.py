"""Generation workflow API built on the artifact-first pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.runner import DailySynthesisRunner
from prompt_diary.generate.evidence_extraction.runner import EvidenceExtractionRunner
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
from prompt_diary.generate.project_synthesis.runner import ProjectSynthesisRunner

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

PhaseName = Literal["evidence", "project", "daily"]


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

    phase_runners: Mapping[TaskKind, PhaseRunner]

    def run_pipeline(
        self,
        *,
        workspace_path: Path,
        messages: tuple[str, ...] = (),
    ) -> GeneratePipelineWorkflowResult:
        """Run the full generation pipeline from a prepared workspace."""
        _require_workspace(workspace_path)
        plan = build_generation_plan(workspace_path)
        runner = GeneratePipelineRunner(phase_runners=self.phase_runners)
        pipeline_result = asyncio.run(runner.run(workspace_path=workspace_path, plan=plan))
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
    ) -> GeneratePhaseWorkflowResult:
        """Run one generation phase task from a prepared workspace."""
        _require_workspace(workspace_path)
        task = _select_task(
            workspace_path=workspace_path,
            phase=phase,
            project_key=project_key,
            session_ref=session_ref,
        )
        phase_runner = self.phase_runners[task.kind]
        task_result = asyncio.run(
            run_generation_task_with_lifecycle(
                workspace_path=workspace_path,
                task=task,
                phase_runner=phase_runner,
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


def run_generate_pipeline(
    *,
    workspace_path: Path,
    phase_runners: Mapping[TaskKind, PhaseRunner] | None = None,
    messages: tuple[str, ...] = (),
) -> GeneratePipelineWorkflowResult:
    """Run the full generation pipeline from a prepared workspace."""
    return GenerateWorkspaceWorkflow(
        phase_runners=_phase_runners_or_default(phase_runners)
    ).run_pipeline(
        workspace_path=workspace_path,
        messages=messages,
    )


def run_generate_phase(
    *,
    workspace_path: Path,
    phase: PhaseName,
    project_key: str | None = None,
    session_ref: str | None = None,
    phase_runners: Mapping[TaskKind, PhaseRunner] | None = None,
) -> GeneratePhaseWorkflowResult:
    """Run one generation phase task from a prepared workspace."""
    return GenerateWorkspaceWorkflow(
        phase_runners=_phase_runners_or_default(phase_runners)
    ).run_phase(
        workspace_path=workspace_path,
        phase=phase,
        project_key=project_key,
        session_ref=session_ref,
    )


def default_phase_runners() -> Mapping[TaskKind, PhaseRunner]:
    """Return the default generation phase runners."""
    return {
        "evidence_extraction": EvidenceExtractionRunner(),
        "project_synthesis": ProjectSynthesisRunner(),
        "daily_synthesis": DailySynthesisRunner(),
    }


def _phase_runners_or_default(
    phase_runners: Mapping[TaskKind, PhaseRunner] | None,
) -> Mapping[TaskKind, PhaseRunner]:
    if phase_runners is None:
        return default_phase_runners()
    return phase_runners


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
