"""The generation configuration is a complete, typed package resource."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING

import msgspec
import pytest

from prompt_diary.generate.agent_settings import load_agent_settings

if TYPE_CHECKING:
    from pathlib import Path


def test_settings_are_loaded_from_package_not_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = load_agent_settings()
    (tmp_path / "agent-settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert load_agent_settings() == expected


@pytest.mark.parametrize(
    ("pass_settings", "error"),
    [
        ({"model": "test-model"}, "missing required field `reasoning_effort`"),
        ({"model": "test-model", "reasoning_effort": "typo"}, "Invalid enum value"),
        ({"model": "", "reasoning_effort": "medium"}, "length >= 1"),
        (
            {"model": "test-model", "reasoning_effort": "medium", "extra": True},
            "unknown field `extra`",
        ),
    ],
)
def test_invalid_packaged_pass_settings_do_not_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pass_settings: dict[str, object],
    error: str,
) -> None:
    payload = json.loads(
        files("prompt_diary.generate").joinpath("agent-settings.json").read_text(encoding="utf-8")
    )
    payload["daily_synthesis"]["team_learning"] = pass_settings
    (tmp_path / "agent-settings.json").write_text(json.dumps(payload), encoding="utf-8")

    def package_files(_package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("prompt_diary.generate.agent_settings.files", package_files)

    with pytest.raises(msgspec.ValidationError, match=error):
        load_agent_settings()
