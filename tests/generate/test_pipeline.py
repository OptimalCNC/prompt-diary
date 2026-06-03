from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis import DailySynthesisRunner
from prompt_diary.generate.pipeline import (
    ArtifactSpec,
    GeneratePipelineRunner,
    GenerationPlan,
    PhaseRunner,
    PipelineRunResult,
    TaskKind,
    TaskResult,
    TaskSpec,
    build_generation_plan,
    daily_synthesis_task_id,
    evidence_task_id,
    project_synthesis_task_id,
    run_generation_task,
)
from prompt_diary.generate.workspace import IndexedSession, PreparedProject, load_prepared_workspace
from prompt_diary.progress.events import TaskFinished
from prompt_diary.progress.reporter import NULL_REPORTER
from tests.agent_fakes import FakeAgentSessionFactory
from tests.support.progress import RecordingReporter

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentConfig, AgentTurnResult
    from prompt_diary.progress.reporter import ProgressReporter

TIMEOUT_MESSAGE = "timed out"


def test_generation_plan_builds_project_local_fan_in(tmp_path: Path) -> None:
    workspace = _workspace_fixture(
        tmp_path,
        {
            "Alpha-111111111111": 2,
            "Beta-222222222222": 1,
        },
    )

    plan = build_generation_plan(workspace)
    tasks = dict(plan.task_map())

    assert tuple(tasks) == (
        "evidence:Alpha-111111111111:S0001",
        "evidence:Alpha-111111111111:S0002",
        "project:Alpha-111111111111",
        "evidence:Beta-222222222222:S0001",
        "project:Beta-222222222222",
        "daily",
    )
    alpha_project = tasks[project_synthesis_task_id("Alpha-111111111111")]
    assert alpha_project.depends_on == (
        evidence_task_id("Alpha-111111111111", "S0001"),
        evidence_task_id("Alpha-111111111111", "S0002"),
    )
    assert not alpha_project.dependency_failure_blocks
    assert [artifact.path.as_posix() for artifact in alpha_project.prerequisite_artifacts] == [
        "metadata.json",
        "projects/Alpha-111111111111/project.json",
        "projects/Alpha-111111111111/sessions.index.jsonl",
        "projects/Alpha-111111111111/evidence/S0001.json",
        "projects/Alpha-111111111111/evidence/S0002.json",
    ]
    daily = tasks[daily_synthesis_task_id()]
    assert daily.depends_on == (
        project_synthesis_task_id("Alpha-111111111111"),
        project_synthesis_task_id("Beta-222222222222"),
    )
    assert [artifact.path.as_posix() for artifact in daily.output_artifacts] == [
        "daily-report.json",
        "report.md",
        "report.notion.json",
    ]


def test_pipeline_runs_mock_phases_and_writes_durable_artifacts(tmp_path: Path) -> None:
    workspace = _workspace_fixture(tmp_path, {"Alpha-111111111111": 2})
    plan = build_generation_plan(workspace)
    phase_runner = WritingPhaseRunner()

    result = asyncio.run(
        GeneratePipelineRunner(
            phase_runners=_all_phase_runners(phase_runner),
            concurrency_limits={
                "evidence_extraction": 1,
                "project_synthesis": 1,
                "daily_synthesis": 1,
            },
        ).run(workspace_path=workspace, plan=plan)
    )

    assert result.ok
    assert (workspace / "projects" / "Alpha-111111111111" / "evidence" / "S0001.json").exists()
    assert (workspace / "projects" / "Alpha-111111111111" / "evidence" / "S0002.json").exists()
    assert (workspace / "projects" / "Alpha-111111111111" / "project-synthesis.json").exists()
    assert (workspace / "daily-report.json").exists()
    assert (workspace / "report.md").exists()
    assert phase_runner.events[-1] == "daily"


def test_standalone_task_checks_prerequisites_without_rerunning_previous_phases(
    tmp_path: Path,
) -> None:
    workspace = _workspace_fixture(tmp_path, {"Alpha-111111111111": 1})
    plan = build_generation_plan(workspace)
    evidence_task = plan.task_map()[evidence_task_id("Alpha-111111111111", "S0001")]
    session_path = workspace / "projects" / "Alpha-111111111111" / "sessions" / "codex"
    (session_path / "session-0001.jsonl").unlink()
    phase_runner = CalledFlagRunner()

    result = asyncio.run(
        run_generation_task(
            workspace_path=workspace,
            task=evidence_task,
            phase_runner=phase_runner,
        )
    )

    assert result.status == "failed"
    assert not phase_runner.called
    assert result.errors == (
        "missing prerequisite artifact: "
        "projects/Alpha-111111111111/sessions/codex/session-0001.jsonl",
    )


