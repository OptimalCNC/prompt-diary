from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import prompt_diary.cli as cli_module
import prompt_diary.cmds.generate as generate_cmd
import prompt_diary.cmds.mcp as mcp_cmd
import prompt_diary.cmds.prepare as prepare_cmd
from prompt_diary import __version__
from prompt_diary.cli import app, main
from prompt_diary.errors import PromptDiaryError
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.targeting.resolve import resolve_report_target

if TYPE_CHECKING:
    from pathlib import Path

PREPARE_FAILED = "prepare failed"
GENERATE_FAILED = "generate failed"
PHASE_FAILED = "phase failed"


@dataclass
class _FakeWorkflowResult:
    messages: tuple[str, ...]


@dataclass
class _FakeWorkflow:
    pipeline_messages: tuple[str, ...] = ()
    phase_messages: tuple[str, ...] = ()
    pipeline_error: str | None = None
    phase_error: str | None = None

    def run_pipeline(
        self, *, workspace_path: Path, messages: tuple[str, ...] = (), **_kwargs: object
    ) -> _FakeWorkflowResult:
        del workspace_path
        if self.pipeline_error is not None:
            raise PromptDiaryError(self.pipeline_error)
        return _FakeWorkflowResult(messages=(*messages, *self.pipeline_messages))

    def run_phase(
        self,
        *,
        workspace_path: Path,
        phase: str,
        project_key: str | None = None,
        session_ref: str | None = None,
        **_kwargs: object,
    ) -> _FakeWorkflowResult:
        del workspace_path, phase, project_key, session_ref
        if self.phase_error is not None:
            raise PromptDiaryError(self.phase_error)
        return _FakeWorkflowResult(messages=self.phase_messages)


def test_report_help_lists_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "prepare" in result.stdout
    assert "generate" in result.stdout
    assert "mcp" in result.stdout


def test_generate_help_lists_phase_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "--help"])

    assert result.exit_code == 0
    assert "evidence" in result.stdout
    assert "project" in result.stdout
    assert "daily" in result.stdout


def test_report_version() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_prepare_error_exits_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
    ) -> None:
        del date, today, timezone_name
        raise PromptDiaryError(PREPARE_FAILED)

    monkeypatch.setattr(prepare_cmd, "resolve_report_target", raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["prepare", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PREPARE_FAILED}\n"


def test_generate_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_generate_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
        **_kwargs: object,
    ) -> tuple[Path, tuple[str, ...]]:
        del date, today, timezone_name
        return tmp_path, ()

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_generate_target",
        workspace_for_generate_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(pipeline_error=GENERATE_FAILED),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {GENERATE_FAILED}\n"


def test_generate_prints_pipeline_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_generate_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
        **_kwargs: object,
    ) -> tuple[Path, tuple[str, ...]]:
        del date, today, timezone_name
        return tmp_path, ("prepared",)

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_generate_target",
        workspace_for_generate_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(pipeline_messages=("generated",)),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "--date", "2026-05-12"])

    assert result.exit_code == 0
    assert result.stdout == "prepared\ngenerated\n"


def test_generate_notion_flag_appends_publish_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The preflight reads these before the pipeline; set them so the happy path proceeds.
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")

    def workspace_for_generate_target(
        *, date: str | None, today: bool, timezone_name: str | None, **_kwargs: object
    ) -> tuple[Path, tuple[str, ...]]:
        del date, today, timezone_name
        return tmp_path, ("prepared",)

    published_for: list[Path] = []

    def publish_report_to_notion(workspace_path: Path, **_kwargs: object) -> tuple[str, ...]:
        published_for.append(workspace_path)
        return ("Published report to Notion: https://notion.so/x",)

    monkeypatch.setattr(
        generate_cmd, "workspace_for_generate_target", workspace_for_generate_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(pipeline_messages=("generated",)),
    )
    monkeypatch.setattr(generate_cmd, "publish_report_to_notion", publish_report_to_notion)

    result = CliRunner().invoke(app, ["generate", "--date", "2026-05-12", "--notion"])

    assert result.exit_code == 0
    # The publish message is appended after the pipeline messages, and it published the workspace.
    assert result.stdout == "prepared\ngenerated\nPublished report to Notion: https://notion.so/x\n"
    assert published_for == [tmp_path]


def test_generate_notion_flag_fails_fast_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_PAGE_ID", raising=False)

    def must_not_run(**_kwargs: object) -> tuple[Path, tuple[str, ...]]:
        raise AssertionError  # the preflight must reject before the pipeline starts

    monkeypatch.setattr(generate_cmd, "workspace_for_generate_target", must_not_run)

    result = CliRunner().invoke(app, ["generate", "--date", "2026-05-12", "--notion"])

    # Missing config is rejected cleanly (no traceback) before any pipeline work begins.
    assert result.exit_code == 2
    assert "NOTION_API_KEY" in result.stderr


def test_generate_phase_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
        **_kwargs: object,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_error=PHASE_FAILED),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "generate",
            "evidence",
            "--date",
            "2026-05-12",
            "--project-key",
            "Project-123",
            "--session-ref",
            "S0001",
        ],
    )

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PHASE_FAILED}\n"


def test_generate_project_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
        **_kwargs: object,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_error=PHASE_FAILED),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["generate", "project", "--date", "2026-05-12", "--project-key", "Project-123"],
    )

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PHASE_FAILED}\n"


