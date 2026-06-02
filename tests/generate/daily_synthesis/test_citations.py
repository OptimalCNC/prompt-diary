"""Tests for daily-report citation resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_diary.generate.daily_synthesis.citations import CitationResolver
from prompt_diary.generate.workspace import load_prepared_workspace
from tests.support.daily_synthesis import PROJECT_KEY, copy_basic_daily_workspace

if TYPE_CHECKING:
    from pathlib import Path


def _resolver(tmp_path: Path) -> CitationResolver:
    workspace = load_prepared_workspace(copy_basic_daily_workspace(tmp_path))
    return CitationResolver.from_workspace(workspace)


def test_resolves_indexed_turn_to_its_line_range(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)

    resolved = resolver.resolve(project_key=PROJECT_KEY, session_ref="S0001", turn_ref="T0001")

    assert resolved is not None
    assert resolved.lines == "2-8"
    assert resolved.to_json() == {
        "project_key": PROJECT_KEY,
        "session_ref": "S0001",
        "turn_ref": "T0001",
        "lines": "2-8",
    }


def test_resolves_every_indexed_turn(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)

    expected = {
        ("S0001", "T0001"): "2-8",
        ("S0001", "T0002"): "9-12",
        ("S0001", "T0003"): "13-15",
        ("S0002", "T0001"): "2-6",
    }
    for (session_ref, turn_ref), lines in expected.items():
        resolved = resolver.resolve(
            project_key=PROJECT_KEY, session_ref=session_ref, turn_ref=turn_ref
        )
        assert resolved is not None
        assert resolved.lines == lines


def test_unknown_turn_does_not_resolve(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)

    assert resolver.resolve(project_key=PROJECT_KEY, session_ref="S0001", turn_ref="T9999") is None
    assert resolver.resolve(project_key=PROJECT_KEY, session_ref="S0099", turn_ref="T0001") is None


def test_wrong_project_key_does_not_resolve(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)

    result = resolver.resolve(project_key="Other-project", session_ref="S0001", turn_ref="T0001")
    assert result is None