def test_project_synthesis_can_start_before_unrelated_project_evidence_finishes(
    tmp_path: Path,
) -> None:
    workspace = _workspace_fixture(
        tmp_path,
        {
            "Alpha-111111111111": 1,
            "Beta-222222222222": 1,
        },
    )
    plan = build_generation_plan(workspace)

    async def run_pipeline() -> ProjectLocalFanInRunner:
        alpha_project_started = asyncio.Event()
        phase_runner = ProjectLocalFanInRunner(alpha_project_started=alpha_project_started)
        result = await asyncio.wait_for(
            GeneratePipelineRunner(
                phase_runners=_all_phase_runners(phase_runner),
                concurrency_limits={
                    "evidence_extraction": 2,
                    "project_synthesis": 1,
                    "daily_synthesis": 1,
                },
            ).run(workspace_path=workspace, plan=plan),
            timeout=2,
        )
        assert result.ok
        return phase_runner

    phase_runner = asyncio.run(run_pipeline())

    alpha_project_start = phase_runner.events.index("start project:Alpha-111111111111")
    beta_evidence_finish = phase_runner.events.index("finish evidence:Beta-222222222222:S0001")
    assert alpha_project_start < beta_evidence_finish


def test_failed_evidence_with_durable_card_does_not_block_project_gap_accounting(
    tmp_path: Path,
) -> None:
    workspace = _workspace_fixture(
        tmp_path,
        {
            "Alpha-111111111111": 1,
            "Beta-222222222222": 1,
        },
    )
    plan = build_generation_plan(workspace)
    phase_runner = FailingEvidenceRunner(
        failed_task_id=evidence_task_id("Alpha-111111111111", "S0001")
    )

    result = asyncio.run(
        GeneratePipelineRunner(phase_runners=_all_phase_runners(phase_runner)).run(
            workspace_path=workspace,
            plan=plan,
        )
    )

    assert result.ok
    assert not result.all_tasks_ok
    assert result.result_for(evidence_task_id("Alpha-111111111111", "S0001")).status == "failed"
    assert result.result_for(project_synthesis_task_id("Alpha-111111111111")).status == "success"
    assert result.result_for(project_synthesis_task_id("Beta-222222222222")).status == "success"
    assert result.result_for(daily_synthesis_task_id()).status == "success"


def test_missing_evidence_card_fails_project_and_blocks_daily(tmp_path: Path) -> None:
    workspace = _workspace_fixture(
        tmp_path,
        {
            "Alpha-111111111111": 1,
            "Beta-222222222222": 1,
        },
    )
    plan = build_generation_plan(workspace)
    phase_runner = MissingEvidenceCardRunner(
        failed_task_id=evidence_task_id("Alpha-111111111111", "S0001")
    )

    result = asyncio.run(
        GeneratePipelineRunner(phase_runners=_all_phase_runners(phase_runner)).run(
            workspace_path=workspace,
            plan=plan,
        )
    )

    assert not result.ok
    assert result.result_for(evidence_task_id("Alpha-111111111111", "S0001")).status == "failed"
    assert result.result_for(project_synthesis_task_id("Alpha-111111111111")).status == "failed"
    assert result.result_for(project_synthesis_task_id("Alpha-111111111111")).errors == (
        "missing prerequisite artifact: projects/Alpha-111111111111/evidence/S0001.json",
    )
    assert result.result_for(project_synthesis_task_id("Beta-222222222222")).status == "success"
    assert result.result_for(daily_synthesis_task_id()).status == "blocked"


def test_failed_project_blocks_daily_synthesis(tmp_path: Path) -> None:
    workspace = _workspace_fixture(
        tmp_path,
        {
            "Alpha-111111111111": 1,
            "Beta-222222222222": 1,
        },
    )
    plan = build_generation_plan(workspace)
    phase_runner = FailingProjectRunner(
        failed_task_id=project_synthesis_task_id("Alpha-111111111111")
    )

    result = asyncio.run(
        GeneratePipelineRunner(phase_runners=_all_phase_runners(phase_runner)).run(
            workspace_path=workspace,
            plan=plan,
        )
    )

    assert not result.ok
    assert result.result_for(project_synthesis_task_id("Alpha-111111111111")).status == "failed"
    assert result.result_for(project_synthesis_task_id("Beta-222222222222")).status == "success"
    assert result.result_for(daily_synthesis_task_id()).status == "blocked"


