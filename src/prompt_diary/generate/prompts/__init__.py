"""Generation prompt templates for Prompt Diary."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from jinja2 import Environment, StrictUndefined


@dataclass(frozen=True)
class PromptEnumValue:
    """One controlled prompt value and its extractor-facing description."""

    value: str
    description: str


EVIDENCE_TRIGGER_TYPES: tuple[PromptEnumValue, ...] = (
    PromptEnumValue("explicit_user_message", "a direct user-authored request or instruction"),
    PromptEnumValue("implicit_context", "user-managed context that caused the agent reaction"),
    PromptEnumValue("user_correction", "a user correction or redirection of previous work"),
    PromptEnumValue("user_approval", "a user approval or acceptance signal"),
    PromptEnumValue("resume_or_continue", "a user request to continue, recover, or finish work"),
)

EVIDENCE_OUTCOME_CATEGORIES: tuple[PromptEnumValue, ...] = (
    PromptEnumValue(
        "code_outcome",
        "new implementation, bug fix, refactor, API change, test added, or benchmark added",
    ),
    PromptEnumValue(
        "document_outcome",
        "specification written, architecture clarified, acceptance criteria added, or old document "
        "reorganized",
    ),
    PromptEnumValue(
        "decision_outcome",
        "technical direction chosen, tradeoff clarified, or module boundary decided",
    ),
    PromptEnumValue(
        "validation_outcome",
        "test passed, simulation run, benchmark result produced, bug reproduced, or issue "
        "confirmed",
    ),
    PromptEnumValue(
        "process_outcome",
        "workflow improved, prompt improved, agent-driving rule created, or reusable checklist "
        "generated",
    ),
    PromptEnumValue(
        "research_outcome",
        "options investigated, comparison made, external reference summarized, or recommendation "
        "produced",
    ),
    PromptEnumValue(
        "blocker_outcome",
        "problem identified but not solved, with the next action clarified",
    ),
    PromptEnumValue(
        "other",
        "no controlled category fits; include the suggested category and reasoning in the summary",
    ),
)

EVIDENCE_CHECK_TYPES: tuple[PromptEnumValue, ...] = (
    PromptEnumValue("command_output", "visible command output"),
    PromptEnumValue("test_output", "visible test output"),
    PromptEnumValue("artifact_inspection", "visible inspection of an artifact"),
    PromptEnumValue("user_feedback", "visible user feedback"),
    PromptEnumValue("other", "visible check or feedback that does not fit another check type"),
)

EVIDENCE_TERMINAL_STATES: tuple[PromptEnumValue, ...] = (
    PromptEnumValue("material_result", "one or more material outcomes are present"),
    PromptEnumValue(
        "no_material",
        "the agent reacted but produced no evidence-backed artifact, decision, validation result, "
        "clarified blocker, or reusable process result",
    ),
    PromptEnumValue(
        "blocked",
        "progress stopped because a dependency, failure, missing information, or required human "
        "decision prevented completion",
    ),
    PromptEnumValue("interrupted", "the reaction paused or stopped before a natural result"),
    PromptEnumValue(
        "failed",
        "the agent attempted work and the observable result failed or contradicted the intended "
        "direction",
    ),
    PromptEnumValue(
        "clarification_only",
        "the interaction clarified scope, constraints, or next steps but did not produce an "
        "outcome beyond clarification",
    ),
    PromptEnumValue(
        "evidence_gap",
        "the assigned turn is too ambiguous or incomplete to classify the result",
    ),
    PromptEnumValue(
        "other",
        "no controlled terminal state fits; include the reasoning in the summary",
    ),
)

EVIDENCE_MATERIALITY_VALUES: tuple[PromptEnumValue, ...] = (
    PromptEnumValue("material", "important extracted evidence for later synthesis"),
    PromptEnumValue("minor", "low-importance extracted evidence that should still be preserved"),
    PromptEnumValue("none", "no material evidence was extracted from the assigned turn"),
)

PROJECT_WORK_ITEM_KINDS: tuple[PromptEnumValue, ...] = (
    PromptEnumValue("material_work_item", "grouped work that produced material progress"),
    PromptEnumValue(
        "no_material_work_item",
        "reportable low-value or negative turns with no material output, including the "
        "trivial-turn bucket",
    ),
    PromptEnumValue(
        "evidence_gap_item",
        "accounts for indexed turns that have no extractable evidence",
    ),
    PromptEnumValue(
        "excluded_with_reason",
        "turns intentionally left out of reportable work items; requires a reason",
    ),
)


def _load(name: str) -> str:
    return files("prompt_diary.generate.prompts").joinpath(name).read_text(encoding="utf-8")


def _render(name: str, **variables: str) -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False)  # noqa: S701 — plain-text prompts, not HTML
    return env.from_string(_load(name)).render(**variables)


def _format_enum_values(values: tuple[PromptEnumValue, ...]) -> str:
    return "\n".join(f"- `{item.value}`: {item.description}." for item in values)


def evidence_extractor_prompt(
    *,
    project_key: str,
    project_json: str,
    session_ref: str,
    session_path: str,
    session_index_record: str,
    target_turn: str,
) -> str:
    """Return the evidence extractor prompt with substituted workspace values."""
    return _render(
        "evidence-extractor.md",
        project_key=project_key,
        project_json=project_json,
        session_ref=session_ref,
        session_path=session_path,
        session_index_record=session_index_record,
        target_turn=target_turn,
        check_type_descriptions=_format_enum_values(EVIDENCE_CHECK_TYPES),
        materiality_descriptions=_format_enum_values(EVIDENCE_MATERIALITY_VALUES),
        outcome_category_descriptions=_format_enum_values(EVIDENCE_OUTCOME_CATEGORIES),
        terminal_state_descriptions=_format_enum_values(EVIDENCE_TERMINAL_STATES),
        trigger_type_descriptions=_format_enum_values(EVIDENCE_TRIGGER_TYPES),
    )


def evidence_extractor_next_turn_prompt(
    *,
    write_evidence_result: str,
    target_turn: str,
) -> str:
    """Return the evidence extractor follow-up prompt for the next assigned turn."""
    return _render(
        "evidence-extractor-next-turn.md",
        write_evidence_result=write_evidence_result,
        target_turn=target_turn,
    )


def project_synthesizer_prompt(*, project_key: str, project_json: str, evidence_chains: str) -> str:
    """Return the project synthesizer prompt with substituted workspace values."""
    return _render(
        "project-synthesizer.md",
        project_key=project_key,
        project_json=project_json,
        evidence_chains=evidence_chains,
        work_item_kind_descriptions=_format_enum_values(PROJECT_WORK_ITEM_KINDS),
    )


def daily_synthesizer_prompt() -> str:
    """Return the daily synthesizer prompt."""
    return _render("daily-synthesizer.md")
