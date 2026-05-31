"""Prompt-template command registration."""

from __future__ import annotations

from typing import Annotated

import typer

from prompt_diary.generate.prompts import (
    daily_synthesizer_prompt,
    evidence_extractor_next_turn_prompt,
    evidence_extractor_prompt,
    project_synthesizer_prompt,
)

ProjectKeyOption = Annotated[str, typer.Option(help="Project key for template substitution.")]
ProjectJsonOption = Annotated[
    str, typer.Option(help="project.json content for template substitution.")
]
SessionRefOption = Annotated[str, typer.Option(help="Session reference for template substitution.")]
SessionPathOption = Annotated[
    str, typer.Option(help="Workspace-root-relative session path for template substitution.")
]
SessionIndexRecordOption = Annotated[
    str, typer.Option(help="Session index record without turns for template substitution.")
]
TargetTurnOption = Annotated[str, typer.Option(help="Target turn for template substitution.")]
WriteEvidenceResultOption = Annotated[
    str, typer.Option(help="write_evidence result for template substitution.")
]
EvidenceChainsOption = Annotated[
    str, typer.Option(help="Trimmed evidence chains (summaries) for template substitution.")
]


def register(app: typer.Typer) -> None:
    """Register prompt-template commands."""
    prompts_app = typer.Typer(help="Print generation prompts.")
    prompts_app.command(name="evidence-extractor")(prompts_evidence_extractor)
    prompts_app.command(name="evidence-extractor-next-turn")(prompts_evidence_extractor_next_turn)
    prompts_app.command(name="project-synthesizer")(prompts_project_synthesizer)
    prompts_app.command(name="daily-synthesizer")(prompts_daily_synthesizer)
    app.add_typer(prompts_app, name="prompts")


def prompts_evidence_extractor(
    *,
    project_key: ProjectKeyOption = "<PROJECT_KEY>",
    project_json: ProjectJsonOption = "<PROJECT_JSON>",
    session_ref: SessionRefOption = "<SESSION_REF>",
    session_path: SessionPathOption = "<SESSION_PATH>",
    session_index_record: SessionIndexRecordOption = "<SESSION_INDEX_RECORD>",
    target_turn: TargetTurnOption = "<TARGET_TURN>",
) -> None:
    """Print the evidence extractor prompt."""
    typer.echo(
        evidence_extractor_prompt(
            project_key=project_key,
            project_json=project_json,
            session_ref=session_ref,
            session_path=session_path,
            session_index_record=session_index_record,
            target_turn=target_turn,
        )
    )


def prompts_evidence_extractor_next_turn(
    *,
    write_evidence_result: WriteEvidenceResultOption = "<WRITE_EVIDENCE_RESULT>",
    target_turn: TargetTurnOption = "<TARGET_TURN>",
) -> None:
    """Print the evidence extractor next-turn prompt."""
    typer.echo(
        evidence_extractor_next_turn_prompt(
            write_evidence_result=write_evidence_result,
            target_turn=target_turn,
        )
    )


def prompts_project_synthesizer(
    *,
    project_key: ProjectKeyOption = "<PROJECT_KEY>",
    project_json: ProjectJsonOption = "<PROJECT_JSON>",
    evidence_chains: EvidenceChainsOption = "<EVIDENCE_CHAINS>",
) -> None:
    """Print the project synthesizer prompt."""
    typer.echo(
        project_synthesizer_prompt(
            project_key=project_key,
            project_json=project_json,
            evidence_chains=evidence_chains,
        )
    )


def prompts_daily_synthesizer() -> None:
    """Print the daily synthesizer prompt."""
    typer.echo(daily_synthesizer_prompt())
