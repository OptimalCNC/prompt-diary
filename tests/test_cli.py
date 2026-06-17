from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

import click
import pytest
import typer
from typer.testing import CliRunner

import prompt_diary.cli as cli_module
import prompt_diary.cmds.common as common_cmd
import prompt_diary.cmds.generate as generate_cmd
import prompt_diary.cmds.mcp as mcp_cmd
import prompt_diary.paths as paths_module
import prompt_diary.targeting.resolve as targets_module
from prompt_diary import __version__
from prompt_diary.cli import app, main
from prompt_diary.config import NOTION_DATABASE_ENV, NOTION_TOKEN_ENV, StoredConfig, save_config
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.rendering import NotionRenderResult
from prompt_diary.paths import REPORTS_HOME_ENV
from prompt_diary.prepare.workspace import prepare_workspace
from prompt_diary.progress.events import PhaseFinished, PhaseStarted
from prompt_diary.secret import Secret
from prompt_diary.targeting.resolve import TIMEZONE_ENV_VARS, resolve_report_target

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.progress.reporter import ProgressReporter

PREPARE_FAILED = "prepare failed"
GENERATE_FAILED = "generate failed"
PHASE_FAILED = "phase failed"
NO_NOTION_RENDER_FAILED = "render notion must not run with --no-notion"
FULL_GENERATE_PHASE_FAILED = "full generate must not call run_phase"
DIRECT_WORKSPACE_PREPARE_FAILED = "direct workspace generation must not prepare a workspace"
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _one_line(text: str) -> str:
    plain = ANSI_ESCAPE_PATTERN.sub("", text)
    plain = "".join(char if char.isascii() else " " for char in plain)
    return " ".join(plain.split())


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

    result = runner.invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert "generate [OPTIONS] [COMMAND] [ARGS]..." in help_text
    assert "evidence" in result.stdout
    assert "project" in result.stdout
    assert "daily" in result.stdout
    assert "render" in result.stdout


def test_generate_help_shows_effective_default_targeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports_root = "help-reports"
    monkeypatch.setenv(TIMEZONE_ENV_VARS[0], "Asia/Shanghai")
    monkeypatch.setenv(TIMEZONE_ENV_VARS[1], "UTC")
    monkeypatch.setenv(REPORTS_HOME_ENV, reports_root)
    before = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    after = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    expected_dates = {before.isoformat(), after.isoformat()}
    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert any(
        (
            f"Defaults to {expected} (yesterday in Asia/Shanghai) when neither --date nor "
            "--today is passed"
        )
        in help_text
        for expected in expected_dates
    )
    assert (
        "Defaults to Asia/Shanghai from $PROMPT_DIARY_TIMEZONE. $TZ is set but not used "
        "because $PROMPT_DIARY_TIMEZONE takes precedence" in help_text
    )
    assert (
        "Defaults to help-reports from $PROMPT_DIARY_HOME (relative to the current working "
        "directory)"
    ) in help_text


def test_generate_help_explains_blank_lower_priority_timezone_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TIMEZONE_ENV_VARS[0], "Asia/Shanghai")
    monkeypatch.setenv(TIMEZONE_ENV_VARS[1], " ")

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Defaults to Asia/Shanghai from $PROMPT_DIARY_TIMEZONE. $TZ is set but blank and is not "
        "used because $PROMPT_DIARY_TIMEZONE takes precedence"
    ) in help_text


def test_generate_help_shows_unset_environment_default_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def user_data_dir(appname: str, *, appauthor: bool) -> str:
        assert appname == "prompt-diary"
        assert appauthor is False
        return "/pd-data"

    for env_var in (*TIMEZONE_ENV_VARS, REPORTS_HOME_ENV):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(targets_module, "_system_timezone_name", lambda: None)
    monkeypatch.setattr(paths_module.platformdirs, "user_data_dir", user_data_dir)

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Defaults to UTC because no valid timezone override is set and no system timezone was "
        "detected. $PROMPT_DIARY_TIMEZONE is unset. $TZ is unset" in help_text
    )
    assert (
        "Defaults to /pd-data from the per-user data directory. $PROMPT_DIARY_HOME is unset "
        "and no reports_root is stored in config"
    ) in help_text


