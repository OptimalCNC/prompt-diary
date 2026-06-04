"""Tests for the per-user platform data directory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from prompt_diary import paths
from prompt_diary.errors import PromptDiaryError
from prompt_diary.paths import platform_data_dir

if TYPE_CHECKING:
    from collections.abc import Callable


def _data_dir_stub(result: str) -> Callable[..., str]:
    """Build a ``platformdirs.user_data_dir`` stand-in that asserts its call contract."""

    def _stub(appname: str, *, appauthor: bool) -> str:
        assert appname == "prompt-diary"
        assert appauthor is False
        return result

    return _stub


def test_platform_data_dir_returns_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("/stub/data"))
    assert platform_data_dir() == Path("/stub/data")


def test_platform_data_dir_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("~/xdg/prompt-diary"))
    assert platform_data_dir() == Path.home() / "xdg" / "prompt-diary"


def test_platform_data_dir_rejects_relative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platformdirs, "user_data_dir", _data_dir_stub("relative/dir"))
    with pytest.raises(PromptDiaryError, match="relative"):
        platform_data_dir()
