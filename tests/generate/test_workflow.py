from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.pipeline import PhaseRunner, TaskKind, TaskResult, TaskSpec
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow
from prompt_diary.progress.events import RunFinished, RunStarted, TaskFinished, TaskStarted
from prompt_diary.progress.reporter import NULL_REPORTER
from tests.agent_fakes import FakeAgentSessionFactory
from tests.support.progress import RecordingReporter

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentConfig, AgentTurnResult
    from prompt_diary.progress.reporter import ProgressReporter


def _no_agent_turns(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    raise AssertionError(_no_agent_turns_message())


def _no_agent_turns_message() -> str:
    return "workflow tests use file-writing phase runners, not agent turns"


def _workflow(phase_runner: PhaseRunner) -> GenerateWorkspaceWorkflow:
    factory = FakeAgentSessionFactory(script=_no_agent_turns)
    return GenerateWorkspaceWorkflow(
        build_agent_factory=lambda _workspace: factory,
        build_phase_runners=lambda _factory: _all_phase_runners(phase_runner),
    )


def test_generate_workflow_runs_pipeline_with_injected_phase_runners(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    phase_runner = WritingPhaseRunner()
    factory = FakeAgentSessionFactory(script=_no_agent_turns)

    result = GenerateWorkspaceWorkflow(
        build_agent_factory=lambda _workspace: factory,
        build_phase_runners=lambda _factory: _all_phase_runners(phase_runner),
    ).run_pipeline(
        workspace_path=workspace,
        messages=("Reusing existing workspace.",),
    )

    assert result.workspace_path == workspace
    assert result.report_path == workspace / "report.md"
    assert result.daily_report_path == workspace / "daily-report.json"
    assert result.pipeline_result.ok
    assert phase_runner.events == ["daily"]
    assert "Reusing existing workspace." in result.messages
    assert factory.entered == 1
    assert factory.exited == 1


def test_generate_workflow_requires_prepared_workspace(tmp_path: Path) -> None:
    with pytest.raises(PromptDiaryError, match="prepared workspace is missing"):
        _workflow(WritingPhaseRunner()).run_pipeline(
            workspace_path=tmp_path / ".reports" / "work" / "2026-05-12",
        )


def test_generate_workflow_rejects_phase_runner_missing_declared_outputs(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(PromptDiaryError) as exc_info:
        _workflow(NoOutputPhaseRunner()).run_pipeline(workspace_path=workspace)

    assert "Generation pipeline failed:" in str(exc_info.value)
    assert "missing output artifact after success: daily-report.json" in str(exc_info.value)


def test_generate_workflow_treats_evidence_failure_as_gap_when_report_completes(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    _write_project(workspace=workspace, project_key="Project-123", session_ref="S0001")

    result = _workflow(EvidenceGapPhaseRunner()).run_pipeline(workspace_path=workspace)

    assert result.pipeline_result.ok
    assert not result.pipeline_result.all_tasks_ok
    assert result.report_path.exists()
    assert result.daily_report_path.exists()


def test_run_generate_phase_runs_one_task(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    phase_runner = WritingPhaseRunner()
    factory = FakeAgentSessionFactory(script=_no_agent_turns)

    result = GenerateWorkspaceWorkflow(
        build_agent_factory=lambda _workspace: factory,
        build_phase_runners=lambda _factory: _all_phase_runners(phase_runner),
    ).run_phase(
        workspace_path=workspace,
        phase="daily",
    )

    assert result.workspace_path == workspace
    assert result.task.task_id == "daily"
    assert result.task_result.ok
    assert result.messages == ("Completed generation task daily.",)
    assert factory.entered == 1
    assert factory.exited == 1


def test_run_generate_phase_enters_context_managed_runner(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    phase_runner = ContextManagedWritingPhaseRunner()

    result = _workflow(phase_runner).run_phase(
        workspace_path=workspace,
        phase="daily",
    )

    assert result.task_result.ok
    assert phase_runner.entered == 1
    assert phase_runner.exited == 1
    assert phase_runner.events[0] == "enter"
    assert phase_runner.events[-1] == "exit"


def test_run_generate_phase_requires_existing_workspace(tmp_path: Path) -> None:
    with pytest.raises(PromptDiaryError, match="prepared workspace is missing"):
        _workflow(WritingPhaseRunner()).run_phase(
            workspace_path=tmp_path / ".reports" / "work" / "2026-05-12",
            phase="daily",
        )


def test_run_generate_phase_validates_phase_scope(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(PromptDiaryError, match="evidence phase requires"):
        _workflow(WritingPhaseRunner()).run_phase(
            workspace_path=workspace,
            phase="evidence",
        )
    with pytest.raises(PromptDiaryError, match="project phase requires"):
        _workflow(WritingPhaseRunner()).run_phase(
            workspace_path=workspace,
            phase="project",
        )


def test_run_generate_phase_rejects_unknown_task(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(PromptDiaryError, match="generation task is not present"):
        _workflow(WritingPhaseRunner()).run_phase(
            workspace_path=workspace,
            phase="project",
            project_key="Missing-123",
        )


def test_run_generate_phase_project_requires_evidence_cards(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    _write_project(workspace=workspace, project_key="Project-123", session_ref="S0001")

    with pytest.raises(PromptDiaryError) as exc_info:
        _workflow(WritingPhaseRunner()).run_phase(
            workspace_path=workspace,
            phase="project",
            project_key="Project-123",
        )

    assert "Generation task project:Project-123 failed:" in str(exc_info.value)
    assert "missing prerequisite artifact: projects/Project-123/evidence/S0001.json" in str(
        exc_info.value
    )


def test_run_generate_phase_reports_prerequisite_failures(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    _write_project(workspace=workspace, project_key="Project-123", session_ref="S0001")
    (workspace / "projects" / "Project-123" / "sessions" / "codex" / "session.jsonl").unlink()

    with pytest.raises(PromptDiaryError) as exc_info:
        _workflow(WritingPhaseRunner()).run_phase(
            workspace_path=workspace,
            phase="evidence",
            project_key="Project-123",
            session_ref="S0001",
        )

    assert "Generation task evidence:Project-123:S0001 failed:" in str(exc_info.value)
    assert "missing prerequisite artifact" in str(exc_info.value)


def test_run_generate_phase_reports_runner_failure_without_error_details(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(PromptDiaryError, match="failed with status failed"):
        _workflow(FailingWithoutDetailsPhaseRunner()).run_phase(
            workspace_path=workspace,
            phase="daily",
        )


def test_run_generate_phase_catches_prompt_diary_error_from_runner(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(PromptDiaryError, match="daily synthesis runner raised"):
        _workflow(RaisingPhaseRunner()).run_phase(
            workspace_path=workspace,
            phase="daily",
        )


def test_run_pipeline_emits_run_started_and_finished(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    workflow = _workflow(WritingPhaseRunner())

    reporter = RecordingReporter()
    workflow.run_pipeline(workspace_path=workspace, reporter=reporter)

    started = reporter.events[0]
    finished = reporter.events[-1]
    assert isinstance(started, RunStarted)
    assert isinstance(finished, RunFinished)
    total_tasks = sum(count for _, count in started.kind_totals)
    assert total_tasks > 0
    assert finished.succeeded + finished.failed + finished.blocked == total_tasks
    # The fixture has no projects, so the plan contains only the daily_synthesis task,
    # and WritingPhaseRunner always returns status="success".
    assert finished.succeeded == total_tasks
    assert finished.failed == 0
    assert finished.blocked == 0


def test_run_phase_emits_run_and_task_envelope(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    workflow = _workflow(WritingPhaseRunner())

    reporter = RecordingReporter()
    workflow.run_phase(workspace_path=workspace, phase="daily", reporter=reporter)

    names = [type(e).__name__ for e in reporter.events]
    assert names[0] == "RunStarted"
    assert names[1] == "PhaseStarted"
    assert names[2] == "TaskStarted"
    assert names[-3] == "TaskFinished"
    assert names[-2] == "PhaseFinished"
    assert names[-1] == "RunFinished"
    started = reporter.events[2]
    finished = reporter.events[-3]
    assert isinstance(started, TaskStarted)
    assert isinstance(finished, TaskFinished)
    assert finished.status == "success"


@dataclass
class WritingPhaseRunner:
    events: list[str] = field(default_factory=list)

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        self.events.append(task.task_id)
        _write_declared_outputs(workspace_path=workspace_path, task=task)
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class ContextManagedWritingPhaseRunner(WritingPhaseRunner):
    entered: int = 0
    exited: int = 0

    async def __aenter__(self) -> ContextManagedWritingPhaseRunner:
        self.entered += 1
        self.events.append("enter")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        self.exited += 1
        self.events.append("exit")


@dataclass
class NoOutputPhaseRunner:
    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del workspace_path, reporter
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class EvidenceGapPhaseRunner:
    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        if task.kind == "evidence_extraction":
            _write_declared_outputs(workspace_path=workspace_path, task=task)
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=("mock evidence extraction failed",),
            )
        _write_declared_outputs(workspace_path=workspace_path, task=task)
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class FailingWithoutDetailsPhaseRunner:
    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del workspace_path, reporter
        return TaskResult(task_id=task.task_id, status="failed")


@dataclass
class RaisingPhaseRunner:
    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del workspace_path, task, reporter
        raise PromptDiaryError(_raising_runner_message())


def _raising_runner_message() -> str:
    return "daily synthesis runner raised"


def _all_phase_runners(phase_runner: PhaseRunner) -> dict[TaskKind, PhaseRunner]:
    return {
        "evidence_extraction": phase_runner,
        "project_synthesis": phase_runner,
        "daily_synthesis": phase_runner,
    }


def _write_workspace_metadata(workspace: Path, *, timezone_name: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    if timezone_name == "Asia/Shanghai":
        local_start = "2026-05-12T00:00:00+08:00"
        local_end = "2026-05-13T00:00:00+08:00"
        utc_start = "2026-05-11T16:00:00Z"
        utc_end = "2026-05-12T16:00:00Z"
    else:
        local_start = "2026-05-12T00:00:00+00:00"
        local_end = "2026-05-13T00:00:00+00:00"
        utc_start = "2026-05-12T00:00:00Z"
        utc_end = "2026-05-13T00:00:00Z"
    (workspace / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "report_date": "2026-05-12",
                "timezone": timezone_name,
                "status": "final",
                "prepared_at": "2026-05-13T09:00:00+08:00",
                "report_window_local": {
                    "start": local_start,
                    "end": local_end,
                },
                "report_window_utc": {
                    "start": utc_start,
                    "end": utc_end,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "projects").mkdir(exist_ok=True)


def _write_declared_outputs(*, workspace_path: Path, task: TaskSpec) -> None:
    for artifact in task.output_artifacts:
        output_path = workspace_path / artifact.path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if artifact.path.name == "report.md":
            output_path.write_text("# Prompt Diary Report\n", encoding="utf-8")
        else:
            output_path.write_text("{}\n", encoding="utf-8")


def _write_project(*, workspace: Path, project_key: str, session_ref: str) -> None:
    project_dir = workspace / "projects" / project_key
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 2,
            "project_key": project_key,
            "project_label": project_key,
        },
    )
    session_path = project_dir / "sessions" / "codex" / "session.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{}\n{}\n", encoding="utf-8")
    _write_jsonl(
        project_dir / "sessions.index.jsonl",
        [
            {
                "session_ref": session_ref,
                "source": "codex",
                "source_session_id": "session",
                "session_path": "sessions/codex/session.jsonl",
                "target_start_line": 1,
                "target_end_line": 2,
                "turns": [
                    {
                        "turn_ref": "T0001",
                        "turn_start_line": 1,
                        "turn_end_line": 2,
                        "target_subagents": [],
                    }
                ],
            }
        ],
    )


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows),
        encoding="utf-8",
    )
