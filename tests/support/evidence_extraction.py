from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest

from prompt_diary.generate.evidence_extraction.mcp import (
    WriteEvidenceAppendedResult,
    WriteEvidenceInvalidResult,
    WriteEvidenceResult,
    write_evidence,
)

if TYPE_CHECKING:
    from prompt_diary.generate.evidence_extraction.model import EvidenceWriteError

PROJECT_KEY = "ReportGenerator-e6ff7eeda632"
SESSION_REF = "S0001"
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "evidence-extraction" / "basic-two-turns"


def copy_basic_evidence_workspace(tmp_path: Path) -> Path:
    """Copy the tiny prepared workspace fixture into a writable test directory."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE_ROOT / "workspace", workspace)
    return workspace


def valid_material_doc_chain() -> dict[str, Any]:
    return {
        "turn_ref": "T0001",
        "trigger": {
            "type": "explicit_user_message",
            "summary": "User asked for evidence contract documentation updates.",
            "quoted_messages": [
                {
                    "text": "Please update the evidence contract docs for the MCP write surface.",
                    "citations": [{"lines": "2-2"}],
                }
            ],
            "citations": [{"lines": "2-2"}],
        },
        "agent_reactions": [
            {
                "summary": "Agent inspected generation docs and edited the MCP write contract.",
                "citations": [{"lines": "3-5"}],
            }
        ],
        "outcomes": [
            {
                "category": "document_outcome",
                "summary": "Evidence contract documentation was updated for canonical card writes.",
                "citations": [{"lines": "5-5"}],
            }
        ],
        "observed_checks": [
            {
                "type": "test_output",
                "summary": "Prompt tests were run and passed.",
                "citations": [{"lines": "6-7"}],
            }
        ],
        "terminal_state": {
            "type": "material_result",
            "summary": "The agent reported the documentation update and passing test result.",
            "citations": [{"lines": "8-8"}],
        },
        "materiality": "material",
    }


def valid_no_material_chain() -> dict[str, Any]:
    return {
        "turn_ref": "T0002",
        "trigger": {
            "type": "resume_or_continue",
            "summary": "User asked the agent to continue.",
            "quoted_messages": [{"text": "continue", "citations": [{"lines": "9-9"}]}],
            "citations": [{"lines": "9-9"}],
        },
        "agent_reactions": [
            {
                "summary": "Agent stated that no further assigned work remained.",
                "citations": [{"lines": "10-10"}],
            }
        ],
        "outcomes": [],
        "observed_checks": [],
        "terminal_state": {
            "type": "no_material",
            "summary": "No material result was produced after the continue request.",
            "citations": [{"lines": "10-10"}],
        },
        "materiality": "none",
    }


def material_result_without_outcomes_chain() -> dict[str, Any]:
    chain = valid_no_material_chain()
    chain["terminal_state"]["type"] = "material_result"
    chain["materiality"] = "material"
    return chain


def chain_with_value(path: tuple[str | int, ...], value: Any) -> dict[str, Any]:
    chain = valid_material_doc_chain()
    target: Any = chain
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    return chain


def call_write_evidence_api(
    *,
    workspace_path: Path,
    project_key: str = PROJECT_KEY,
    session_ref: str = SESSION_REF,
    evidence_chain: dict[str, Any] | None = None,
) -> WriteEvidenceResult:
    return write_evidence(
        workspace_path=workspace_path,
        project_key=project_key,
        session_ref=session_ref,
        evidence_chain=valid_material_doc_chain() if evidence_chain is None else evidence_chain,
    )


def result_to_dict(result: object) -> dict[str, Any]:
    if isinstance(result, WriteEvidenceAppendedResult):
        return {
            "status": result.status,
            "project_key": result.project_key,
            "session_ref": result.session_ref,
            "turn_ref": result.turn_ref,
        }
    if isinstance(result, WriteEvidenceInvalidResult):
        return {
            "status": result.status,
            "errors": [_error_to_dict(error) for error in result.errors],
        }
    if isinstance(result, Mapping):
        return dict(cast("Mapping[str, Any]", result))
    pytest.fail(f"result must be a write evidence result or mapping, got {type(result)!r}")


def _error_to_dict(error: EvidenceWriteError) -> dict[str, str]:
    return {
        "path": error.path,
        "message": error.message,
        "hint": error.hint,
    }


def load_evidence_card(workspace_path: Path) -> dict[str, Any]:
    card_path = workspace_path / "projects" / PROJECT_KEY / "evidence" / f"{SESSION_REF}.json"
    return cast("dict[str, Any]", json.loads(card_path.read_text(encoding="utf-8")))


def evidence_card_text(workspace_path: Path) -> str:
    card_path = workspace_path / "projects" / PROJECT_KEY / "evidence" / f"{SESSION_REF}.json"
    return card_path.read_text(encoding="utf-8")


def assert_appended_result(result: object, *, turn_ref: str) -> None:
    result_dict = result_to_dict(result)
    assert {
        key: result_dict[key] for key in ("status", "project_key", "session_ref", "turn_ref")
    } == {
        "status": "appended",
        "project_key": PROJECT_KEY,
        "session_ref": SESSION_REF,
        "turn_ref": turn_ref,
    }


def assert_invalid_result(
    result: object,
    *,
    path: str,
    message_contains: str | None = None,
    hint_contains: str | None = None,
) -> None:
    result_dict = result_to_dict(result)
    assert result_dict["status"] == "invalid"
    errors_obj = result_dict["errors"]
    assert isinstance(errors_obj, list)
    errors = cast("list[object]", errors_obj)
    matching_errors: list[Mapping[str, Any]] = []
    for error_obj in errors:
        if isinstance(error_obj, Mapping):
            error = cast("Mapping[str, Any]", error_obj)
            if error.get("path") == path:
                matching_errors.append(error)
    assert matching_errors, f"expected invalid result to include path {path!r}: {errors!r}"
    error = matching_errors[0]
    message = error.get("message")
    hint = error.get("hint")
    assert isinstance(message, str)
    assert message
    assert isinstance(hint, str)
    assert hint
    if message_contains is not None:
        assert message_contains in message
    if hint_contains is not None:
        assert hint_contains in hint


def deep_copy_json(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)


def build_evidence_chain(
    *,
    turn_ref: str,
    span: tuple[int, int],
    kind: Literal["material", "no_material"] = "material",
) -> dict[str, Any]:
    """Build a write-valid evidence chain whose citations all fall inside ``span``.

    Trigger/quoted cite the first line; reaction/outcome/terminal cite the last line, so a
    material outcome always intersects reaction evidence (never only the trigger) for any
    span of one or more lines.
    """
    start, end = span
    start_lines = f"{start}-{start}"
    end_lines = f"{end}-{end}"
    trigger = {
        "type": "explicit_user_message",
        "summary": f"User request captured for {turn_ref}.",
        "quoted_messages": [
            {"text": "Captured user message.", "citations": [{"lines": start_lines}]}
        ],
        "citations": [{"lines": start_lines}],
    }
    reactions: list[dict[str, Any]] = [
        {"summary": f"Agent reaction for {turn_ref}.", "citations": [{"lines": end_lines}]}
    ]
    if kind == "material":
        outcomes: list[dict[str, Any]] = [
            {
                "category": "document_outcome",
                "summary": f"Material result for {turn_ref}.",
                "citations": [{"lines": end_lines}],
            }
        ]
        terminal: dict[str, Any] = {
            "type": "material_result",
            "summary": f"Material result reported for {turn_ref}.",
            "citations": [{"lines": end_lines}],
        }
        materiality = "material"
    else:
        outcomes = []
        terminal = {
            "type": "no_material",
            "summary": f"No material result for {turn_ref}.",
            "citations": [{"lines": end_lines}],
        }
        materiality = "none"
    return {
        "turn_ref": turn_ref,
        "trigger": trigger,
        "agent_reactions": reactions,
        "outcomes": outcomes,
        "observed_checks": [],
        "terminal_state": terminal,
        "materiality": materiality,
    }
