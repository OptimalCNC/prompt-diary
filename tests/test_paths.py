"""Tests for reports-root resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from prompt_diary import paths
from prompt_diary.errors import PromptDiaryError
from prompt_diary.paths import (
    REPORTS_HOME_ENV,
    default_reports_root,
    resolve_reports_root,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _data_dir_stub(result: str) -> Callable[..., str]:
    """Build a ``platformdirs.user_data_dir`` stand-in that asserts its call contract."""

    def _stub(appname: str, *, appauthor: bool) -> str:
        assert appname == "prompt-diary"
        assert appauthor is False
        return result

    return _stub


def test_default_reports_root_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REPORTS_HOME_ENV, "/custom/reports")
    assert default_reports_root() == Path("/custom/reports")


def test_default_reports_root_strips_and_expands_padded_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REPORTS_HOME_ENV, "  ~/diary-home  ")
    assert default_reports_root() == Path.home() / "diary-home"


def test_default_reports_root_treats_blank_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REPORTS_HOME_ENV, "   ")
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    assert default_reports_root() == Path("/stub/data")


def test_default_reports_root_falls_back_to_platform_data_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    assert default_reports_root() == Path("/stub/data")


def test_default_reports_root_expands_user_in_platform_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("~/xdg/prompt-diary"))
    assert default_reports_root() == Path.home() / "xdg" / "prompt-diary"


def test_default_reports_root_rejects_relative_platform_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REPORTS_HOME_ENV, raising=False)
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("relative/dir"))
    with pytest.raises(PromptDiaryError, match="relative"):
        default_reports_root()


def test_resolve_reports_root_prefers_explicit_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REPORTS_HOME_ENV, "/env/reports")
    assert resolve_reports_root(Path("/explicit/reports")) == Path("/explicit/reports")


def test_resolve_reports_root_expands_user_in_explicit() -> None:
    assert resolve_reports_root(Path("~/explicit-home")) == Path.home() / "explicit-home"


def test_resolve_reports_root_delegates_to_default_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REPORTS_HOME_ENV, "/env/reports")
    assert resolve_reports_root(None) == Path("/env/reports")