def test_failed_dependency_blocks_transitive_dependents(tmp_path: Path) -> None:
    plan = GenerationPlan(
        tasks=(
            TaskSpec(task_id="a", kind="daily_synthesis"),
            TaskSpec(task_id="b", kind="daily_synthesis", depends_on=("a",)),
            TaskSpec(task_id="c", kind="daily_synthesis", depends_on=("b",)),
        )
    )

    result = asyncio.run(
        GeneratePipelineRunner(
            phase_runners={"daily_synthesis": FailingTaskRunner(failed_task_id="a")}
        ).run(workspace_path=tmp_path, plan=plan)
    )

    assert not result.ok
    assert result.result_for("a").status == "failed"
    assert result.result_for("b").status == "blocked"
    assert result.result_for("c").status == "blocked"
    assert result.result_for("c").errors == ("dependency did not complete successfully: b",)


def test_pipeline_emits_task_started_and_finished(tmp_path: Path) -> None:
    workspace = _workspace_fixture(tmp_path, {"Alpha-111111111111": 1})
    plan = build_generation_plan(workspace)
    reporter = RecordingReporter()

    asyncio.run(
        GeneratePipelineRunner(
            phase_runners=_all_phase_runners(WritingPhaseRunner()),
            concurrency_limits={
                "evidence_extraction": 1,
                "project_synthesis": 1,
                "daily_synthesis": 1,
            },
            reporter=reporter,
        ).run(workspace_path=workspace, plan=plan)
    )

    kinds = [type(event).__name__ for event in reporter.events]
    assert "TaskStarted" in kinds
    assert "TaskFinished" in kinds


def test_pipeline_emits_blocked_task_finished(tmp_path: Path) -> None:
    plan = GenerationPlan(
        tasks=(
            TaskSpec(task_id="a", kind="daily_synthesis"),
            TaskSpec(task_id="b", kind="daily_synthesis", depends_on=("a",)),
        )
    )
    reporter = RecordingReporter()

    result = asyncio.run(
        GeneratePipelineRunner(
            phase_runners={"daily_synthesis": FailingTaskRunner(failed_task_id="a")},
            reporter=reporter,
        ).run(workspace_path=tmp_path, plan=plan)
    )

    assert result.result_for("b").status == "blocked"
    finished_events = [event for event in reporter.events if isinstance(event, TaskFinished)]
    blocked_event = next(e for e in finished_events if e.task_id == "b")
    assert blocked_event.status == "blocked"


def test_standalone_daily_phase_propagates_workspace_error(tmp_path: Path) -> None:
    # On a workspace with no metadata.json, the runner's Build step raises a PromptDiaryError before
    # any agent turn; like project synthesis, the runner lets it propagate for the pipeline to wrap
    # into a failed TaskResult, so the agent script is never invoked.
    task = TaskSpec(task_id="placeholder", kind="daily_synthesis")
    factory = FakeAgentSessionFactory(script=_unused_agent_script)

    with pytest.raises(PromptDiaryError, match="required JSON file is missing"):
        asyncio.run(
            DailySynthesisRunner(agent_factory=factory).run(
                workspace_path=tmp_path, task=task, reporter=NULL_REPORTER
            )
        )


def test_pipeline_validation_errors_are_actionable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace-relative"):
        ArtifactSpec(PurePosixPath("/absolute.json"), "bad artifact")

    duplicate = TaskSpec(task_id="duplicate", kind="daily_synthesis")
    with pytest.raises(PromptDiaryError, match="duplicate generation task id"):
        GenerationPlan(tasks=(duplicate, duplicate)).task_map()

    with pytest.raises(KeyError):
        PipelineRunResult(results=()).result_for("missing")

    assert not PipelineRunResult(results=(TaskResult(task_id="failed", status="failed"),)).ok

    phase_runner = WritingPhaseRunner()
    runner = GeneratePipelineRunner(phase_runners=_all_phase_runners(phase_runner))

    unknown_dependency_plan = GenerationPlan(
        tasks=(
            TaskSpec(
                task_id="daily",
                kind="daily_synthesis",
                depends_on=("missing",),
            ),
        )
    )
    with pytest.raises(PromptDiaryError, match="depends on unknown task"):
        asyncio.run(runner.run(workspace_path=tmp_path, plan=unknown_dependency_plan))

    missing_runner_plan = GenerationPlan(tasks=(TaskSpec(task_id="daily", kind="daily_synthesis"),))
    with pytest.raises(PromptDiaryError, match="missing phase runner"):
        asyncio.run(
            GeneratePipelineRunner(phase_runners={}).run(
                workspace_path=tmp_path,
                plan=missing_runner_plan,
            )
        )

    unknown_limit = cast("dict[TaskKind, int]", {"unknown": 1})
    with pytest.raises(PromptDiaryError, match="unknown generation task kind"):
        asyncio.run(
            GeneratePipelineRunner(
                phase_runners={},
                concurrency_limits=unknown_limit,
            ).run(workspace_path=tmp_path, plan=GenerationPlan(tasks=()))
        )

    with pytest.raises(PromptDiaryError, match="must be positive"):
        asyncio.run(
            GeneratePipelineRunner(
                phase_runners={},
                concurrency_limits={"daily_synthesis": 0},
            ).run(workspace_path=tmp_path, plan=GenerationPlan(tasks=()))
        )

    cyclic_plan = GenerationPlan(
        tasks=(
            TaskSpec(task_id="a", kind="daily_synthesis", depends_on=("b",)),
            TaskSpec(task_id="b", kind="daily_synthesis", depends_on=("a",)),
        )
    )
    with pytest.raises(PromptDiaryError, match="unresolved dependencies"):
        asyncio.run(runner.run(workspace_path=tmp_path, plan=cyclic_plan))


