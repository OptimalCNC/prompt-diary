"""Shared fixtures and builders for daily synthesis tests.

The ``basic`` fixture is a complete post-project-synthesis workspace: metadata, project metadata,
the session index, the evidence cards, and the project-synthesis envelope. Daily synthesis reads
the session index (for citation resolution) and ``project-synthesis.json`` (work items and
``source_user_messages``); the evidence cards are kept for workspace fidelity even though daily
synthesis does not read them directly.

The write tools patch a single ``daily-report.json`` at the workspace root. A deterministic Build
step (a later stage) seeds that file with the three synthesize slots set to ``null``; until Build
exists, :func:`seed_daily_report_skeleton` writes a minimal valid skeleton so the write tools have
something to patch.

The write-tool API (``prompt_diary.generate.daily_synthesis.mcp``) is imported lazily inside the
``call_*``/``result_to_dict`` helpers rather than at module top level: the fixtures and builders
here are shared with ``test_model.py`` and ``test_citations.py``, which must keep importing even
before the write-tool module exists. The new write tests import the API directly and so fail at
import time (the expected RED state) without taking the shared builders down with them.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from prompt_diary.generate.daily_synthesis.finalize import FinalizeResult
    from prompt_diary.generate.daily_synthesis.mcp import (
        WriteEngagementResult,
        WriteProjectSummaryResult,
        WriteReportTitleResult,
        WriteTeamLearningResult,
    )

PROJECT_KEY = "ReportGenerator-e6ff7eeda632"
PROJECT_LABEL = "ReportGenerator"

# The two distinct project keys in the two-projects fixture, in workspace (sorted dir-name) order.
TWO_PROJECTS_KEY_A = "ProjectAlpha-aaaaaaaaaaaa"
TWO_PROJECTS_KEY_B = "ProjectBeta-bbbbbbbbbbbb"

_FIXTURES_ROOT = Path(__file__).parents[1] / "fixtures" / "daily-synthesis"
FIXTURE_ROOT = _FIXTURES_ROOT / "basic"
DISPOSITIONS_FIXTURE_ROOT = _FIXTURES_ROOT / "dispositions"
CORRUPT_FIXTURE_ROOT = _FIXTURES_ROOT / "corrupt"
EXEC_UNCITED_FIXTURE_ROOT = _FIXTURES_ROOT / "exec-uncited"
TWO_PROJECTS_FIXTURE_ROOT = _FIXTURES_ROOT / "two-projects"

DAILY_REPORT_NAME = "daily-report.json"


def copy_basic_daily_workspace(tmp_path: Path) -> Path:
    """Copy the post-project-synthesis daily-synthesis fixture into a writable test directory."""
    return _copy_workspace(FIXTURE_ROOT, tmp_path)


def copy_dispositions_daily_workspace(tmp_path: Path) -> Path:
    """Copy the disposition-coverage fixture: one project of material work items per disposition.

    Its envelope exercises every disposition branch (failed / blocked / interrupted / completed /
    clarification, plus failed-wins precedence). Build does not read evidence cards, so this
    fixture omits them.
    """
    return _copy_workspace(DISPOSITIONS_FIXTURE_ROOT, tmp_path)


def copy_two_projects_daily_workspace(tmp_path: Path) -> Path:
    """Copy a two-project fixture: two distinct project keys, each with one material work item.

    Each project carries a committed evidence chain and a material work item citing it, so the
    runner runs one summary pass per project plus the shared engagement and team-learning passes.
    The two projects deliberately reuse the same ``S0001/T0001`` ref, exercising the cross-project
    labelling that disambiguates repeated session refs.
    """
    return _copy_workspace(TWO_PROJECTS_FIXTURE_ROOT, tmp_path)


def copy_corrupt_daily_workspace(tmp_path: Path) -> Path:
    """Copy a workspace whose ``project-synthesis.json`` holds one structurally-invalid work item.

    The lone work item carries a non-controlled ``kind``, so ``parse_work_item`` rejects it. Build
    must fail loudly on this post-synthesis corruption rather than silently dropping the work item.
    """
    return _copy_workspace(CORRUPT_FIXTURE_ROOT, tmp_path)


def copy_exec_uncited_daily_workspace(tmp_path: Path) -> Path:
    """Copy a workspace whose one material work item has an uncited outcome and terminal state.

    Its outcome and ``failed`` terminal carry empty ``evidence_refs``, so their resolved citations
    are empty. Build keeps the work item in Work by Project (uncited).
    """
    return _copy_workspace(EXEC_UNCITED_FIXTURE_ROOT, tmp_path)


def _copy_workspace(fixture_root: Path, tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    shutil.copytree(fixture_root / "workspace", workspace)
    return workspace


def rewrite_envelope_gap_only(workspace_path: Path, *, project_key: str = PROJECT_KEY) -> None:
    """Rewrite a project's envelope so its only work item is a gap item over an unevidenced turn.

    Models a project that project synthesis covered entirely with ``evidence_gap_item`` (no
    committed, citable turn): Build keeps the gap item in Work by Project, but the runner runs no
    summary pass for it and Finalize does not require one. ``source_user_messages`` is cleared so
    the engagement/team-learning inputs carry nothing for this project either.
    """
    path = workspace_path / "projects" / project_key / "project-synthesis.json"
    envelope = _load_json(path)
    envelope["work_items"] = [
        {
            "work_item_ref": "W0001",
            "kind": "evidence_gap_item",
            "title": "Indexed turn with no extractable evidence",
            "covered_turns": [{"session_ref": "S0001", "turn_ref": "T0003"}],
            "outcomes": [],
            "terminal_states": [],
            "limits": [],
            "confidence": "low",
        }
    ]
    envelope["source_user_messages"] = []
    path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")


def project_citation(session_ref: str, turn: str) -> dict[str, str]:
    """Build a per-project citation (project implied by the tool argument)."""
    return {"session_ref": session_ref, "turn_ref": turn}


def cross_citation(session_ref: str, turn: str, project_key: str = PROJECT_KEY) -> dict[str, str]:
    """Build a cross-project citation that names its project explicitly."""
    return {"project_key": project_key, "session_ref": session_ref, "turn_ref": turn}


def valid_project_summary() -> dict[str, Any]:
    """A valid per-project ``summary`` submission for the basic fixture."""
    return {
        "text": "Simplified the evidence tools and designed the QA approach.",
        "citations": [project_citation("S0001", "T0001"), project_citation("S0002", "T0001")],
    }


def valid_report_title() -> dict[str, Any]:
    """A valid whole-report title submission for the basic fixture."""
    return {
        "text": "Evidence Tools and QA Strategy",
        "citations": [cross_citation("S0001", "T0001")],
    }


def valid_engagement() -> dict[str, Any]:
    """A valid engagement submission (overall_reading / observations / limits) for the fixture."""
    return {
        "overall_reading": {
            "text": "The user framed concrete goals and approved results.",
            "citations": [cross_citation("S0001", "T0001")],
            "confidence": "medium",
        },
        "observations": [
            {
                "dimension": "direction",
                "statement": "Asked to simplify the evidence tools and drop chain_ref.",
                "citations": [cross_citation("S0001", "T0001")],
                "confidence": "medium",
            }
        ],
        "limits": ["Offline thinking and review are not observable."],
    }


def valid_team_learning() -> dict[str, Any]:
    """A valid team-learning submission (takeaways / patterns / limits) for the fixture."""
    return {
        "takeaways": {
            "text": "Capturing a reusable QA approach is worth promoting.",
            "citations": [cross_citation("S0002", "T0001")],
            "confidence": "low",
        },
        "patterns": [
            {
                "kind": "reuse",
                "statement": "A three-layer QA strategy was written down as a repeatable approach.",
                "rationale": "A reusable checklist lowers the attention cost of future QA work.",
                "recurrence": "single sighting; likely to recur for future test design",
                "citations": [cross_citation("S0002", "T0001")],
                "confidence": "low",
            }
        ],
        "limits": ["Single-day evidence; recurrence cannot be confirmed."],
    }


def seed_daily_report_skeleton(workspace_path: Path) -> Path:
    """Write a minimal valid ``daily-report.json`` skeleton with the synthesize slots null.

    Stands in for the deterministic Build step until it exists: the write tools require the file
    to already exist and only patch their own slot. ``report_date``/``status``/``timezone`` and the
    report window are derived from the fixture ``metadata.json``; one ``projects`` entry is emitted
    per project directory, each with ``summary`` set to ``null``.
    """
    metadata = _load_json(workspace_path / "metadata.json")
    window_local = _as_mapping(metadata.get("report_window_local"))
    skeleton: dict[str, Any] = {
        "schema_version": 1,
        "report_date": metadata.get("report_date"),
        "status": metadata.get("status"),
        "window": {
            "start": window_local.get("start"),
            "end": window_local.get("end"),
            "timezone": metadata.get("timezone"),
        },
        "report_title": None,
        "overall_confidence": None,
        "projects": [
            {
                "project_key": project_key,
                "project_label": project_label,
                "summary": None,
                "work_items": [],
                "source_user_messages": [],
            }
            for project_key, project_label in _workspace_projects(workspace_path)
        ],
        "engagement_assessment": None,
        "team_learning": None,
    }
    path = daily_report_path(workspace_path)
    path.write_text(json.dumps(skeleton, indent=2) + "\n", encoding="utf-8")
    return path


def daily_report_path(workspace_path: Path) -> Path:
    return workspace_path / DAILY_REPORT_NAME


def load_daily_report(workspace_path: Path) -> dict[str, Any]:
    """Read ``daily-report.json`` back as a dict."""
    return _load_json(daily_report_path(workspace_path))


def daily_report_text(workspace_path: Path) -> str:
    return daily_report_path(workspace_path).read_text(encoding="utf-8")


def project_slot(workspace_path: Path, project_key: str = PROJECT_KEY) -> dict[str, Any]:
    """Return the ``projects`` entry for ``project_key`` from the stored daily report."""
    report = load_daily_report(workspace_path)
    for entry in _as_list(report.get("projects")):
        mapping = _as_mapping(entry)
        if mapping.get("project_key") == project_key:
            return mapping
    pytest.fail(f"no projects entry for {project_key!r} in {DAILY_REPORT_NAME}")


def call_write_project_summary_api(
    *,
    workspace_path: Path,
    project_key: str = PROJECT_KEY,
    summary: dict[str, Any] | None = None,
) -> WriteProjectSummaryResult:
    # Imported lazily so this shared module keeps importing before the write-tool module exists.
    from prompt_diary.generate.daily_synthesis.mcp import write_project_summary  # noqa: PLC0415

    return write_project_summary(
        workspace_path=workspace_path,
        project_key=project_key,
        summary=valid_project_summary() if summary is None else summary,
    )


def call_write_report_title_api(
    *,
    workspace_path: Path,
    title: dict[str, Any] | None = None,
) -> WriteReportTitleResult:
    # Imported lazily so this shared module keeps importing before the write-tool module exists.
    from prompt_diary.generate.daily_synthesis.mcp import write_report_title  # noqa: PLC0415

    return write_report_title(
        workspace_path=workspace_path,
        title=valid_report_title() if title is None else title,
    )


def call_write_engagement_api(
    *,
    workspace_path: Path,
    overall_reading: dict[str, Any] | None = None,
    observations: list[Any] | None = None,
    limits: list[Any] | None = None,
) -> WriteEngagementResult:
    # Imported lazily so this shared module keeps importing before the write-tool module exists.
    from prompt_diary.generate.daily_synthesis.mcp import write_engagement  # noqa: PLC0415

    payload = valid_engagement()
    return write_engagement(
        workspace_path=workspace_path,
        overall_reading=payload["overall_reading"] if overall_reading is None else overall_reading,
        observations=payload["observations"] if observations is None else observations,
        limits=payload["limits"] if limits is None else limits,
    )


def call_write_team_learning_api(
    *,
    workspace_path: Path,
    takeaways: dict[str, Any] | None = None,
    patterns: list[Any] | None = None,
    limits: list[Any] | None = None,
) -> WriteTeamLearningResult:
    # Imported lazily so this shared module keeps importing before the write-tool module exists.
    from prompt_diary.generate.daily_synthesis.mcp import write_team_learning  # noqa: PLC0415

    payload = valid_team_learning()
    return write_team_learning(
        workspace_path=workspace_path,
        takeaways=payload["takeaways"] if takeaways is None else takeaways,
        patterns=payload["patterns"] if patterns is None else patterns,
        limits=payload["limits"] if limits is None else limits,
    )


def build_daily_report_via_api(workspace_path: Path) -> dict[str, Any]:
    """Run the deterministic Build step against ``workspace_path`` and return the skeleton."""
    # Imported lazily so this shared module keeps importing before the build module exists.
    from prompt_diary.generate.daily_synthesis.build import build_daily_report  # noqa: PLC0415

    return build_daily_report(workspace_path=workspace_path)


def fill_synthesize_slots(workspace_path: Path, *, project_key: str = PROJECT_KEY) -> None:
    """Fill the synthesize slots with the ``valid_*`` builders via the write tools."""
    assert_project_summary_written(
        call_write_project_summary_api(workspace_path=workspace_path, project_key=project_key),
        project_key=project_key,
    )
    assert_report_title_written(call_write_report_title_api(workspace_path=workspace_path))
    assert_engagement_written(call_write_engagement_api(workspace_path=workspace_path))
    assert_team_learning_written(call_write_team_learning_api(workspace_path=workspace_path))


def finalize_daily_report_via_api(workspace_path: Path) -> FinalizeResult:
    """Run the deterministic Finalize step against ``workspace_path``."""
    # Imported lazily so this shared module keeps importing before the finalize module exists.
    from prompt_diary.generate.daily_synthesis.finalize import (  # noqa: PLC0415
        finalize_daily_report,
    )

    return finalize_daily_report(workspace_path=workspace_path)


def empty_daily_workspace(tmp_path: Path) -> Path:
    """Build a minimal prepared workspace with metadata but no projects (no work items).

    The smallest input that exercises the empty-report path: ``load_prepared_workspace`` returns no
    projects, Build emits an empty ``projects`` list, and Finalize sees no work items.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 2,
        "report_date": "2026-05-28",
        "timezone": "Asia/Shanghai",
        "status": "final",
        "report_window_local": {
            "start": "2026-05-28T00:00:00+08:00",
            "end": "2026-05-29T00:00:00+08:00",
        },
    }
    (workspace / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return workspace


def finalize_result_to_dict(result: object) -> dict[str, Any]:
    """Reduce a finalize result to a comparable dict (status + payload)."""
    # Imported lazily so this shared module keeps importing before the finalize module exists.
    from prompt_diary.generate.daily_synthesis.finalize import (  # noqa: PLC0415
        FinalizedResult,
        FinalizeInvalidResult,
    )

    if isinstance(result, FinalizedResult):
        return {"status": result.status, "overall_confidence": result.overall_confidence}
    if isinstance(result, FinalizeInvalidResult):
        return {
            "status": result.status,
            "errors": [
                {"path": error.path, "message": error.message, "hint": error.hint}
                for error in result.errors
            ],
        }
    pytest.fail(f"result must be a finalize result, got {type(result)!r}")


def result_to_dict(result: object) -> dict[str, Any]:
    # Imported lazily so this shared module keeps importing before the write-tool module exists.
    from prompt_diary.generate.daily_synthesis.mcp import (  # noqa: PLC0415
        DailyReportInvalidResult,
        EngagementWrittenResult,
        ProjectSummaryWrittenResult,
        ReportTitleWrittenResult,
        TeamLearningWrittenResult,
    )

    if isinstance(result, ProjectSummaryWrittenResult):
        return {"status": result.status, "project_key": result.project_key}
    if isinstance(
        result, (ReportTitleWrittenResult, EngagementWrittenResult, TeamLearningWrittenResult)
    ):
        return {"status": result.status}
    if isinstance(result, DailyReportInvalidResult):
        return {
            "status": result.status,
            "errors": [
                {"path": error.path, "message": error.message, "hint": error.hint}
                for error in result.errors
            ],
        }
    if isinstance(result, Mapping):
        return dict(cast("Mapping[str, Any]", result))
    pytest.fail(f"result must be a daily-report write result or mapping, got {type(result)!r}")


def assert_project_summary_written(result: object, *, project_key: str = PROJECT_KEY) -> None:
    payload = result_to_dict(result)
    assert payload["status"] == "written"
    assert payload["project_key"] == project_key


def assert_report_title_written(result: object) -> None:
    payload = result_to_dict(result)
    assert payload["status"] == "written"


def assert_engagement_written(result: object) -> None:
    payload = result_to_dict(result)
    assert payload["status"] == "written"


def assert_team_learning_written(result: object) -> None:
    payload = result_to_dict(result)
    assert payload["status"] == "written"


def assert_invalid_result(
    result: object,
    *,
    path: str,
    message_contains: str | None = None,
    hint_contains: str | None = None,
) -> None:
    payload = result_to_dict(result)
    assert payload["status"] == "invalid"
    errors_obj = payload["errors"]
    assert isinstance(errors_obj, list)
    matching: list[Mapping[str, Any]] = []
    for error_obj in cast("list[object]", errors_obj):
        if isinstance(error_obj, Mapping):
            error = cast("Mapping[str, Any]", error_obj)
            if error.get("path") == path:
                matching.append(error)
    assert matching, f"expected an invalid error at path {path!r}: {errors_obj!r}"
    error = matching[0]
    message = error.get("message")
    hint = error.get("hint")
    assert isinstance(message, str) and message  # noqa: PT018
    assert isinstance(hint, str) and hint  # noqa: PT018
    if message_contains is not None:
        assert message_contains in message
    if hint_contains is not None:
        assert hint_contains in hint


def _workspace_projects(workspace_path: Path) -> list[tuple[str, str]]:
    projects_root = workspace_path / "projects"
    projects: list[tuple[str, str]] = []
    for project_dir in sorted(projects_root.iterdir(), key=lambda path: path.name):
        if not project_dir.is_dir():
            continue
        project_json = _load_json(project_dir / "project.json")
        project_key = _as_str(project_json.get("project_key"))
        project_label = _as_str(project_json.get("project_label"))
        projects.append((project_key, project_label))
    return projects


def _load_json(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def _as_mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""
