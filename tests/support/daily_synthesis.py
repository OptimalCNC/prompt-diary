"""Shared fixtures and builders for daily synthesis tests.

The ``basic`` fixture is a complete post-project-synthesis workspace: metadata, project metadata,
the session index, the evidence cards, and the project-synthesis envelope. Daily synthesis reads
the session index (for citation resolution) and ``project-synthesis.json`` (work items and
``source_user_messages``); the evidence cards are kept for workspace fidelity even though daily
synthesis does not read them directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

PROJECT_KEY = "ReportGenerator-e6ff7eeda632"
PROJECT_LABEL = "ReportGenerator"

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "daily-synthesis" / "basic"


def copy_basic_daily_workspace(tmp_path: Path) -> Path:
    """Copy the post-project-synthesis daily-synthesis fixture into a writable test directory."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE_ROOT / "workspace", workspace)
    return workspace


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