def test_task_success_requires_declared_outputs(tmp_path: Path) -> None:
    task = TaskSpec(
        task_id="missing-output",
        kind="daily_synthesis",
        output_artifacts=(ArtifactSpec(PurePosixPath("missing.json"), "missing output"),),
    )

    result = asyncio.run(
        run_generation_task(
            workspace_path=tmp_path,
            task=task,
            phase_runner=CalledFlagRunner(),
        )
    )

    assert result.status == "failed"
    assert result.errors == ("missing output artifact after success: missing.json",)


def test_task_converts_unexpected_runner_exception_to_failure(tmp_path: Path) -> None:
    task = TaskSpec(task_id="timeout", kind="daily_synthesis")

    result = asyncio.run(
        run_generation_task(
            workspace_path=tmp_path,
            task=task,
            phase_runner=UnexpectedErrorRunner(),
        )
    )

    assert result.status == "failed"
    assert result.errors == ("unexpected phase runner error: TimeoutError: timed out",)


def test_pipeline_enters_context_managed_phase_runner_once(tmp_path: Path) -> None:
    workspace = _workspace_fixture(tmp_path, {"Alpha-111111111111": 1})
    plan = build_generation_plan(workspace)
    phase_runner = ContextManagedWritingPhaseRunner()

    result = asyncio.run(
        GeneratePipelineRunner(phase_runners=_all_phase_runners(phase_runner)).run(
            workspace_path=workspace,
            plan=plan,
        )
    )

    assert result.ok
    assert phase_runner.entered == 1
    assert phase_runner.exited == 1
    assert phase_runner.events[0] == "enter"
    assert phase_runner.events[-1] == "exit"


