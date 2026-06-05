"""Daily synthesis phase runner.

Orchestrates the whole daily-synthesis phase for one prepared workspace: it runs the deterministic
Build step, drives the focused agent passes that fill the three ``synthesize`` slots, runs the
deterministic Finalize and Markdown render, and returns the task result. Build, Finalize, and the
write tools own all validation and resolution; this runner only sequences the passes and checks
that each pass actually wrote its slot.

Each pass is its own agent conversation — a fresh ``agent_factory.runner(...)`` per pass — and runs
a single turn. The runner does not retry a pass: a pass's prompt instructs the agent to self-correct
within the turn on a ``status: invalid`` tool result, so after the turn the runner only re-reads the
report and fails if the slot is still ``null``. A report with no reportable work item — every
project gap-only or excluded-only, or no project at all — short-circuits: no summary, engagement, or
team-learning pass runs, Finalize leaves the judgment slots ``null``, and the Markdown render emits
the Empty fallbacks.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentConfig
from prompt_diary.generate.daily_synthesis.build import build_daily_report
from prompt_diary.generate.daily_synthesis.finalize import (
    FinalizeInvalidResult,
    finalize_daily_report,
)
from prompt_diary.generate.daily_synthesis.inputs import (
    build_project_summary_inputs,
    build_report_inputs,
)
from prompt_diary.generate.daily_synthesis.model import REPORTABLE_WORK_ITEM_KINDS
from prompt_diary.generate.daily_synthesis.render_markdown import render_report
from prompt_diary.generate.daily_synthesis.render_notion import render_notion_artifact
from prompt_diary.generate.pipeline import TaskResult
from prompt_diary.generate.prompts import (
    engagement_prompt,
    project_summary_prompt,
    team_learning_prompt,
)
from prompt_diary.progress.events import PhaseFinished, PhaseStarted
from prompt_diary.progress.reporter import NULL_REPORTER

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentRunner, AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskSpec
    from prompt_diary.progress.reporter import ProgressReporter


DEFAULT_DAILY_SYNTHESIS_REASONING_EFFORT = "medium"
"""Per-pass Codex reasoning effort for daily synthesis.

Writing a curated, cited project summary or judgment section is comparable in depth to project
synthesis — more judgment than evidence extraction, but not deep problem solving — so each pass pins
a mid-level effort instead of inheriting the user's global Codex setting. It is a per-conversation
(``AgentConfig``) value; override it by constructing the runner with ``reasoning_effort``.
"""

_REPORT_NAME = "daily-report.json"
_REPORT_MD_NAME = "report.md"
_REPORT_NOTION_NAME = "report.notion.json"


@dataclass(frozen=True)
class DailySynthesisRunner:
    """Drive the deterministic steps and agent passes that build one day's report."""

    agent_factory: AgentSessionFactory
    reasoning_effort: str | None = DEFAULT_DAILY_SYNTHESIS_REASONING_EFFORT

    async def run(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        reporter: ProgressReporter = NULL_REPORTER,
    ) -> TaskResult:
        """Run the daily synthesis task: Build, the agent passes, Finalize, and render."""
        # The rendered reports (report.md, report.notion.json) must exist only after a successful
        # render, so clear stale ones from a previous run before anything else — including before
        # Build. A run that now fails (Build raises on a corrupt envelope, a pass writes nothing, or
        # Finalize rejects) must not leave an old rendered report beside the new, partial
        # daily-report.json.
        _reset_rendered_report(workspace_path)
        report = build_daily_report(workspace_path=workspace_path)

        for project_key in _work_bearing_projects(report):
            failure = await self._run_project_summary(workspace_path, task, project_key)
            if failure is not None:
                return failure

        if _has_any_work_item(report):
            failure = await self._run_report_passes(workspace_path, task)
            if failure is not None:
                return failure

        finalized = finalize_daily_report(workspace_path=workspace_path)
        if isinstance(finalized, FinalizeInvalidResult):
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                errors=tuple(error.message for error in finalized.errors),
            )

        # Render both views transactionally: if either renderer raises, leave neither rendered
        # report behind (the pipeline turns the propagated error into a failed task), so a failed
        # run never leaves a fresh report.md without its report.notion.json, or vice versa.
        rendered = False
        render_status = "failed"
        reporter.emit(PhaseStarted(at=time.monotonic(), phase_id="rendering", label="rendering"))
        try:
            render_report(workspace_path=workspace_path)
            render_notion_artifact(workspace_path=workspace_path)
            rendered = True
            render_status = "success"
        finally:
            if not rendered:
                _reset_rendered_report(workspace_path)
            reporter.emit(
                PhaseFinished(at=time.monotonic(), phase_id="rendering", status=render_status)
            )
        return TaskResult(
            task_id=task.task_id, status="success", output_artifacts=task.output_artifacts
        )

    async def _run_project_summary(
        self, workspace_path: Path, task: TaskSpec, project_key: str
    ) -> TaskResult | None:
        inputs = build_project_summary_inputs(
            workspace_path=workspace_path, project_key=project_key
        )
        runner = await self._new_runner(workspace_path)
        await runner.turn(
            project_summary_prompt(
                project_key=inputs.project_key,
                project_json=inputs.project_json,
                work_items=inputs.work_items,
            )
        )
        if _project_summary(workspace_path, project_key) is None:
            return _failed(task, _summary_not_written_message(project_key))
        return None

    async def _run_report_passes(self, workspace_path: Path, task: TaskSpec) -> TaskResult | None:
        inputs = build_report_inputs(workspace_path=workspace_path)

        engagement_runner = await self._new_runner(workspace_path)
        await engagement_runner.turn(
            engagement_prompt(
                work_items=inputs.work_items,
                source_user_messages=inputs.source_user_messages,
            )
        )
        if _slot(workspace_path, "engagement_assessment") is None:
            return _failed(task, _section_not_written_message("engagement_assessment"))

        learning_runner = await self._new_runner(workspace_path)
        await learning_runner.turn(
            team_learning_prompt(
                work_items=inputs.work_items,
                source_user_messages=inputs.source_user_messages,
            )
        )
        if _slot(workspace_path, "team_learning") is None:
            return _failed(task, _section_not_written_message("team_learning"))
        return None

    async def _new_runner(self, workspace_path: Path) -> AgentRunner:
        return await self.agent_factory.runner(
            AgentConfig(
                working_directory=workspace_path,
                approval_mode="auto_review",
                sandbox="workspace-write",
                reasoning_effort=self.reasoning_effort,
            )
        )


