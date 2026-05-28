"""Typer command-line entrypoint for Prompt Diary."""

from __future__ import annotations

from typing import Annotated, NoReturn

import typer

from prompt_diary import __version__
from prompt_diary.api import generate_prompt_diary, prepare_prompt_diary
from prompt_diary.codex_bootstrap import bootstrap_codex_sdk
from prompt_diary.errors import PromptDiaryError
from prompt_diary.mcp_server import serve_mcp_server
from prompt_diary.prompts import (
    daily_synthesizer_prompt,
    evidence_extractor_next_turn_prompt,
    evidence_extractor_prompt,
    project_synthesizer_prompt,
)

app = typer.Typer(
    add_completion=False,
    help="Prepare and generate evidence-backed prompt diary reports.",
    invoke_without_command=True,
    no_args_is_help=True,
)

DateOption = Annotated[str | None, typer.Option(help="Target local date in YYYY-MM-DD format.")]
TodayOption = Annotated[bool, typer.Option(help="Target the current local day.")]
TimezoneOption = Annotated[
    str | None,
    typer.Option(help="IANA timezone name, e.g. Asia/Shanghai."),
]
ForceOption = Annotated[bool, typer.Option(help="Recreate an existing workspace.")]
VersionOption = Annotated[
    bool,
    typer.Option("--version", help="Show the version and exit.", is_eager=True),
]


@app.callback()
def app_callback(*, version: VersionOption = False) -> None:
    """Prompt Diary command group."""
    if version:
        typer.echo(__version__)
        raise typer.Exit


@app.command()
def prepare(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
    force: ForceOption = False,
) -> None:
    """Prepare a prompt diary workspace."""
    try:
        result = prepare_prompt_diary(
            date=date,
            today=today,
            timezone_name=timezone,
            force=force,
        )
    except PromptDiaryError as exc:
        _exit_with_error(exc)
    for message in result.messages:
        typer.echo(message)


@app.command()
def generate(
    *,
    date: DateOption = None,
    today: TodayOption = False,
    timezone: TimezoneOption = None,
) -> None:
    """Generate and validate a prompt diary report."""
    try:
        result = generate_prompt_diary(date=date, today=today, timezone_name=timezone)
    except PromptDiaryError as exc:
        _exit_with_error(exc)
    for message in result.messages:
        typer.echo(message)


_prompts_app = typer.Typer(help="Print generation prompts.")
app.add_typer(_prompts_app, name="prompts")

_codex_app = typer.Typer(help="Manage optional Codex SDK support.")
app.add_typer(_codex_app, name="codex")

_mcp_app = typer.Typer(help="Run MCP server commands.")
app.add_typer(_mcp_app, name="mcp")

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


@_prompts_app.command(name="evidence-extractor")
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


@_prompts_app.command(name="evidence-extractor-next-turn")
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


@_prompts_app.command(name="project-synthesizer")
def prompts_project_synthesizer() -> None:
    """Print the project synthesizer prompt."""
    typer.echo(project_synthesizer_prompt())


@_prompts_app.command(name="daily-synthesizer")
def prompts_daily_synthesizer() -> None:
    """Print the daily synthesizer prompt."""
    typer.echo(daily_synthesizer_prompt())


@_mcp_app.command(name="serve")
def mcp_serve() -> None:
    """Run the MCP server over stdio."""
    serve_mcp_server()


@_codex_app.command(name="bootstrap")
def codex_bootstrap() -> None:
    """Install the optional Codex SDK into this runtime environment."""
    try:
        result = bootstrap_codex_sdk()
    except PromptDiaryError as exc:
        _exit_with_error(exc)
    for message in result.messages:
        typer.echo(message)


def _exit_with_error(exc: PromptDiaryError) -> NoReturn:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(2) from exc


def main() -> None:
    app()
