"""Tests for the persistent config store and setting resolution."""

from __future__ import annotations

import traceback
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
    resolve_notion_reporter,
    resolve_reports_root,
    save_config,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.paths import REPORTS_HOME_ENV
from prompt_diary.secret import Secret

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


def test_save_config_cleans_up_temp_on_non_oserror_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A non-OSError raised after mkstemp (e.g. msgspec failing to encode a malformed field) must
    # still remove the temp file and propagate unchanged — not orphan a 0600 artifact, nor be
    # masked as a write failure (the encode now runs inside the try, after the temp file exists).
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "config.json"))

    def _raise_type_error(obj: object) -> bytes:
        del obj
        raise TypeError("boom")

    monkeypatch.setattr("msgspec.json.encode", _raise_type_error)
    with pytest.raises(TypeError, match="boom"):
        save_config(StoredConfig(notion_api_key="tok"))
    assert list(tmp_path.glob(".config-*")) == []  # no orphan temp file remains


def test_save_config_cleanup_failure_does_not_mask_the_original_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # If temp-file cleanup itself fails (e.g. the config dir became unwritable mid-save), it must
    # not mask the real failure: the original exception propagates and the cleanup error is
    # swallowed (an orphaned temp file beats a hidden error of the wrong type).
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "config.json"))

    def _raise_type_error(obj: object) -> bytes:
        del obj
        raise TypeError("boom")

    def _unlink_denied(self: Path, *, missing_ok: bool = False) -> None:
        del self, missing_ok
        raise PermissionError

    monkeypatch.setattr("msgspec.json.encode", _raise_type_error)
    monkeypatch.setattr(Path, "unlink", _unlink_denied)
    # The encode error must propagate, not the swallowed cleanup PermissionError.
    with pytest.raises(TypeError, match="boom"):
        save_config(StoredConfig(notion_api_key="tok"))


def test_save_config_failure_never_surfaces_the_token_in_traceback_locals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A save failure must not leak the token: the serialized config bytes are an anonymous argument
    # to write(), never a frame local that survives fallible I/O, and the StoredConfig local renders
    # redacted. Inspect save_config's own frames (not the test frame, which pytest's assertion
    # rewriting salts with the searched-for literal) after a failure inside the try (replace,
    # wrapped as PromptDiaryError) and outside it (mkstemp, a bare OSError that propagates).
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "config.json"))
    stored = StoredConfig(notion_api_key="supersecrettoken")

    def _save_config_locals(error: BaseException) -> list[str]:
        summary = traceback.StackSummary.extract(
            traceback.walk_tb(error.__traceback__), capture_locals=True
        )
        return [
            value
            for frame in summary
            if frame.name == "save_config"
            for value in (frame.locals or {}).values()
        ]

    def _fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("boom")

    monkeypatch.setattr(Path, "replace", _fail)  # inside the try: wrapped as PromptDiaryError
    with pytest.raises(PromptDiaryError) as wrapped:
        save_config(stored)
    inside_locals = _save_config_locals(wrapped.value)
    assert inside_locals  # save_config is on the failing stack
    assert all("supersecrettoken" not in value for value in inside_locals)

    monkeypatch.setattr("tempfile.mkstemp", _fail)  # outside the try: a bare OSError propagates
    with pytest.raises(OSError, match="boom") as bare:
        save_config(stored)
    assert all("supersecrettoken" not in value for value in _save_config_locals(bare.value))


def test_load_config_raises_on_corrupt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "config.json"
    target.write_bytes(b"{ not json")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(target))
    with pytest.raises(PromptDiaryError, match="invalid"):
        load_config()


def test_load_config_corrupt_file_never_surfaces_a_stored_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A malformed config that still contains a token must not leak it — neither in the error message
    # nor in a locals-capturing traceback (the raw file bytes are not retained across the raise).
    target = tmp_path / "config.json"
    target.write_text('{"notion_api_key":"supersecrettoken', encoding="utf-8")  # malformed + token
    monkeypatch.setenv(CONFIG_PATH_ENV, str(target))
    with pytest.raises(PromptDiaryError) as exc_info:
        load_config()
    error = exc_info.value
    assert "supersecrettoken" not in str(error)
    rendered = "".join(
        traceback.TracebackException(
            type(error), error, error.__traceback__, capture_locals=True
        ).format()
    )
    assert "supersecrettoken" not in rendered


