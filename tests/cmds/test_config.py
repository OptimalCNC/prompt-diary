"""Tests for the `config` bootstrap wizard and inspection commands."""

from __future__ import annotations

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
from prompt_diary.paths import REPORTS_HOME_ENV

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


_TOKEN_REJECTED = "token rejected"
_DATABASE_REJECTED = "database rejected"


class _FakeValidator:
    """Accepts only the token ``good-token`` and the database ``good-db``."""

    def __init__(self, *, token: str) -> None:
        self._token = token

    def verify_token(self) -> None:
        if self._token != "good-token":
            raise PromptDiaryError(_TOKEN_REJECTED)

    def verify_database(self, database_id: str) -> None:
        if database_id != "good-db":
            raise PromptDiaryError(_DATABASE_REJECTED)


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

    result = CliRunner().invoke(app, ["config", "init"], input=f"good-token\n{custom}\ngood-db\n")

    assert result.exit_code == 0, result.output
    assert "Saved configuration to" in result.stdout
    stored = load_config()
    assert stored.notion_api_key == "good-token"
    assert stored.notion_page_id == "good-db"
    assert stored.reports_root == custom


def test_config_init_reprompts_on_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))

    # token: bad then good; data folder: accept the default (-> None); page: bad then good.
    result = CliRunner().invoke(
        app, ["config", "init"], input="bad\ngood-token\n/stub/data\nbad-db\ngood-db\n"
    )

    assert result.exit_code == 0, result.output
    assert "token rejected" in result.stderr
    assert "database rejected" in result.stderr
    stored = load_config()
    assert stored.notion_api_key == "good-token"
    assert stored.notion_page_id == "good-db"
    assert stored.reports_root is None  # the per-user data dir is not pinned into the config


def test_config_init_rejects_empty_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    custom = str(tmp_path / "r")

    result = CliRunner().invoke(app, ["config", "init"], input=f"\ngood-token\n{custom}\ngood-db\n")

    assert result.exit_code == 0, result.output
    assert "A value is required." in result.stderr


def test_config_init_keeps_current_on_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_cmd, "build_notion_validator", _fake_factory)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    save_config(
        StoredConfig(notion_api_key="good-token", notion_page_id="good-db", reports_root="/old")
    )

    result = CliRunner().invoke(app, ["config", "init"], input="\n\n\n")  # keep every current value

    assert result.exit_code == 0, result.output
    assert "good-token" not in result.output  # the stored token is never echoed back in the prompt
    stored = load_config()
    assert stored == StoredConfig(
        notion_api_key="good-token", notion_page_id="good-db", reports_root="/old"
    )


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


def test_config_show_notes_env_override_and_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "envtok")
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)

    result = CliRunner().invoke(app, ["config", "show"])  # empty config (nothing saved)

    assert result.exit_code == 0, result.output
    assert "(unset)" in result.stdout
    assert "default: per-user data dir" in result.stdout
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