def test_load_prepared_workspace_handles_empty_projects_and_missing_index(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "empty-projects" / "work"
    _write_metadata(workspace)

    loaded_empty = load_prepared_workspace(workspace)

    assert loaded_empty.report_date == "2026-05-12"
    assert loaded_empty.projects == ()

    project_workspace = tmp_path / "missing-index" / "work"
    _write_metadata(project_workspace)
    _write_project_json(project_workspace / "projects" / "Alpha-111111111111")

    loaded_project = load_prepared_workspace(project_workspace)

    assert loaded_project.projects[0].sessions == ()


def test_load_prepared_workspace_reports_workspace_shape_errors(tmp_path: Path) -> None:
    assert "required JSON file is missing" in _load_error(tmp_path / "missing")

    invalid_json_workspace = tmp_path / "invalid-json"
    invalid_json_workspace.mkdir()
    (invalid_json_workspace / "metadata.json").write_text("{", encoding="utf-8")
    assert "contains invalid JSON" in _load_error(invalid_json_workspace)

    scalar_metadata_workspace = tmp_path / "scalar-metadata"
    scalar_metadata_workspace.mkdir()
    (scalar_metadata_workspace / "metadata.json").write_text("[]", encoding="utf-8")
    assert "must contain a JSON object" in _load_error(scalar_metadata_workspace)

    missing_timezone_workspace = tmp_path / "missing-timezone"
    _write_metadata(missing_timezone_workspace)
    metadata = _load_json(missing_timezone_workspace / "metadata.json")
    del metadata["timezone"]
    _write_json(missing_timezone_workspace / "metadata.json", metadata)
    assert "missing string field 'timezone'" in _load_error(missing_timezone_workspace)

    project_mismatch_workspace = _workspace_fixture(
        tmp_path / "project-mismatch",
        {"Alpha-111111111111": 1},
    )
    _write_json(
        _project_dir(project_mismatch_workspace, "Alpha-111111111111") / "project.json",
        {
            "schema_version": 2,
            "project_key": "Other-222222222222",
            "project_label": "Other",
        },
    )
    assert "must match directory" in _load_error(project_mismatch_workspace)


def test_load_prepared_workspace_reports_session_index_shape_errors(tmp_path: Path) -> None:
    invalid_index_workspace = _workspace_fixture(tmp_path / "invalid-index", {"Alpha": 1})
    _index_path(invalid_index_workspace, "Alpha").write_text("{\n", encoding="utf-8")
    assert "contains invalid JSON" in _load_error(invalid_index_workspace)

    scalar_index_workspace = _workspace_fixture(tmp_path / "scalar-index", {"Alpha": 1})
    _index_path(scalar_index_workspace, "Alpha").write_text("[]\n", encoding="utf-8")
    assert "must contain a JSON object" in _load_error(scalar_index_workspace)

    blank_line_workspace = _workspace_fixture(tmp_path / "blank-line", {"Alpha": 1})
    index_path = _index_path(blank_line_workspace, "Alpha")
    index_path.write_text(f"\n{index_path.read_text(encoding='utf-8')}", encoding="utf-8")
    assert len(load_prepared_workspace(blank_line_workspace).projects[0].sessions) == 1

    missing_string_workspace = _workspace_fixture(tmp_path / "missing-string", {"Alpha": 1})
    row = _load_jsonl(_index_path(missing_string_workspace, "Alpha"))[0]
    del row["session_ref"]
    _write_jsonl(_index_path(missing_string_workspace, "Alpha"), [row])
    assert "missing string field 'session_ref'" in _load_error(missing_string_workspace)

    missing_int_workspace = _workspace_fixture(tmp_path / "missing-int", {"Alpha": 1})
    row = _load_jsonl(_index_path(missing_int_workspace, "Alpha"))[0]
    del row["target_end_line"]
    _write_jsonl(_index_path(missing_int_workspace, "Alpha"), [row])
    assert "missing integer field 'target_end_line'" in _load_error(missing_int_workspace)

    duplicate_session_workspace = _workspace_fixture(
        tmp_path / "duplicate-session",
        {"Alpha": 1},
    )
    row = _load_jsonl(_index_path(duplicate_session_workspace, "Alpha"))[0]
    _write_jsonl(_index_path(duplicate_session_workspace, "Alpha"), [row, row])
    assert "duplicate session_ref 'S0001'" in _load_error(duplicate_session_workspace)

    nonpositive_span_workspace = _workspace_fixture(
        tmp_path / "nonpositive-span",
        {"Alpha": 1},
    )
    row = _load_jsonl(_index_path(nonpositive_span_workspace, "Alpha"))[0]
    row["target_start_line"] = 0
    _write_jsonl(_index_path(nonpositive_span_workspace, "Alpha"), [row])
    assert "target span start line must be positive" in _load_error(nonpositive_span_workspace)

    unordered_span_workspace = _workspace_fixture(tmp_path / "unordered-span", {"Alpha": 1})
    row = _load_jsonl(_index_path(unordered_span_workspace, "Alpha"))[0]
    row["target_end_line"] = 1
    _write_jsonl(_index_path(unordered_span_workspace, "Alpha"), [row])
    assert "target span end line must be >= start line" in _load_error(unordered_span_workspace)

    missing_turns_workspace = _workspace_fixture(tmp_path / "missing-turns", {"Alpha": 1})
    row = _load_jsonl(_index_path(missing_turns_workspace, "Alpha"))[0]
    del row["turns"]
    _write_jsonl(_index_path(missing_turns_workspace, "Alpha"), [row])
    assert "missing array field 'turns'" in _load_error(missing_turns_workspace)

    non_object_turn_workspace = _workspace_fixture(tmp_path / "non-object-turn", {"Alpha": 1})
    row = _load_jsonl(_index_path(non_object_turn_workspace, "Alpha"))[0]
    row["turns"] = ["not-object"]
    _write_jsonl(_index_path(non_object_turn_workspace, "Alpha"), [row])
    assert "turns[1] must be a JSON object" in _load_error(non_object_turn_workspace)

    malformed_turn_workspace = _workspace_fixture(tmp_path / "malformed-turn", {"Alpha": 1})
    row = _load_jsonl(_index_path(malformed_turn_workspace, "Alpha"))[0]
    turns = cast("list[dict[str, object]]", row["turns"])
    turns[0]["turn_ref"] = "turn-1"
    _write_jsonl(_index_path(malformed_turn_workspace, "Alpha"), [row])
    assert "turn_ref 'turn-1' must match T0001" in _load_error(malformed_turn_workspace)

    duplicate_turn_workspace = _workspace_fixture(tmp_path / "duplicate-turn", {"Alpha": 1})
    row = _load_jsonl(_index_path(duplicate_turn_workspace, "Alpha"))[0]
    turn = cast("list[dict[str, object]]", row["turns"])[0]
    row["turns"] = [turn, dict(turn)]
    _write_jsonl(_index_path(duplicate_turn_workspace, "Alpha"), [row])
    assert "duplicate turn_ref 'T0001'" in _load_error(duplicate_turn_workspace)

    unordered_turn_workspace = _workspace_fixture(tmp_path / "unordered-turn", {"Alpha": 1})
    row = _load_jsonl(_index_path(unordered_turn_workspace, "Alpha"))[0]
    turn = cast("list[dict[str, object]]", row["turns"])[0]
    turn["turn_start_line"] = 4
    turn["turn_end_line"] = 2
    _write_jsonl(_index_path(unordered_turn_workspace, "Alpha"), [row])
    assert "turn T0001 end line must be >= start line" in _load_error(unordered_turn_workspace)

    invalid_path_workspace = _workspace_fixture(tmp_path / "invalid-path", {"Alpha": 1})
    row = _load_jsonl(_index_path(invalid_path_workspace, "Alpha"))[0]
    row["session_path"] = "../outside.jsonl"
    _write_jsonl(_index_path(invalid_path_workspace, "Alpha"), [row])
    assert "relative sessions/ path" in _load_error(invalid_path_workspace)

    escaping_symlink_workspace = _workspace_fixture(tmp_path / "escaping-symlink", {"Alpha": 1})
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    symlink_path = _project_dir(escaping_symlink_workspace, "Alpha") / "sessions" / "codex"
    symlink = symlink_path / "escape.jsonl"
    try:
        symlink.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    row = _load_jsonl(_index_path(escaping_symlink_workspace, "Alpha"))[0]
    row["session_path"] = "sessions/codex/escape.jsonl"
    _write_jsonl(_index_path(escaping_symlink_workspace, "Alpha"), [row])
    assert "session_path must resolve under" in _load_error(escaping_symlink_workspace)


@dataclass
class WritingPhaseRunner:
    events: list[str] = field(default_factory=list)

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        self.events.append(task.task_id)
        _write_task_output(workspace_path, task)
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class CalledFlagRunner:
    called: bool = False

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del workspace_path, reporter
        self.called = True
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class UnexpectedErrorRunner:
    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del workspace_path, task, reporter
        raise TimeoutError(TIMEOUT_MESSAGE)


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
class ProjectLocalFanInRunner:
    alpha_project_started: asyncio.Event
    events: list[str] = field(default_factory=list)

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        project_key = task.project_key
        if task.kind == "evidence_extraction" and project_key == "Beta-222222222222":
            self.events.append(f"start {task.task_id}")
            await self.alpha_project_started.wait()
            _write_task_output(workspace_path, task)
            self.events.append(f"finish {task.task_id}")
            return TaskResult(task_id=task.task_id, status="success")
        if task.kind == "project_synthesis" and project_key == "Alpha-111111111111":
            self.events.append(f"start {task.task_id}")
            self.alpha_project_started.set()
            _write_task_output(workspace_path, task)
            self.events.append(f"finish {task.task_id}")
            return TaskResult(task_id=task.task_id, status="success")

        self.events.append(f"start {task.task_id}")
        _write_task_output(workspace_path, task)
        self.events.append(f"finish {task.task_id}")
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class FailingEvidenceRunner:
    failed_task_id: str

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        if task.task_id == self.failed_task_id:
            _write_task_output(workspace_path, task)
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=("mock evidence extraction failed",),
            )
        _write_task_output(workspace_path, task)
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class MissingEvidenceCardRunner:
    failed_task_id: str

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        if task.task_id == self.failed_task_id:
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=("mock evidence extraction failed",),
            )
        _write_task_output(workspace_path, task)
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class FailingProjectRunner:
    failed_task_id: str

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        if task.task_id == self.failed_task_id:
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=("mock project synthesis failed",),
            )
        _write_task_output(workspace_path, task)
        return TaskResult(task_id=task.task_id, status="success")


