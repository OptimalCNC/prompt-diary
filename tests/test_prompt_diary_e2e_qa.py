from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

import prompt_diary.cmds.generate as generate_cmd
from prompt_diary.cli import app
from prompt_diary.generate.pipeline import PhaseRunner, TaskKind, TaskResult, TaskSpec
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow
from prompt_diary.models import JsonObject, SourceSpec
from prompt_diary.paths import REPORTS_HOME_ENV
from prompt_diary.prepare.workspace import CLAUDE_SOURCE_ENV, CODEX_SOURCE_ENV, prepare_workspace
from prompt_diary.progress.reporter import NULL_REPORTER
from prompt_diary.targeting.resolve import resolve_report_target
from tests.agent_fakes import FakeAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from prompt_diary.agent import AgentConfig, AgentTurnResult
    from prompt_diary.progress.reporter import ProgressReporter

TARGET_DATE = "2020-01-02"
TARGET_TIMEZONE = "Asia/Shanghai"
TARGET_NOW = datetime(2020, 1, 3, 9, 2, tzinfo=ZoneInfo(TARGET_TIMEZONE))


@dataclass(frozen=True)
class ReconstructedSources:
    codex_root: Path
    claude_root: Path

    @property
    def source_specs(self) -> tuple[SourceSpec, ...]:
        return (
            SourceSpec(source="codex", root=self.codex_root),
            SourceSpec(source="claude-code", root=self.claude_root),
        )


def _no_agent_turns(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    raise AssertionError(_no_agent_turns_message())


def _no_agent_turns_message() -> str:
    return "e2e uses a file-writing phase runner, not agent turns"


def test_cli_generate_reuses_existing_workspace_from_env_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _write_reconstructed_sources(tmp_path)
    phase_runner = WritingPhaseRunner()
    factory = FakeAgentSessionFactory(script=_no_agent_turns)
    runner = CliRunner()
    reports_root = tmp_path / ".reports"
    workspace = _prepare_existing_workspace(reports_root=reports_root, sources=sources)
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: GenerateWorkspaceWorkflow(
            build_agent_factory=lambda _workspace: factory,
            build_phase_runners=lambda _factory: _all_phase_runners(phase_runner),
        ),
    )

    generate_result = runner.invoke(
        app,
        ["generate", "--date", TARGET_DATE, "--timezone", TARGET_TIMEZONE],
        env={**_source_env(sources), REPORTS_HOME_ENV: str(reports_root)},
    )
    assert generate_result.exit_code == 0, generate_result.output
    assert f"Reusing existing workspace {workspace}" in generate_result.stdout
    assert "prepare --force" in generate_result.stdout
    assert f"Wrote rendered report {workspace / 'report.md'}" in generate_result.stdout
    assert (workspace / "daily-report.json").exists()
    assert (workspace / "report.md").exists()
    assert phase_runner.events[-1] == "render"
    report_text = (workspace / "report.md").read_text(encoding="utf-8")
    assert "# No Supported Work Evidence — 2020-01-02" in report_text
    assert "Status: final" in report_text


def test_cli_generate_prepares_missing_workspace_from_env_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _write_reconstructed_sources(tmp_path)
    phase_runner = WritingPhaseRunner()
    factory = FakeAgentSessionFactory(script=_no_agent_turns)
    runner = CliRunner()
    reports_root = tmp_path / ".reports"
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: GenerateWorkspaceWorkflow(
            build_agent_factory=lambda _workspace: factory,
            build_phase_runners=lambda _factory: _all_phase_runners(phase_runner),
        ),
    )

    generate_result = runner.invoke(
        app,
        ["generate", "--date", TARGET_DATE, "--timezone", TARGET_TIMEZONE],
        env={**_source_env(sources), REPORTS_HOME_ENV: str(reports_root)},
    )

    workspace = reports_root / "work" / TARGET_DATE
    assert generate_result.exit_code == 0, generate_result.output
    assert f"Prepared workspace {workspace}" in generate_result.stdout
    assert f"Wrote rendered report {workspace / 'report.md'}" in generate_result.stdout
    assert workspace.exists()
    assert (workspace / "daily-report.json").exists()
    assert (workspace / "report.md").exists()
    assert phase_runner.events[-1] == "render"


def _prepare_existing_workspace(*, reports_root: Path, sources: ReconstructedSources) -> Path:
    target = resolve_report_target(
        date=TARGET_DATE,
        today=False,
        timezone_name=TARGET_TIMEZONE,
        now=TARGET_NOW,
    )
    result = prepare_workspace(
        target,
        reports_root=reports_root,
        source_specs=sources.source_specs,
        prepared_at=TARGET_NOW,
    )
    return result.workspace_path


def _write_reconstructed_sources(tmp_path: Path) -> ReconstructedSources:
    project_root = tmp_path / "projects" / "ReportGenerator"
    project_root.mkdir(parents=True)
    codex_root = tmp_path / "codex-sessions"
    claude_root = tmp_path / "claude-projects"
    codex_session_path = (
        codex_root
        / "2020"
        / "01"
        / "02"
        / "rollout-2020-01-02T09-12-00-01900000-0000-7000-8000-000000000001.jsonl"
    )
    claude_session_path = (
        claude_root / "-tmp-qa-ReportGenerator" / "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3.jsonl"
    )

    _write_jsonl(codex_session_path, _codex_session_records(project_root))
    _write_jsonl(claude_session_path, _claude_session_records(project_root))
    return ReconstructedSources(
        codex_root=codex_root,
        claude_root=claude_root,
    )