def test_generate_daily_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
        **_kwargs: object,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_error=PHASE_FAILED),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "daily", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert result.stderr == f"Error: {PHASE_FAILED}\n"


def test_generate_phase_commands_delegate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str | None, str | None]] = []

    @dataclass
    class _RecordingWorkflow:
        def run_pipeline(
            self, *, workspace_path: Path, messages: tuple[str, ...] = (), **_kwargs: object
        ) -> _FakeWorkflowResult:
            del workspace_path
            return _FakeWorkflowResult(messages=messages)

        def run_phase(
            self,
            *,
            workspace_path: Path,
            phase: str,
            project_key: str | None = None,
            session_ref: str | None = None,
            **_kwargs: object,
        ) -> _FakeWorkflowResult:
            del workspace_path
            calls.append((phase, project_key, session_ref))
            return _FakeWorkflowResult(messages=("completed",))

    def workspace_for_existing_target(
        *,
        date: str | None,
        today: bool,
        timezone_name: str | None,
        **_kwargs: object,
    ) -> Path:
        del date, today, timezone_name
        return tmp_path

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(generate_cmd, "build_generation_workflow", _RecordingWorkflow)
    runner = CliRunner()

    evidence = runner.invoke(
        app,
        [
            "generate",
            "evidence",
            "--date",
            "2026-05-12",
            "--project-key",
            "Project-123",
            "--session-ref",
            "S0001",
        ],
    )
    project = runner.invoke(
        app,
        ["generate", "project", "--date", "2026-05-12", "--project-key", "Project-123"],
    )
    daily = runner.invoke(app, ["generate", "daily", "--date", "2026-05-12"])

    assert evidence.exit_code == 0
    assert project.exit_code == 0
    assert daily.exit_code == 0
    assert calls == [
        ("evidence", "Project-123", "S0001"),
        ("project", "Project-123", None),
        ("daily", None, None),
    ]


def test_prepare_reports_root_flag_wins_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMPT_DIARY_CODEX_SESSIONS", "")
    monkeypatch.setenv("PROMPT_DIARY_CLAUDE_PROJECTS", "")
    monkeypatch.setenv("PROMPT_DIARY_HOME", str(tmp_path / "env"))
    flag_root = tmp_path / "flag"

    result = CliRunner().invoke(
        app,
        [
            "prepare",
            "--date",
            "2026-05-12",
            "--timezone",
            "UTC",
            "--quiet",
            "--reports-root",
            str(flag_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (flag_root / "work" / "2026-05-12").exists()
    assert not (tmp_path / "env").exists()


def test_prepare_uses_reports_home_env_without_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMPT_DIARY_CODEX_SESSIONS", "")
    monkeypatch.setenv("PROMPT_DIARY_CLAUDE_PROJECTS", "")
    env_root = tmp_path / "env"
    monkeypatch.setenv("PROMPT_DIARY_HOME", str(env_root))

    result = CliRunner().invoke(
        app,
        ["prepare", "--date", "2026-05-12", "--timezone", "UTC", "--quiet"],
    )

    assert result.exit_code == 0, result.output
    assert (env_root / "work" / "2026-05-12").exists()


def test_generate_phase_reports_root_flag_positions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Path] = []

    def workspace_for_existing_target(*, reports_root: Path, **_kwargs: object) -> Path:
        captured.append(reports_root)
        return tmp_path

    monkeypatch.setattr(
        generate_cmd, "workspace_for_existing_target", workspace_for_existing_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_messages=("done",)),
    )
    # A competing PROMPT_DIARY_HOME must lose to an explicit --reports-root in either position.
    monkeypatch.setenv("PROMPT_DIARY_HOME", str(tmp_path / "env"))
    group_root = tmp_path / "group"
    sub_root = tmp_path / "sub"
    phase_args = ["--date", "2026-05-12", "--project-key", "Project-123", "--session-ref", "S0001"]

    group_first = CliRunner().invoke(
        app, ["generate", "--reports-root", str(group_root), "evidence", *phase_args]
    )
    sub_after = CliRunner().invoke(
        app, ["generate", "evidence", *phase_args, "--reports-root", str(sub_root)]
    )

    assert group_first.exit_code == 0, group_first.output
    assert sub_after.exit_code == 0, sub_after.output
    # The group-level flag (before the subcommand) and the subcommand-level flag both reach the
    # workspace resolver, beating the env default.
    assert captured == [group_root, sub_root]


def test_generate_existing_workspace_resolution(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
    )
    prepared = prepare_workspace(target, reports_root=reports_root, source_specs=())

    workspace_path = generate_cmd.workspace_for_existing_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        reports_root=reports_root,
    )

    assert workspace_path == prepared.workspace_path


def test_generate_existing_workspace_resolution_requires_workspace(tmp_path: Path) -> None:
    with pytest.raises(PromptDiaryError, match="run prepare first"):
        generate_cmd.workspace_for_existing_target(
            date="2026-05-12",
            today=False,
            timezone_name="Asia/Shanghai",
            reports_root=tmp_path / ".reports",
        )


def test_mcp_serve_delegates_to_server(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_serve_mcp_server() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(mcp_cmd, "serve_mcp_server", fake_serve_mcp_server)
    runner = CliRunner()

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0
    assert called


def test_codex_command_is_not_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["codex", "--help"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_main_invokes_app(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "app", fake_app)

    main()

    assert called