@dataclass
class FailingTaskRunner:
    failed_task_id: str

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del workspace_path, reporter
        if task.task_id == self.failed_task_id:
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=("mock task failed",),
            )
        return TaskResult(task_id=task.task_id, status="success")


def _unused_agent_script(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    raise AssertionError(_unused_agent_script_message())


def _unused_agent_script_message() -> str:
    return "placeholder phase runners must not mint an agent turn"


def _all_phase_runners(phase_runner: PhaseRunner) -> dict[TaskKind, PhaseRunner]:
    return {
        "evidence_extraction": phase_runner,
        "project_synthesis": phase_runner,
        "daily_synthesis": phase_runner,
    }


def _write_task_output(workspace_path: Path, task: TaskSpec) -> None:
    if task.kind == "evidence_extraction":
        _write_evidence_card(workspace_path, task)
        return
    if task.kind == "project_synthesis":
        _write_project_synthesis(workspace_path, task)
        return
    _write_daily_synthesis(workspace_path)


def _write_evidence_card(workspace_path: Path, task: TaskSpec) -> None:
    project_key, session_ref = _task_project_session(task)
    session = _session(workspace_path, project_key, session_ref)
    _write_json(
        workspace_path / task.output_artifacts[0].path,
        {
            "schema_version": 1,
            "project_key": project_key,
            "session_ref": session_ref,
            "evidence_chains": [
                {
                    "turn_ref": turn.turn_ref,
                    "trigger": {
                        "type": "explicit_user_message",
                        "summary": "Mock trigger.",
                        "quoted_messages": [],
                        "citations": [{"lines": f"{turn.span.start}-{turn.span.start}"}],
                    },
                    "agent_reactions": [
                        {
                            "summary": "Mock agent reaction.",
                            "citations": [{"lines": f"{turn.span.start}-{turn.span.end}"}],
                        }
                    ],
                    "outcomes": [],
                    "observed_checks": [],
                    "terminal_state": {
                        "type": "no_material",
                        "summary": "Mock extraction produced no material claim.",
                        "citations": [{"lines": f"{turn.span.end}-{turn.span.end}"}],
                    },
                    "materiality": "none",
                }
                for turn in session.turns
            ],
        },
    )


def _write_project_synthesis(workspace_path: Path, task: TaskSpec) -> None:
    project_key = _task_project(task)
    project = _project(workspace_path, project_key)
    _write_json(
        workspace_path / task.output_artifacts[0].path,
        {
            "schema_version": 1,
            "project_key": project.project_key,
            "project_label": project.project_label,
            "progress_summary": "Mock project synthesis output.",
            "work_items": [],
            "evidence_accounting": [
                {
                    "session_ref": session.session_ref,
                    "turn_ref": turn.turn_ref,
                    "disposition": "no_material_work_item",
                    "reason": "Mock evidence was preserved without a material claim.",
                }
                for session in project.sessions
                for turn in session.turns
            ],
            "blockers": [],
            "useful_agent_driving_patterns": [],
            "risks_or_antipatterns": [],
            "confidence": "low",
        },
    )


def _write_daily_synthesis(workspace_path: Path) -> None:
    _write_json(
        workspace_path / "daily-report.json",
        {
            "schema_version": 1,
            "report_date": "2026-05-12",
            "status": "final",
            "window": {
                "local_start": "2026-05-12T00:00:00+08:00",
                "local_end": "2026-05-13T00:00:00+08:00",
                "timezone": "Asia/Shanghai",
            },
            "overall_confidence": "low",
            "executive_summary": {
                "top_outcomes": [],
                "main_risks": [],
                "confidence_limits": [],
            },
            "outcome_overview": [],
            "projects": [],
            "verification_evidence_quality": {
                "verified_results": [],
                "partially_verified_results": [],
                "unverified_claims": [],
                "contradictions": [],
                "missing_checks": [],
                "confidence_limits": [],
            },
            "engagement_assessment": {
                "overall_judgment": "Insufficient evidence to judge",
                "supporting_observations": [],
                "limits": [],
            },
            "ai_agent_driving_quality": {
                "useful_patterns": [],
                "risks_or_antipatterns": [],
                "shareable_skills": [],
            },
            "problems_risks_help_needed": [],
            "blockers_next_actions": [],
            "no_material_interrupted_examples": [],
            "follow_ups": [],
            "evidence_gaps": [],
        },
    )
    (workspace_path / "report.md").write_text(
        "\n".join(
            [
                "# Prompt Diary Report - 2026-05-12",
                "",
                "Status: final",
                "Window: 2026-05-12T00:00:00+08:00 to 2026-05-13T00:00:00+08:00 Asia/Shanghai",
                "Overall Confidence: low",
                "",
                "## Executive Summary",
                "- No supported work claims found for this report window.",
                "## Outcome Overview",
                "- No supported outcomes found for this report window.",
                "## Project Details",
                "- No supported project-level work items found for this report window.",
                "## Verification / Evidence Quality",
                "- No verification or evidence-quality issues found.",
                "## Engagement Assessment",
                "- Insufficient supported engagement evidence for this report window.",
                "## AI-Agent Driving Quality",
                "- No supported reusable agent-driving pattern found.",
                "## Problems / Risks / Help Needed",
                "- No supported problems, risks, or help requests found in target spans.",
                "## Blockers and Next Actions",
                "- No supported blockers or next actions found.",
                "## No-Material / Interrupted Examples",
                "- No supported no-material or interrupted interactions found.",
                "## Follow-ups",
                "- No supported follow-ups found.",
                "## Evidence Gaps",
                "- No evidence gaps found.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # The daily task also declares the Notion payload artifact; the mock writes a minimal one so the
    # pipeline's missing-output check passes (the real runner renders it from the layout).
    _write_json(
        workspace_path / "report.notion.json",
        {"title": "Prompt Diary Report - 2026-05-12", "properties": {}, "children": []},
    )


def _task_project(task: TaskSpec) -> str:
    if task.project_key is None:
        raise AssertionError(_missing_project_key_message(task.task_id))
    return task.project_key


def _task_project_session(task: TaskSpec) -> tuple[str, str]:
    project_key = _task_project(task)
    if task.session_ref is None:
        raise AssertionError(_missing_session_ref_message(task.task_id))
    return project_key, task.session_ref


def _project(workspace_path: Path, project_key: str) -> PreparedProject:
    workspace = load_prepared_workspace(workspace_path)
    for project in workspace.projects:
        if project.project_key == project_key:
            return project
    raise AssertionError(_missing_project_fixture_message(project_key))


def _session(workspace_path: Path, project_key: str, session_ref: str) -> IndexedSession:
    project = _project(workspace_path, project_key)
    for session in project.sessions:
        if session.session_ref == session_ref:
            return session
    raise AssertionError(_missing_session_fixture_message(project_key, session_ref))


def _workspace_fixture(tmp_path: Path, projects: dict[str, int]) -> Path:
    workspace = tmp_path / "work" / "2026-05-12"
    _write_metadata(workspace)
    for project_key, session_count in projects.items():
        _write_project(workspace, project_key, session_count)
    return workspace


def _write_metadata(workspace: Path) -> None:
    _write_json(
        workspace / "metadata.json",
        {
            "schema_version": 2,
            "report_date": "2026-05-12",
            "timezone": "Asia/Shanghai",
            "status": "final",
            "prepared_at": "2026-05-13T08:58:00+08:00",
            "report_window_local": {
                "start": "2026-05-12T00:00:00+08:00",
                "end": "2026-05-13T00:00:00+08:00",
            },
            "report_window_utc": {
                "start": "2026-05-11T16:00:00Z",
                "end": "2026-05-12T16:00:00Z",
            },
        },
    )


def _write_project(workspace: Path, project_key: str, session_count: int) -> None:
    project_dir = workspace / "projects" / project_key
    _write_project_json(project_dir)
    session_rows: list[dict[str, object]] = []
    for index in range(1, session_count + 1):
        session_ref = f"S{index:04d}"
        session_file = f"session-{index:04d}.jsonl"
        session_path = project_dir / "sessions" / "codex" / session_file
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text("{}\n{}\n{}\n{}\n", encoding="utf-8")
        session_rows.append(
            {
                "session_ref": session_ref,
                "source": "codex",
                "source_session_id": f"codex-session-{index:04d}",
                "session_path": f"sessions/codex/{session_file}",
                "target_start_line": 2,
                "target_end_line": 4,
                "turns": [
                    {
                        "turn_ref": "T0001",
                        "turn_start_line": 2,
                        "turn_end_line": 4,
                        "target_subagents": [],
                    }
                ],
            }
        )
    _write_jsonl(project_dir / "sessions.index.jsonl", session_rows)


def _write_project_json(project_dir: Path) -> None:
    project_key = project_dir.name
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 2,
            "project_key": project_key,
            "project_label": project_key.split("-", maxsplit=1)[0],
        },
    )


def _project_dir(workspace: Path, project_key: str) -> Path:
    return workspace / "projects" / project_key


def _index_path(workspace: Path, project_key: str) -> Path:
    return _project_dir(workspace, project_key) / "sessions.index.jsonl"


def _load_error(workspace: Path) -> str:
    with pytest.raises(PromptDiaryError) as exc_info:
        load_prepared_workspace(workspace)
    return str(exc_info.value)


def _load_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast("dict[str, object]", raw)


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        assert isinstance(raw, dict)
        rows.append(cast("dict[str, object]", raw))
    return rows


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(f"{json.dumps(cast('object', row), sort_keys=True)}\n" for row in rows),
        encoding="utf-8",
    )


def _missing_project_key_message(task_id: str) -> str:
    return f"task {task_id} has no project key"


def _missing_session_ref_message(task_id: str) -> str:
    return f"task {task_id} has no session ref"


def _missing_project_fixture_message(project_key: str) -> str:
    return f"missing project fixture: {project_key}"


def _missing_session_fixture_message(project_key: str, session_ref: str) -> str:
    return f"missing session fixture: {project_key}/{session_ref}"