def test_stored_config_repr_redacts_the_token() -> None:
    # A loaded config is a frame local in many functions; its repr (logged, or rendered by a
    # locals-capturing traceback) must not surface the stored token, while non-secret fields stay
    # visible for debugging.
    rendered = repr(StoredConfig(notion_api_key="supersecrettoken", notion_page_id="db-1"))
    assert "supersecrettoken" not in rendered  # the token is redacted...
    assert "db-1" in rendered  # ...but the database id (not a secret) is shown
    assert "notion_api_key" in rendered  # the field still appears, just with its value masked
    assert "notion_api_key=None" in repr(StoredConfig())  # an unset token renders plainly as None


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
    assert resolve_notion_credentials() == (Secret("env-tok"), "env-db")


def test_resolve_notion_credentials_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    assert resolve_notion_credentials() == (Secret("cfg-tok"), "cfg-db")


def test_resolve_notion_credentials_env_overrides_config_per_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    monkeypatch.setenv(NOTION_TOKEN_ENV, "env-tok")
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    assert resolve_notion_credentials() == (Secret("env-tok"), "cfg-db")


def test_resolve_notion_credentials_config_token_with_env_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    monkeypatch.delenv(NOTION_TOKEN_ENV, raising=False)
    monkeypatch.setenv(NOTION_DATABASE_ENV, "env-db")
    assert resolve_notion_credentials() == (Secret("cfg-tok"), "env-db")


def test_resolve_notion_credentials_blank_env_falls_back_to_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(StoredConfig(notion_api_key="cfg-tok", notion_page_id="cfg-db"))
    monkeypatch.setenv(NOTION_TOKEN_ENV, "   ")
    monkeypatch.setenv(NOTION_DATABASE_ENV, "  ")
    assert resolve_notion_credentials() == (Secret("cfg-tok"), "cfg-db")


def test_resolve_notion_credentials_wraps_the_token_in_a_redacting_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTION_TOKEN_ENV, "supersecrettoken")
    monkeypatch.setenv(NOTION_DATABASE_ENV, "db")
    token, _ = resolve_notion_credentials()
    assert token.reveal() == "supersecrettoken"  # the raw value is reachable only via reveal()
    assert "supersecrettoken" not in str(token)  # str/repr redact, so it cannot leak by accident
    assert "supersecrettoken" not in repr(token)


def test_resolve_notion_credentials_missing_database_keeps_token_out_of_traceback_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Token present but database missing: the raise must not leave a bare token in frame locals,
    # which a locals-capturing traceback renderer would otherwise surface.
    monkeypatch.setenv(NOTION_TOKEN_ENV, "supersecrettoken")
    monkeypatch.delenv(NOTION_DATABASE_ENV, raising=False)
    with pytest.raises(PromptDiaryError) as exc_info:
        resolve_notion_credentials()
    error = exc_info.value
    rendered = "".join(
        traceback.TracebackException(
            type(error), error, error.__traceback__, capture_locals=True
        ).format()
    )
    assert "supersecrettoken" not in rendered


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


# --- resolve_notion_reporter ---------------------------------------------------------------------


def test_resolve_notion_reporter_defaults_column_to_huibaoren() -> None:
    # A name alone resolves against the default 汇报人 column (what the wizard sets up).
    save_config(StoredConfig(notion_reporter="Wei Hu"))
    assert resolve_notion_reporter() == ("Wei Hu", "汇报人")


def test_resolve_notion_reporter_uses_configured_column() -> None:
    # A power user can retarget the column (e.g. an English database) by hand.
    save_config(StoredConfig(notion_reporter="Wei Hu", notion_reporter_property="Reporter"))
    assert resolve_notion_reporter() == ("Wei Hu", "Reporter")


def test_resolve_notion_reporter_none_without_a_name() -> None:
    # A column with no name is not a reporter: there is nothing to write.
    save_config(StoredConfig(notion_reporter_property="Reporter"))
    assert resolve_notion_reporter() is None


def test_resolve_notion_reporter_none_when_unset() -> None:
    assert resolve_notion_reporter() is None
