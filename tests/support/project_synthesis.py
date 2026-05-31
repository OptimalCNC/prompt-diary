from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any, cast

PROJECT_KEY = "ReportGenerator-e6ff7eeda632"
PROJECT_LABEL = "ReportGenerator"


def turn_ref(session_ref: str, turn: str) -> dict[str, str]:
    return {"session_ref": session_ref, "turn_ref": turn}


def valid_material_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0001",
        "kind": "material_work_item",
        "title": "Finalize the evidence-extraction contract",
        "covered_turns": [turn_ref("S0001", "T0001"), turn_ref("S0001", "T0002")],
        "trigger": {
            "summary": "User drove the evidence surface to turn_ref and finalized the choices.",
            "evidence_refs": [turn_ref("S0001", "T0001")],
        },
        "agent_reaction": {
            "summary": "Migrated the contract and prompt, then froze with a commit.",
            "main_actions": ["turn_ref migration", "freeze commit"],
        },
        "outcomes": [
            {
                "category": "document_outcome",
                "summary": "Evidence contract moved to top-level turn_ref.",
                "evidence_refs": [turn_ref("S0001", "T0001")],
                "confidence": "high",
            }
        ],
        "terminal_states": [
            {
                "type": "material_result",
                "summary": "Contract frozen as a checkpoint commit.",
                "evidence_refs": [turn_ref("S0001", "T0002")],
            }
        ],
        "limits": ["Prompt-test suite not confirmed green within these turns."],
        "confidence": "high",
    }


def valid_no_material_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0002",
        "kind": "no_material_work_item",
        "title": "Trivial connectivity and throwaway questions",
        "covered_turns": [turn_ref("S0002", "T0001")],
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def valid_evidence_gap_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0003",
        "kind": "evidence_gap_item",
        "title": "Indexed turns with no extractable evidence",
        "covered_turns": [turn_ref("S0001", "T0003")],
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def valid_excluded_work_item() -> dict[str, Any]:
    return {
        "work_item_ref": "W0004",
        "kind": "excluded_with_reason",
        "title": "Duplicate evidence already represented elsewhere",
        "covered_turns": [turn_ref("S0002", "T0002")],
        "reason": "Duplicate of W0001; the same edit is already represented there.",
        "outcomes": [],
        "terminal_states": [],
        "limits": [],
        "confidence": "low",
    }


def work_item_with_value(path: tuple[str | int, ...], value: Any) -> dict[str, Any]:
    item = valid_material_work_item()
    target: Any = item
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    return item


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "project-synthesis" / "basic"

# Indexed-turn universe of the basic fixture, in (session, turn) order. S0001/T0003 is the gap turn.
ALL_TURNS: tuple[tuple[str, str], ...] = (
    ("S0001", "T0001"),
    ("S0001", "T0002"),
    ("S0001", "T0003"),
    ("S0002", "T0001"),
)
GAP_TURNS: tuple[tuple[str, str], ...] = (("S0001", "T0003"),)
COMMITTED_TURNS: tuple[tuple[str, str], ...] = (
    ("S0001", "T0001"),
    ("S0001", "T0002"),
    ("S0002", "T0001"),
)


def copy_basic_project_workspace(tmp_path: Path) -> Path:
    """Copy the post-extraction project-synthesis fixture into a writable test directory."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE_ROOT / "workspace", workspace)
    return workspace


def synthesis_path(workspace_path: Path) -> Path:
    return workspace_path / "projects" / PROJECT_KEY / "project-synthesis.json"


def load_project_synthesis(workspace_path: Path) -> dict[str, Any]:
    return cast(
        "dict[str, Any]", json.loads(synthesis_path(workspace_path).read_text(encoding="utf-8"))
    )


def project_synthesis_text(workspace_path: Path) -> str:
    return synthesis_path(workspace_path).read_text(encoding="utf-8")


def deep_copy_json(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)