def test_generate_help_explains_blank_timezone_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TIMEZONE_ENV_VARS[0], " ")
    monkeypatch.delenv(TIMEZONE_ENV_VARS[1], raising=False)
    monkeypatch.setattr(targets_module, "_system_timezone_name", lambda: None)

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Defaults to UTC because no valid timezone override is set and no system timezone was "
        "detected. $PROMPT_DIARY_TIMEZONE is set but blank. $TZ is unset"
    ) in help_text


def test_generate_help_explains_system_timezone_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_var in TIMEZONE_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(targets_module, "_system_timezone_name", lambda: "Asia/Shanghai")

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Defaults to Asia/Shanghai from the system timezone. $PROMPT_DIARY_TIMEZONE is unset. "
        "$TZ is unset"
    ) in help_text


def test_generate_help_explains_timezone_environment_that_is_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TIMEZONE_ENV_VARS[0], ":posix/UTC")
    monkeypatch.delenv(TIMEZONE_ENV_VARS[1], raising=False)
    monkeypatch.setattr(targets_module, "_system_timezone_name", lambda: None)

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Defaults to UTC because no valid timezone override is set and no system timezone was "
        "detected. $PROMPT_DIARY_TIMEZONE is not used because it is POSIX TZ syntax (:posix/UTC), "
        "not an IANA timezone name. $TZ is unset"
    ) in help_text


def test_generate_help_explains_invalid_timezone_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TIMEZONE_ENV_VARS[0], "Not/AZone")
    monkeypatch.setenv(TIMEZONE_ENV_VARS[1], "UTC")

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "No default timezone is available because $PROMPT_DIARY_TIMEZONE=Not/AZone is not a known "
        "IANA timezone name. $TZ is set but not used because $PROMPT_DIARY_TIMEZONE takes "
        "precedence. Running without --timezone will error. Pass --timezone or fix "
        "$PROMPT_DIARY_TIMEZONE"
    ) in help_text
    assert (
        "If neither --date nor --today is passed, the default date cannot be computed until a "
        "valid timezone default is available"
    ) in help_text


def test_generate_help_explains_invalid_secondary_timezone_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TIMEZONE_ENV_VARS[0], raising=False)
    monkeypatch.setenv(TIMEZONE_ENV_VARS[1], "Not/AZone")

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "No default timezone is available because $TZ=Not/AZone is not a known IANA timezone name. "
        "$PROMPT_DIARY_TIMEZONE is unset. Running without --timezone will error. Pass --timezone "
        "or fix $TZ"
    ) in help_text
    assert (
        "If neither --date nor --today is passed, the default date cannot be computed until a "
        "valid timezone default is available"
    ) in help_text


def test_generate_help_shows_stored_config_reports_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    save_config(StoredConfig(reports_root="stored-reports"))

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Defaults to stored-reports from stored config (relative to the current working "
        "directory). $PROMPT_DIARY_HOME is unset"
    ) in help_text


def test_generate_help_explains_blank_reports_root_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def user_data_dir(appname: str, *, appauthor: bool) -> str:
        assert appname == "prompt-diary"
        assert appauthor is False
        return "/pd-data"

    monkeypatch.setenv(REPORTS_HOME_ENV, " ")
    monkeypatch.setattr(paths_module.platformdirs, "user_data_dir", user_data_dir)

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Defaults to /pd-data from the per-user data directory. $PROMPT_DIARY_HOME is set but "
        "blank and no reports_root is stored in config"
    ) in help_text


def test_generate_phase_help_shows_effective_default_targeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TIMEZONE_ENV_VARS[0], "Asia/Shanghai")
    monkeypatch.delenv(TIMEZONE_ENV_VARS[1], raising=False)
    monkeypatch.setenv(REPORTS_HOME_ENV, "phase-help-reports")

    result = CliRunner().invoke(app, ["generate", "render", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert "Defaults to Asia/Shanghai from $PROMPT_DIARY_TIMEZONE. $TZ is unset" in help_text
    assert (
        "Defaults to phase-help-reports from $PROMPT_DIARY_HOME (relative to the current working "
        "directory)"
    ) in help_text


def test_generate_help_shows_default_notion_publish_from_config() -> None:
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Default now: publish because the Notion token and database id are stored in config. "
        "Pass --no-notion to skip" in help_text
    )


def test_generate_help_shows_default_notion_skip_when_missing() -> None:
    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Default now: do not publish because no Notion token or database id resolves. "
        "If --notion is passed now, it will error" in help_text
    )


