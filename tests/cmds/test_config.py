"""Tests for the `config` bootstrap wizard and inspection commands."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import prompt_diary.cmds.config as config_cmd
from prompt_diary import paths
from prompt_diary.cli import app
from prompt_diary.config import (
    CONFIG_PATH_ENV,
    NOTION_DATABASE_ENV,
    NOTION_TOKEN_ENV,
    StoredConfig,
    load_config,
    save_config,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.notion_validate import (
    NotionDatabaseInfo,
    NotionIdentity,
)
from prompt_diary.paths import REPORTS_HOME_ENV

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


_TOKEN_REJECTED = "token rejected"
_DATABASE_REJECTED = "database rejected"
_VALID_TOKENS = frozenset({"good-token", "rotated-token"})


class _FakeValidator:
    """Accepts the tokens in ``_VALID_TOKENS`` and only the database ``good-db``."""

    def __init__(self, *, token: str) -> None:
        self._token = token

    def verify_token(self) -> NotionIdentity:
        if self._token not in _VALID_TOKENS:
            raise PromptDiaryError(_TOKEN_REJECTED)
        return NotionIdentity(
            integration_name="Prompt Diary Bot",
            workspace_name="Acme HQ",
            owner_type="workspace",
        )

    def verify_database(self, database_id: str) -> NotionDatabaseInfo:
        if database_id != "good-db":
            raise PromptDiaryError(_DATABASE_REJECTED)
        return NotionDatabaseInfo(database_id=database_id, title="Daily Report")


def _fake_factory(*, token: str) -> _FakeValidator:
    return _FakeValidator(token=token)


def _data_dir_stub(result: str) -> Callable[..., str]:
    def _stub(appname: str, *, appauthor: bool) -> str:
        assert appname == "prompt-diary"
        assert appauthor is False
        return result

    return _stub


def test_config_init_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    custom = str(tmp_path / "myreports")

    result = CliRunner().invoke(
        app, ["config", "init"], input=f"good-token\n{custom}\ngood-db\nWei Hu\n"
    )

    assert result.exit_code == 0, result.output
    assert "Saved configuration to" in result.stdout
    assert 'Notion integration "Prompt Diary Bot"' in result.stdout  # issue 3: who authenticated
    assert 'workspace "Acme HQ"' in result.stdout
    assert "owner type workspace" in result.stdout
    assert 'Connected to "Daily Report"' in result.stdout  # issue 4: the connected database name
    stored = load_config()
    assert stored.notion_api_key == "good-token"
    assert stored.notion_page_id == "good-db"
    assert stored.reports_root == custom
    assert stored.notion_reporter == "Wei Hu"  # the free-form reporter name was captured


def test_config_init_persists_verified_token_before_database_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    custom = str(tmp_path / "r")

    # Verify a token and a data folder, then send EOF at the database prompt to abort the wizard.
    result = CliRunner().invoke(app, ["config", "init"], input=f"good-token\n{custom}\n")

    assert result.exit_code != 0  # aborted before the database step completed
    stored = load_config()
    assert stored.notion_api_key == "good-token"  # the verified token was saved immediately
    assert stored.reports_root == custom  # as was the accepted data folder
    assert stored.notion_page_id is None  # the interrupted database step stored nothing


def test_config_init_clears_stale_database_when_token_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    save_config(StoredConfig(notion_api_key="good-token", notion_page_id="good-db"))
    custom = str(tmp_path / "r")

    # Rotate to a different valid token, then abort (EOF) at the database step.
    result = CliRunner().invoke(app, ["config", "init"], input=f"rotated-token\n{custom}\n")

    assert result.exit_code != 0
    stored = load_config()
    assert stored.notion_api_key == "rotated-token"  # the new token was saved
    # The database verified against the OLD token must not survive a token change + abort, so a
    # later publish cannot use the new token against a stale (possibly unintended) database.
    assert stored.notion_page_id is None
    assert stored.reports_root == custom


def test_config_init_reprompts_on_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))

    # token: bad then good; data folder: default (-> None); page: bad then good; reporter: skip.
    result = CliRunner().invoke(
        app, ["config", "init"], input="bad\ngood-token\n/stub/data\nbad-db\ngood-db\n\n"
    )

    assert result.exit_code == 0, result.output
    assert "token rejected" in result.stderr
    assert "database rejected" in result.stderr
    stored = load_config()
    assert stored.notion_api_key == "good-token"
    assert stored.notion_page_id == "good-db"
    assert stored.reports_root is None  # the per-user data dir is not pinned into the config
    assert stored.notion_reporter is None  # skipped reporter stays unset


def test_config_init_rejects_empty_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    custom = str(tmp_path / "r")

    result = CliRunner().invoke(
        app, ["config", "init"], input=f"\ngood-token\n{custom}\ngood-db\n\n"
    )

    assert result.exit_code == 0, result.output
    assert "A value is required." in result.stderr


def test_config_init_keeps_current_on_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    save_config(
        StoredConfig(notion_api_key="good-token", notion_page_id="good-db", reports_root="/old")
    )

    result = CliRunner().invoke(app, ["config", "init"], input="\n\n\n\n")  # keep every value

    assert result.exit_code == 0, result.output
    assert "good-token" not in result.output  # the stored token is never echoed back in the prompt
    stored = load_config()
    assert stored == StoredConfig(
        notion_api_key="good-token", notion_page_id="good-db", reports_root="/old"
    )


def test_config_init_keeps_reporter_on_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    save_config(
        StoredConfig(
            notion_api_key="good-token",
            notion_page_id="good-db",
            reports_root="/old",
            notion_reporter="Wei Hu",
        )
    )

    result = CliRunner().invoke(app, ["config", "init"], input="\n\n\n\n")  # keep every value

    assert result.exit_code == 0, result.output
    stored = load_config()
    assert stored.notion_reporter == "Wei Hu"  # enter keeps the stored reporter name


def test_config_init_keeps_the_token_out_of_traceback_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Should an unexpected error mid-wizard be rendered by a locals-capturing traceback, the
    # just-entered token must not surface: config_init holds it only as a Secret and the config's
    # repr is redacted. Fail the first save (right after the token is accepted) so that frame is on
    # the stack with the token live.
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)

    def explode(config: StoredConfig) -> Path:
        del config
        raise RuntimeError("boom")  # not a PromptDiaryError, so it escapes config_init's handler

    monkeypatch.setattr(config_cmd, "save_config", explode)

    result = CliRunner().invoke(app, ["config", "init"], input="good-token\n")

    error = result.exception
    assert error is not None
    # Inspect the wizard's *own* frame, not the whole stack: the CliRunner harness deliberately
    # retains the fed input string, which is a test artifact, whereas config_init's locals are the
    # real concern — it must not hold a bare token across the later, fallible save/prompt steps.
    summary = traceback.StackSummary.extract(
        traceback.walk_tb(error.__traceback__), capture_locals=True
    )
    wizard_frames = [frame for frame in summary if frame.name == "config_init"]
    assert wizard_frames  # the wizard frame is on the failing stack
    assert all(
        "good-token" not in value
        for frame in wizard_frames
        for value in (frame.locals or {}).values()
    )  # token held as a Secret, config repr redacted: neither local renders the raw token


def test_config_init_exits_on_corrupt_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "config.json"
    target.write_bytes(b"{ broken")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(target))

    result = CliRunner().invoke(app, ["config", "init"], input="")

    assert result.exit_code == 2
    assert "invalid" in result.stderr


def test_config_show_masks_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    save_config(
        StoredConfig(notion_api_key="supersecrettoken", notion_page_id="db-1", reports_root="/data")
    )

    result = CliRunner().invoke(app, ["config", "show"])

    assert result.exit_code == 0, result.output
    assert "supersecrettoken" not in result.output  # the token is masked, never printed
    assert "set (16 chars)" in result.stdout
    assert "db-1" in result.stdout
    assert "/data" in result.stdout
    assert "override" not in result.stdout


def test_config_show_displays_reporter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    save_config(StoredConfig(notion_reporter="Wei Hu"))

    result = CliRunner().invoke(app, ["config", "show"])

    assert result.exit_code == 0, result.output
    assert "Wei Hu" in result.stdout  # the reporter name is shown (it is not a secret)


def test_config_show_notes_env_override_and_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    monkeypatch.setenv(NOTION_TOKEN_ENV, "envtok")
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)

    result = CliRunner().invoke(app, ["config", "show"])  # empty config (nothing saved)

    assert result.exit_code == 0, result.output
    assert "(unset)" in result.stdout
    assert "/stub/data (default; not configured)" in result.stdout  # resolved folder, not a label
    assert f"override the stored config: {NOTION_TOKEN_ENV}" in result.stdout


def test_config_show_exits_on_corrupt_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "config.json"
    target.write_bytes(b"nope")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(target))

    result = CliRunner().invoke(app, ["config", "show"])

    assert result.exit_code == 2
    assert "invalid" in result.stderr


def test_config_path_prints_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "cfg.json"))

    result = CliRunner().invoke(app, ["config", "path"])

    assert result.exit_code == 0, result.output
    assert str(tmp_path / "cfg.json") in result.stdout
