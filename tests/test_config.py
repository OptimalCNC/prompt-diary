"""Tests for the persistent config store and setting resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from prompt_diary import config, paths
from prompt_diary.config import (
    CONFIG_PATH_ENV,
    NOTION_DATABASE_ENV,
    NOTION_TOKEN_ENV,
    StoredConfig,
    config_path,
    load_config,
    notion_is_configured,
    resolve_notion_credentials,
    resolve_reports_root,
    save_config,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.paths import REPORTS_HOME_ENV

if TYPE_CHECKING:
    from collections.abc import Callable


def _dir_stub(result: str) -> Callable[..., str]:
    """Build a ``platformdirs`` stand-in that asserts its call contract."""

    def _stub(appname: str, *, appauthor: bool) -> str:
        assert appname == "prompt-diary"
        assert appauthor is False
        return result

    return _stub


# --- config_path ---------------------------------------------------------------------------------


def test_config_path_uses_env_override_and_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, "~/cfg/diary.json")
    assert config_path() == Path.home() / "cfg" / "diary.json"


def test_config_path_falls_back_to_user_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, "   ")  # blank is treated as unset
    monkeypatch.setattr(config.platformdirs, "user_config_dir", _dir_stub("/stub/cfg"))
    assert config_path() == Path("/stub/cfg/config.json")


# --- load_config / save_config -------------------------------------------------------------------


def test_load_config_returns_empty_when_missing() -> None:
    # The autouse fixture points PROMPT_DIARY_CONFIG at a path that does not exist yet.
    assert load_config() == StoredConfig()


def test_save_then_load_roundtrips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "nested" / "config.json"))
    stored = StoredConfig(reports_root="/data", notion_api_key="tok", notion_page_id="db")
    written = save_config(stored)
    assert written == tmp_path / "nested" / "config.json"
    assert written.exists()
    assert written.stat().st_mode & 0o777 == 0o600
    assert load_config() == stored


def test_save_config_replaces_existing_loose_file_with_0600(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"notion_api_key": "old"}', encoding="utf-8")
    target.chmod(0o644)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(target))
    save_config(StoredConfig(notion_page_id="db"))
    # Atomic replace yields a 0600 file regardless of prior mode, content fully replaced.
    assert target.stat().st_mode & 0o777 == 0o600
    assert load_config() == StoredConfig(notion_page_id="db")


def test_save_config_cleans_up_temp_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "config.json"))

    def _raise_oserror(self: Path, target: object) -> None:
        del self, target
        raise OSError("boom")

    monkeypatch.setattr(Path, "replace", _raise_oserror)
    with pytest.raises(PromptDiaryError, match="failed to write"):
        save_config(StoredConfig(notion_api_key="tok"))
    assert list(tmp_path.glob(".config-*")) == []  # the temp file was removed


def test_load_config_raises_on_corrupt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "config.json"
    target.write_bytes(b"{ not json")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(target))
    with pytest.raises(PromptDiaryError, match="invalid"):
        load_config()


# --- resolve_reports_root ------------------------------------------------------------------------


def test_resolve_reports_root_prefers_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REPORTS_HOME_ENV, "/env/root")
    assert resolve_reports_root(Path("~/explicit")) == Path.home() / "explicit"


def test_resolve_reports_root_uses_env_over_config(monkeypatch: pytest.MonkeyPatch) -> None:
    save_config(StoredConfig(reports_root="/config/root"))
    monkeypatch.setenv(REPORTS_HOME_ENV, "  ~/env-root  ")
    assert resolve_reports_root(None) == Path.home() / "env-root"


def test_resolve_reports_root_uses_config_when_no_flag_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    save_config(StoredConfig(reports_root="~/config-root"))
    assert resolve_reports_root(None) == Path.home() / "config-root"


def test_resolve_reports_root_falls_back_to_platform_data_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _dir_stub("/stub/data"))
    assert resolve_reports_root(None) == Path("/stub/data")


# --- resolve_notion_credentials ------------------------------------------------------------------


def test_resolve_notion_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")
    monkeypatch.setenv(NOTION_DATABASE_ENV, "env-db")
    assert resolve_notion_credentials() == ("env-tok", "env-db")


def test_resolve_notion_credentials_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    assert resolve_notion_credentials() == ("cfg-tok", "cfg-db")


def test_resolve_notion_credentials_env_overrides_config_per_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    assert resolve_notion_credentials() == ("env-tok", "cfg-db")


def test_resolve_notion_credentials_config_token_with_env_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.setenv(NOTION_DATABASE_ENV, "env-db")
    assert resolve_notion_credentials() == ("cfg-tok", "env-db")


def test_resolve_notion_credentials_blank_env_falls_back_to_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    monkeypatch.setenv(NOTION_TOKEN_ENV, "   ")
    monkeypatch.setenv(NOTION_DATABASE_ENV, "  ")
    assert resolve_notion_credentials() == ("cfg-tok", "cfg-db")


def test_resolve_notion_credentials_missing_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    with pytest.raises(PromptDiaryError, match="NOTION_API_KEY"):
        resolve_notion_credentials()


def test_resolve_notion_credentials_missing_database_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    with pytest.raises(PromptDiaryError, match="credentials"):
        resolve_notion_credentials()


# --- notion_is_configured ------------------------------------------------------------------------


def test_notion_is_configured_true_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")
    monkeypatch.setenv(NOTION_DATABASE_ENV, "env-db")
    assert notion_is_configured() is True


def test_notion_is_configured_true_from_stored_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    assert notion_is_configured() is True


def test_notion_is_configured_false_when_database_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    save_config(StoredConfig(notion_api_key="cfg-tok"))  # token only, no database id
    assert notion_is_configured() is False