def test_generate_help_shows_default_notion_publish_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")
    monkeypatch.setenv(NOTION_DATABASE_ENV, "env-db")

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Default now: publish because $NOTION_API_KEY and $NOTION_PAGE_ID are set. "
        "Pass --no-notion to skip" in help_text
    )


def test_generate_help_shows_default_notion_publish_from_mixed_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(StoredConfig(notion_page_id="cfg-db"))
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Default now: publish because the Notion token is from $NOTION_API_KEY and the database "
        "id is stored in config. Pass --no-notion to skip"
    ) in help_text


def test_generate_help_shows_default_notion_skip_when_database_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")

    result = CliRunner().invoke(app, ["generate", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Default now: do not publish because no database id resolves. If --notion is passed now, "
        "it will error" in help_text
    )


def test_generate_render_help_shows_default_notion_publish_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")
    monkeypatch.setenv(NOTION_DATABASE_ENV, "env-db")

    result = CliRunner().invoke(app, ["generate", "render", "--help"], terminal_width=220)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert (
        "Default now: publish because $NOTION_API_KEY and $NOTION_PAGE_ID are set. "
        "Pass --no-notion to skip"
    ) in help_text


def test_refresh_dynamic_default_help_ignores_click_arguments() -> None:
    argument = click.Argument(["name"])

    common_cmd.refresh_dynamic_default_help([argument])

    assert argument.name == "name"


def test_workspace_target_command_help_lists_shared_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    local_app = typer.Typer(add_completion=False)

    def callback() -> None:
        pass

    local_app.callback()(callback)

    def handler(*, target_options: common_cmd.CliWorkspaceTargetOptions) -> None:
        del target_options

    common_cmd.workspace_target_command(local_app, handler, name="demo")

    result = CliRunner().invoke(local_app, ["demo", "--help"], terminal_width=180)

    help_text = _one_line(result.stdout)
    assert result.exit_code == 0
    assert "--date" in help_text
    assert "--today" in help_text
    assert "--timezone" in help_text
    assert "--reports-root" in help_text


def test_workspace_target_command_can_include_workspace_flag(tmp_path: Path) -> None:
    local_app = typer.Typer(add_completion=False)
    captured: list[common_cmd.CliWorkspaceTargetOptions] = []

    def callback() -> None:
        pass

    local_app.callback()(callback)

    def handler(*, target_options: common_cmd.CliWorkspaceTargetOptions) -> None:
        captured.append(target_options)

    common_cmd.workspace_target_command(local_app, handler, name="demo", include_workspace=True)
    workspace = tmp_path / "prepared-workspace"

    result = CliRunner().invoke(local_app, ["demo", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert captured == [
        common_cmd.CliWorkspaceTargetOptions(
            report_target=common_cmd.CliReportTargetOptions(
                date=None,
                today=False,
                timezone=None,
            ),
            reports_root=None,
            workspace=workspace,
        )
    ]


def test_prepare_help_does_not_list_workspace() -> None:
    result = CliRunner().invoke(app, ["prepare", "--help"], terminal_width=180)

    assert result.exit_code == 0
    assert "--workspace" not in _one_line(result.stdout)


def test_workspace_target_command_passes_single_options_object(tmp_path: Path) -> None:
    local_app = typer.Typer(add_completion=False)
    captured: list[common_cmd.CliWorkspaceTargetOptions] = []

    def callback() -> None:
        pass

    local_app.callback()(callback)

    def handler(*, target_options: common_cmd.CliWorkspaceTargetOptions) -> None:
        captured.append(target_options)

    common_cmd.workspace_target_command(local_app, handler, name="demo")

    result = CliRunner().invoke(
        local_app,
        [
            "demo",
            "--date",
            "2026-05-12",
            "--timezone",
            "UTC",
            "--reports-root",
            str(tmp_path / "reports"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == [
        common_cmd.CliWorkspaceTargetOptions(
            report_target=common_cmd.CliReportTargetOptions(
                date="2026-05-12",
                today=False,
                timezone="UTC",
            ),
            reports_root=tmp_path / "reports",
        )
    ]


def test_workspace_target_command_requires_target_options_parameter() -> None:
    local_app = typer.Typer(add_completion=False)

    def handler() -> None:
        pass

    with pytest.raises(ValueError, match="target_options"):
        common_cmd.workspace_target_command(local_app, handler, name="demo")


def test_workspace_target_callback_signature_lists_shared_flags() -> None:
    local_app = typer.Typer(add_completion=False, invoke_without_command=True)
    captured: list[common_cmd.CliWorkspaceTargetOptions] = []

    def callback(*, target_options: common_cmd.CliWorkspaceTargetOptions) -> None:
        captured.append(target_options)

    wrapper = common_cmd.workspace_target_callback(local_app, callback)
    parameter_names = tuple(inspect.signature(wrapper).parameters)

    result = CliRunner().invoke(local_app, ["--today", "--timezone", "UTC"])

    assert result.exit_code == 0, result.output
    assert parameter_names == ("date", "today", "timezone", "reports_root")
    assert captured == [
        common_cmd.CliWorkspaceTargetOptions(
            report_target=common_cmd.CliReportTargetOptions(
                date=None,
                today=True,
                timezone="UTC",
            ),
            reports_root=None,
        )
    ]


def test_generate_render_accepts_notion_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_PAGE_ID", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["generate", "render", "--notion", "--date", "2026-05-12"])

    assert result.exit_code == 2
    assert "--notion was given" in result.stderr


def test_report_version() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_prepare_error_exits_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(
        self: common_cmd.CliWorkspaceTargetOptions,
        *,
        now: datetime | None = None,
    ) -> common_cmd.ResolvedCliWorkspaceTarget:
        del self, now
        raise PromptDiaryError(PREPARE_FAILED)

    monkeypatch.setattr(common_cmd.CliWorkspaceTargetOptions, "resolve", raise_error)
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
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> tuple[Path, tuple[str, ...]]:
        del target_options
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
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> tuple[Path, tuple[str, ...]]:
        del target_options
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


def test_generate_prints_timing_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_generate_target(
        *,
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> tuple[Path, tuple[str, ...]]:
        del target_options
        return tmp_path, ("prepared",)

    class TimingWorkflow(_FakeWorkflow):
        def run_pipeline(
            self, *, workspace_path: Path, messages: tuple[str, ...] = (), **kwargs: object
        ) -> _FakeWorkflowResult:
            reporter = cast("ProgressReporter", kwargs["reporter"])
            reporter.emit(PhaseStarted(at=1.0, phase_id="evidence", label="evidence"))
            reporter.emit(PhaseFinished(at=3.25, phase_id="evidence", status="success"))
            return super().run_pipeline(workspace_path=workspace_path, messages=messages, **kwargs)

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_generate_target",
        workspace_for_generate_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: TimingWorkflow(pipeline_messages=("generated",)),
    )

    result = CliRunner().invoke(app, ["generate", "--date", "2026-05-12"])

    assert result.exit_code == 0
    assert result.stdout == "prepared\ngenerated\nSpent 2.2s evidence.\n"


def test_generate_notion_flag_appends_publish_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The preflight reads these before the pipeline; set them so the happy path proceeds.
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")

    def workspace_for_generate_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> tuple[Path, tuple[str, ...]]:
        del target_options
        return tmp_path, ("prepared",)

    published: list[tuple[Path, object]] = []

    def render_workspace_report_to_notion(
        workspace_path: Path, **_kwargs: object
    ) -> NotionRenderResult:
        published.append((workspace_path, _kwargs.get("credentials")))
        return NotionRenderResult(
            artifact_path=workspace_path / "report.notion.json",
            page_id="page-x",
            url="https://notion.so/x",
            warnings=("汇报人 was left empty",),
        )

    monkeypatch.setattr(
        generate_cmd, "workspace_for_generate_target", workspace_for_generate_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(pipeline_messages=("generated",)),
    )
    monkeypatch.setattr(
        generate_cmd, "render_workspace_report_to_notion", render_workspace_report_to_notion
    )

    result = CliRunner().invoke(app, ["generate", "--date", "2026-05-12", "--notion"])

    assert result.exit_code == 0
    # The publish message is appended after the pipeline messages, and it published the workspace.
    assert result.stdout == "prepared\ngenerated\nPublished report to Notion: https://notion.so/x\n"
    assert published == [(tmp_path, (Secret("tok"), "db"))]
    # A non-fatal publish warning is surfaced on stderr, without disturbing the stdout messages.
    assert "Warning: 汇报人 was left empty" in result.stderr


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


def test_generate_publishes_to_notion_by_default_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")

    def workspace_for_generate_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> tuple[Path, tuple[str, ...]]:
        del target_options
        return tmp_path, ("prepared",)

    published: list[tuple[Path, object]] = []

    def render_workspace_report_to_notion(
        workspace_path: Path, **_kwargs: object
    ) -> NotionRenderResult:
        published.append((workspace_path, _kwargs.get("credentials")))
        return NotionRenderResult(
            artifact_path=workspace_path / "report.notion.json",
            page_id="page-x",
            url="https://notion.so/x",
            warnings=(),
        )

    monkeypatch.setattr(
        generate_cmd, "workspace_for_generate_target", workspace_for_generate_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(pipeline_messages=("generated",)),
    )
    monkeypatch.setattr(
        generate_cmd, "render_workspace_report_to_notion", render_workspace_report_to_notion
    )

    result = CliRunner().invoke(app, ["generate", "--date", "2026-05-12"])  # no --notion flag

    assert result.exit_code == 0
    assert result.stdout == "prepared\ngenerated\nPublished report to Notion: https://notion.so/x\n"
    assert published == [(tmp_path, (Secret("tok"), "db"))]


def test_generate_no_notion_skips_publish_even_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")

    def workspace_for_generate_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> tuple[Path, tuple[str, ...]]:
        del target_options
        return tmp_path, ("prepared",)

    def render_must_not_run(workspace_path: Path, **_kwargs: object) -> NotionRenderResult:
        del workspace_path
        raise AssertionError(NO_NOTION_RENDER_FAILED)

    monkeypatch.setattr(
        generate_cmd, "workspace_for_generate_target", workspace_for_generate_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(pipeline_messages=("generated",)),
    )
    monkeypatch.setattr(generate_cmd, "render_workspace_report_to_notion", render_must_not_run)

    result = CliRunner().invoke(app, ["generate", "--date", "2026-05-12", "--no-notion"])

    assert result.exit_code == 0
    assert result.stdout == "prepared\ngenerated\n"


def test_generate_workspace_runs_pipeline_against_direct_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = resolve_report_target(date="2026-05-12", today=False, timezone_name="UTC")
    prepared = prepare_workspace(target, reports_root=tmp_path / ".reports", source_specs=())
    pipeline_calls: list[tuple[Path, tuple[str, ...]]] = []

    @dataclass
    class _RecordingWorkflow:
        def run_pipeline(
            self, *, workspace_path: Path, messages: tuple[str, ...] = (), **_kwargs: object
        ) -> _FakeWorkflowResult:
            pipeline_calls.append((workspace_path, messages))
            return _FakeWorkflowResult(messages=(*messages, "generated"))

        def run_phase(self, **_kwargs: object) -> _FakeWorkflowResult:
            raise AssertionError(FULL_GENERATE_PHASE_FAILED)

    def prepare_must_not_run(**_kwargs: object) -> object:
        raise AssertionError(DIRECT_WORKSPACE_PREPARE_FAILED)

    monkeypatch.setattr(generate_cmd, "build_generation_workflow", _RecordingWorkflow)
    monkeypatch.setattr(generate_cmd, "prepare_workspace", prepare_must_not_run)

    result = CliRunner().invoke(
        app,
        ["generate", "--workspace", str(prepared.workspace_path), "--no-notion"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == f"Using prepared workspace {prepared.workspace_path}.\ngenerated\n"
    assert pipeline_calls == [
        (prepared.workspace_path, (f"Using prepared workspace {prepared.workspace_path}.",))
    ]


def test_generate_render_notion_publishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")
    rendered_for: list[Path] = []

    def workspace_for_existing_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> Path:
        del target_options
        return tmp_path

    def render_workspace_report_to_notion(
        workspace_path: Path, **_kwargs: object
    ) -> NotionRenderResult:
        rendered_for.append(workspace_path)
        return NotionRenderResult(
            artifact_path=workspace_path / "report.notion.json",
            page_id="page-1",
            url="https://notion.so/page-x",
            warnings=("汇报人 was left empty",),
        )

    monkeypatch.setattr(
        generate_cmd, "workspace_for_existing_target", workspace_for_existing_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_messages=("rendered",)),
    )
    monkeypatch.setattr(
        generate_cmd, "render_workspace_report_to_notion", render_workspace_report_to_notion
    )

    result = CliRunner().invoke(
        app,
        ["generate", "render", "--notion", "--date", "2026-05-12", "--timezone", "UTC"],
    )

    assert result.exit_code == 0
    assert result.stdout == "rendered\nPublished report to Notion: https://notion.so/page-x\n"
    assert rendered_for == [tmp_path]
    # The publish warning is surfaced on stderr, separate from the stdout publish message.
    assert "Warning: 汇报人 was left empty" in result.stderr


def test_generate_render_publishes_to_notion_by_default_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")
    rendered_for: list[tuple[Path, object]] = []

    def workspace_for_existing_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> Path:
        del target_options
        return tmp_path

    def render_workspace_report_to_notion(
        workspace_path: Path, **_kwargs: object
    ) -> NotionRenderResult:
        rendered_for.append((workspace_path, _kwargs.get("credentials")))
        return NotionRenderResult(
            artifact_path=workspace_path / "report.notion.json",
            page_id="page-1",
            url="https://notion.so/page-x",
            warnings=(),
        )

    monkeypatch.setattr(
        generate_cmd, "workspace_for_existing_target", workspace_for_existing_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_messages=("rendered",)),
    )
    monkeypatch.setattr(
        generate_cmd, "render_workspace_report_to_notion", render_workspace_report_to_notion
    )

    result = CliRunner().invoke(
        app,
        ["generate", "render", "--date", "2026-05-12", "--timezone", "UTC"],
    )

    assert result.exit_code == 0
    assert result.stdout == "rendered\nPublished report to Notion: https://notion.so/page-x\n"
    assert rendered_for == [(tmp_path, (Secret("tok"), "db"))]


def test_generate_render_no_notion_skips_publish_even_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")

    def workspace_for_existing_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> Path:
        del target_options
        return tmp_path

    def render_must_not_run(workspace_path: Path, **_kwargs: object) -> NotionRenderResult:
        del workspace_path
        raise AssertionError(NO_NOTION_RENDER_FAILED)

    monkeypatch.setattr(
        generate_cmd, "workspace_for_existing_target", workspace_for_existing_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_messages=("rendered",)),
    )
    monkeypatch.setattr(generate_cmd, "render_workspace_report_to_notion", render_must_not_run)

    result = CliRunner().invoke(
        app,
        [
            "generate",
            "render",
            "--no-notion",
            "--date",
            "2026-05-12",
            "--timezone",
            "UTC",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "rendered\n"


def test_generate_render_accepts_workspace_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "prepared-workspace"
    captured: list[Path | None] = []

    def workspace_for_existing_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> Path:
        captured.append(target_options.workspace)
        return workspace

    monkeypatch.setattr(
        generate_cmd, "workspace_for_existing_target", workspace_for_existing_target
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_messages=("rendered",)),
    )

    result = CliRunner().invoke(
        app,
        ["generate", "render", "--workspace", str(workspace), "--no-notion"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "rendered\n"
    assert captured == [workspace]


def test_generate_render_requires_existing_workspace(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "generate",
            "render",
            "--date",
            "2026-05-12",
            "--timezone",
            "UTC",
            "--reports-root",
            str(tmp_path / ".reports"),
        ],
    )

    assert result.exit_code == 2
    assert "prepared workspace is missing" in result.stderr


def test_generate_direct_workspace_requires_existing_workspace(tmp_path: Path) -> None:
    missing_workspace = tmp_path / "missing-workspace"

    result = CliRunner().invoke(
        app,
        ["generate", "--workspace", str(missing_workspace), "--no-notion"],
    )

    assert result.exit_code == 2
    assert f"prepared workspace is missing: {missing_workspace}; run prepare first" in result.stderr


def test_generate_phase_error_exits_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def workspace_for_existing_target(
        *,
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> Path:
        del target_options
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
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> Path:
        del target_options
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
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> Path:
        del target_options
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
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> Path:
        del target_options
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


def test_generate_daily_accepts_workspace_before_or_after_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "prepared-workspace"
    captured: list[Path | None] = []

    def workspace_for_existing_target(
        *,
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> Path:
        captured.append(target_options.workspace)
        return workspace

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_messages=("completed",)),
    )
    runner = CliRunner()

    after_phase = runner.invoke(app, ["generate", "daily", "--workspace", str(workspace)])
    before_phase = runner.invoke(app, ["generate", "--workspace", str(workspace), "daily"])

    assert after_phase.exit_code == 0, after_phase.output
    assert before_phase.exit_code == 0, before_phase.output
    assert captured == [workspace, workspace]


def test_generate_phase_workspace_flag_after_phase_wins_over_group_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    group_workspace = tmp_path / "group-workspace"
    subcommand_workspace = tmp_path / "subcommand-workspace"
    captured: list[Path | None] = []

    def workspace_for_existing_target(
        *,
        target_options: common_cmd.CliWorkspaceTargetOptions,
        **_kwargs: object,
    ) -> Path:
        captured.append(target_options.workspace)
        return subcommand_workspace

    monkeypatch.setattr(
        generate_cmd,
        "workspace_for_existing_target",
        workspace_for_existing_target,
    )
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: _FakeWorkflow(phase_messages=("completed",)),
    )

    result = CliRunner().invoke(
        app,
        [
            "generate",
            "--workspace",
            str(group_workspace),
            "daily",
            "--workspace",
            str(subcommand_workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == [subcommand_workspace]


def test_generate_phase_target_options_without_group_context_keeps_subcommand_options(
    tmp_path: Path,
) -> None:
    target_options = common_cmd.CliWorkspaceTargetOptions(
        report_target=common_cmd.CliReportTargetOptions(
            date="2026-05-12",
            today=False,
            timezone="UTC",
        ),
        reports_root=tmp_path / "reports",
    )
    ctx = click.Context(click.Command("generate"))

    assert generate_cmd._phase_target_options(ctx, target_options) is target_options  # pyright: ignore[reportPrivateUsage, reportArgumentType]


def test_generate_workspace_rejects_date_target_flags(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "generate",
            "--workspace",
            str(tmp_path / "prepared-workspace"),
            "--date",
            "2026-05-12",
            "--no-notion",
        ],
    )

    assert result.exit_code == 2
    assert "--workspace cannot be combined with --date" in result.stderr


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

    def workspace_for_existing_target(
        *, target_options: common_cmd.CliWorkspaceTargetOptions, **_kwargs: object
    ) -> Path:
        assert target_options.reports_root is not None
        captured.append(target_options.reports_root)
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
        target_options=common_cmd.CliWorkspaceTargetOptions(
            report_target=common_cmd.CliReportTargetOptions(
                date="2026-05-12",
                today=False,
                timezone="Asia/Shanghai",
            ),
            reports_root=reports_root,
        ),
    )

    assert workspace_path == prepared.workspace_path


def test_generate_existing_workspace_resolution_accepts_direct_workspace(tmp_path: Path) -> None:
    target = resolve_report_target(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
    )
    prepared = prepare_workspace(target, reports_root=tmp_path / ".reports", source_specs=())

    workspace_path = generate_cmd.workspace_for_existing_target(
        target_options=common_cmd.CliWorkspaceTargetOptions(
            report_target=common_cmd.CliReportTargetOptions(
                date=None,
                today=False,
                timezone=None,
            ),
            reports_root=None,
            workspace=prepared.workspace_path,
        ),
    )

    assert workspace_path == prepared.workspace_path


def test_generate_existing_workspace_resolution_requires_workspace(tmp_path: Path) -> None:
    with pytest.raises(PromptDiaryError, match="run prepare first"):
        generate_cmd.workspace_for_existing_target(
            target_options=common_cmd.CliWorkspaceTargetOptions(
                report_target=common_cmd.CliReportTargetOptions(
                    date="2026-05-12",
                    today=False,
                    timezone="Asia/Shanghai",
                ),
                reports_root=tmp_path / ".reports",
            ),
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