def _codex_session_records(project_root: Path) -> list[JsonObject]:
    return [
        {
            "timestamp": "2020-01-01T15:59:59.900Z",
            "type": "session_meta",
            "payload": {
                "id": "01900000-0000-7000-8000-000000000001",
                "timestamp": "2020-01-01T15:59:59.900Z",
                "cwd": str(project_root),
                "originator": "codex_cli_rs",
                "cli_version": "0.0.0-test",
                "source": "reconstructed-fixture",
                "model_provider": "openai",
            },
        },
        {
            "timestamp": "2020-01-01T16:00:00.000Z",
            "type": "event_msg",
            "payload": {
                "type": "turn_started",
                "turn_id": "turn-qa-001",
                "started_at": "2020-01-01T16:00:00.000Z",
            },
        },
        {
            "type": "turn_context",
            "payload": {
                "turn_id": "turn-qa-001",
                "cwd": str(project_root),
                "current_date": "2020-01-02",
                "timezone": TARGET_TIMEZONE,
            },
        },
        {
            "timestamp": "2020-01-01T16:00:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"text": "Validate the fixture.", "type": "input_text"}],
            },
        },
        {
            "timestamp": "2020-01-02T15:59:59.999Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": "Validated the reconstructed fixture behavior.",
            },
        },
        {
            "timestamp": "2020-01-02T16:00:00.000Z",
            "type": "event_msg",
            "payload": {"type": "turn_completed", "turn_id": "turn-qa-next-day"},
        },
    ]


def _claude_session_records(project_root: Path) -> list[JsonObject]:
    return [
        {
            "type": "permission-mode",
            "permissionMode": "default",
            "sessionId": "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3",
        },
        {
            "parentUuid": None,
            "isSidechain": False,
            "attachment": {"fileName": "README.md", "contentType": "text/markdown"},
            "type": "attachment",
            "uuid": "00000000-0000-4000-8000-000000000001",
            "timestamp": "2020-01-01T15:59:59.500Z",
            "userType": "external",
            "entrypoint": "cli",
            "cwd": str(project_root),
            "sessionId": "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3",
            "version": "0.0.0-test",
            "gitBranch": "qa-fixture",
        },
        {
            "parentUuid": "00000000-0000-4000-8000-000000000001",
            "isSidechain": False,
            "promptId": "prompt-qa-001",
            "type": "user",
            "message": {"role": "user", "content": "Prepare a workspace for this report day."},
            "uuid": "00000000-0000-4000-8000-000000000002",
            "timestamp": "2020-01-01T16:00:00.000Z",
            "permissionMode": "default",
            "userType": "external",
            "entrypoint": "cli",
            "cwd": str(project_root),
            "sessionId": "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3",
            "version": "0.0.0-test",
            "gitBranch": "qa-fixture",
        },
        {
            "parentUuid": "00000000-0000-4000-8000-000000000002",
            "isSidechain": False,
            "message": {
                "model": "claude-test",
                "id": "msg_qa_001",
                "type": "message",
                "role": "assistant",
                "content": "I will inspect the prepared boundary and report evidence.",
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 12},
            },
            "type": "assistant",
            "uuid": "00000000-0000-4000-8000-000000000003",
            "timestamp": "2020-01-02T03:30:00.123Z",
            "userType": "external",
            "entrypoint": "cli",
            "cwd": str(project_root),
            "sessionId": "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3",
            "version": "0.0.0-test",
            "gitBranch": "qa-fixture",
        },
        {
            "parentUuid": "00000000-0000-4000-8000-000000000003",
            "isSidechain": False,
            "promptId": "prompt-qa-tool-001",
            "type": "user",
            "message": {"role": "user", "content": "Tool result placeholder."},
            "uuid": "00000000-0000-4000-8000-000000000004",
            "timestamp": "2020-01-02T15:59:59.999Z",
            "toolUseResult": {"stdout": "ok", "stderr": "", "interrupted": False},
            "sourceToolAssistantUUID": "00000000-0000-4000-8000-000000000003",
            "userType": "external",
            "entrypoint": "cli",
            "cwd": str(project_root),
            "sessionId": "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3",
            "version": "0.0.0-test",
            "gitBranch": "qa-fixture",
        },
        {
            "parentUuid": "00000000-0000-4000-8000-000000000004",
            "isSidechain": False,
            "type": "system",
            "subtype": "summary",
            "durationMs": 1000,
            "messageCount": 4,
            "timestamp": "2020-01-02T16:00:00.000Z",
            "uuid": "00000000-0000-4000-8000-000000000005",
            "isMeta": True,
            "userType": "external",
            "entrypoint": "cli",
            "cwd": str(project_root),
            "sessionId": "3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3",
            "version": "0.0.0-test",
            "gitBranch": "qa-fixture",
        },
    ]


def _source_env(sources: ReconstructedSources) -> dict[str, str]:
    return {
        CODEX_SOURCE_ENV: str(sources.codex_root),
        CLAUDE_SOURCE_ENV: str(sources.claude_root),
    }


@dataclass
class WritingPhaseRunner:
    events: list[str] = field(default_factory=list)

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        self.events.append(task.task_id)
        for artifact in task.output_artifacts:
            output_path = workspace_path / artifact.path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if artifact.path.name == "report.md":
                output_path.write_text(
                    "\n".join(
                        [
                            f"# No Supported Work Evidence — {TARGET_DATE}",
                            "",
                            "Status: final",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
            else:
                output_path.write_text("{}\n", encoding="utf-8")
        return TaskResult(task_id=task.task_id, status="success")


def _all_phase_runners(phase_runner: PhaseRunner) -> dict[TaskKind, PhaseRunner]:
    return {
        "evidence_extraction": phase_runner,
        "project_synthesis": phase_runner,
        "daily_synthesis": phase_runner,
        "rendering": phase_runner,
    }


def _write_jsonl(path: Path, records: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records),
        encoding="utf-8",
    )
