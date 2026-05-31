from __future__ import annotations

from typing import Any

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