def _work_bearing_projects(report: dict[str, Any]) -> tuple[str, ...]:
    # Only projects with reportable work get a summary pass: a gap-only / excluded-only project has
    # no committed, citable turn, so its summary pass could never write a valid citation.
    return tuple(
        _as_str(_as_mapping(project).get("project_key"))
        for project in _as_list(report.get("projects"))
        if _has_reportable_work_item(_as_mapping(project))
    )


def _has_any_work_item(report: dict[str, Any]) -> bool:
    # Engagement and team-learning run only when some project has reportable work to read.
    return any(
        _has_reportable_work_item(_as_mapping(project))
        for project in _as_list(report.get("projects"))
    )


def _has_reportable_work_item(project: dict[str, Any]) -> bool:
    return any(
        _as_mapping(item).get("kind") in REPORTABLE_WORK_ITEM_KINDS
        for item in _as_list(project.get("work_items"))
    )


def _project_summary(workspace_path: Path, project_key: str) -> object:
    report = _read_report(workspace_path)
    for project in _as_list(report.get("projects")):
        mapping = _as_mapping(project)
        if mapping.get("project_key") == project_key:
            return mapping.get("summary")
    # The key comes from _work_bearing_projects reading this same report, so its entry is always
    # present; this fallback only guards a report mutated out from under the pass.
    return None  # pragma: no cover


def _slot(workspace_path: Path, slot: str) -> object:
    return _read_report(workspace_path).get(slot)


def _reset_rendered_report(workspace_path: Path) -> None:
    # Clear both rendered views (Markdown and Notion) so a run that fails before rendering leaves no
    # stale report beside the new, partial daily-report.json.
    (workspace_path / _REPORT_MD_NAME).unlink(missing_ok=True)
    (workspace_path / _REPORT_NOTION_NAME).unlink(missing_ok=True)


def _read_report(workspace_path: Path) -> dict[str, Any]:
    raw: object = json.loads((workspace_path / _REPORT_NAME).read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _failed(task: TaskSpec, message: str) -> TaskResult:
    return TaskResult(task_id=task.task_id, status="failed", errors=(message,))


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _summary_not_written_message(project_key: str) -> str:
    return f"project summary pass did not write projects[{project_key}].summary"


def _section_not_written_message(slot: str) -> str:
    return f"{slot} pass did not write {slot}"
