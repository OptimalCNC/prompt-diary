from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from prompt_diary.agent import AgentConfig
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from prompt_diary.generate.prompts import evidence_extractor_prompt
from tests.support.evidence_agent import EvidenceWritingAgentSessionFactory
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
    load_evidence_card,
)

if TYPE_CHECKING:
    from pathlib import Path


def _first_turn_prompt(workspace: Path) -> str:
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )
    return evidence_extractor_prompt(
        project_key=inputs.project_key,
        project_json=inputs.project_json,
        session_ref=inputs.session_ref,
        session_path=inputs.session_path,
        session_index_record=inputs.session_index_record,
        target_turn=inputs.turns[0].target_turn_json,
    )


def test_fake_parses_prompt_and_writes_evidence(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    prompt = _first_turn_prompt(workspace)
    factory = EvidenceWritingAgentSessionFactory()

    async def run() -> None:
        async with factory:
            runner = await factory.runner(AgentConfig(working_directory=workspace))
            await runner.turn(prompt)

    asyncio.run(run())

    assert factory.processed == [(SESSION_REF, "T0001")]
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001"]


def test_fake_skips_write_for_fail_turns(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    prompt = _first_turn_prompt(workspace)
    factory = EvidenceWritingAgentSessionFactory(fail_turns=frozenset({"T0001"}))

    async def run() -> None:
        async with factory:
            runner = await factory.runner(AgentConfig(working_directory=workspace))
            await runner.turn(prompt)

    asyncio.run(run())

    card_path = workspace / "projects" / PROJECT_KEY / "evidence" / f"{SESSION_REF}.json"
    assert not card_path.exists()
    assert factory.processed == [(SESSION_REF, "T0001")]
