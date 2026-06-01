"""Mock-agent workflow test: real runner + real read_session_lines + real write_evidence.

Only the agent is faked. The fake follows the MCP-only prompt faithfully: it reads the assigned
turn through the real ``read_session_lines`` tool and builds its evidence chain from the line
numbers that read returned, then commits through the real ``write_evidence``. This proves the
runner, the prompt contract, the reader, and the writer integrate end-to-end.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from prompt_diary.generate.evidence_extraction.runner import EvidenceExtractionRunner
from prompt_diary.generate.pipeline import TaskSpec, evidence_card_artifact, evidence_task_id
from tests.support.evidence_agent import EvidenceReadingWritingAgentSessionFactory
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
    load_evidence_card,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult

# Physical line bounds of each assigned turn in the basic-two-turns fixture's session index.
_TURN_BOUNDS: dict[str, tuple[int, int]] = {"T0001": (2, 8), "T0002": (9, 10)}


def _evidence_task() -> TaskSpec:
    return TaskSpec(
        task_id=evidence_task_id(PROJECT_KEY, SESSION_REF),
        kind="evidence_extraction",
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
        output_artifacts=(evidence_card_artifact(PROJECT_KEY, SESSION_REF),),
    )


def _run(factory: EvidenceReadingWritingAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = EvidenceExtractionRunner(agent_factory=factory)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(workspace_path=workspace, task=_evidence_task())

    return asyncio.run(run())


def test_runner_mock_agent_reads_via_read_session_lines_then_writes(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceReadingWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    # Both turns were read then written, in index order.
    assert [(read.session_ref, read.turn_ref) for read in factory.reads] == [
        (SESSION_REF, "T0001"),
        (SESSION_REF, "T0002"),
    ]
    for read in factory.reads:
        start, end = _TURN_BOUNDS[read.turn_ref]
        assert read.result.status == "ok"
        assert read.result.mode == "compact"
        assert read.result.records, "compact read returned no records for the assigned turn"
        # Every record the agent read is an absolute physical line inside the assigned turn.
        assert all(start <= record.line <= end for record in read.result.records)
    # The write the agent committed is tied to what it read: the card has both chains in order.
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]
