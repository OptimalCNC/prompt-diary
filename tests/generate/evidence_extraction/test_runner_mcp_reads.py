"""Mock-agent workflow test: real runner + real read_session_lines + real write_evidence.

Only the agent is faked. The fake follows the MCP-only prompt faithfully: it reads the assigned
turn through the real ``read_session_lines`` tool and builds its evidence chain from the line
numbers that read returned, then commits through the real ``write_evidence``. This proves the
runner, the prompt contract, the reader, and the writer integrate end-to-end.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

import pytest

from prompt_diary.generate.evidence_extraction.runner import EvidenceExtractionRunner
from prompt_diary.generate.evidence_extraction.session_compaction import CompactRecord
from prompt_diary.generate.evidence_extraction.session_reader import (
    ReadSessionLinesCompactResult,
    read_session_lines,
)
from prompt_diary.generate.pipeline import TaskSpec, evidence_card_artifact, evidence_task_id
from tests.support.evidence_agent import (
    EvidenceReadingWritingAgentRunner,
    EvidenceReadingWritingAgentSessionFactory,
)
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
    load_evidence_card,
)
from tests.support.session_reader import session_file_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult

# Physical line bounds of each assigned turn in the basic-two-turns fixture's session index.
_TURN_BOUNDS: dict[str, tuple[int, int]] = {"T0001": (2, 8), "T0002": (9, 10)}


def _citation_lines(node: dict[str, Any]) -> str:
    """Return the single ``lines`` citation string of a chain node (trigger/terminal_state)."""
    citations = cast("list[dict[str, Any]]", node["citations"])
    assert len(citations) == 1, f"expected exactly one citation, got {citations!r}"
    return cast("str", citations[0]["lines"])


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
        # Exhausting the requested cursor sequence establishes coverage even when compact
        # records omit physical lines that contain only source metadata or reasoning.
        assert (read.result.line_range.start, read.result.line_range.end) == (start, end)
        assert read.result.next_cursor is None
    # The write the agent committed is tied to what it read: the card has both chains in order.
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]
    # Citations refer to the evidence actually returned, independently of cursor coverage.
    for chain in card["evidence_chains"]:
        start, end = _TURN_BOUNDS[chain["turn_ref"]]
        assert _citation_lines(chain["trigger"]) == f"{start}-{start}"
        assert _citation_lines(chain["terminal_state"]) == f"{end}-{end}"


def test_runner_read_completes_when_last_physical_line_is_omitted(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    path = session_file_path(workspace)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = '{"type":"turn_context"}'
    lines[7] = '{"type":"turn_context"}'
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    factory = EvidenceReadingWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    first = factory.reads[0].result
    assert (first.line_range.start, first.line_range.end) == (2, 8)
    assert first.next_cursor is None
    assert [record.line for record in first.records] == [2, 4, 5, 6, 7]
    chain = load_evidence_card(workspace)["evidence_chains"][0]
    assert _citation_lines(chain["trigger"]) == "2-2"
    assert _citation_lines(chain["terminal_state"]) == "7-7"


def test_fresh_continuation_finds_previous_trigger_from_assignment_locator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    project_dir = workspace / "projects" / PROJECT_KEY
    index_path = project_dir / "sessions.index.jsonl"
    row = json.loads(index_path.read_text(encoding="utf-8"))
    session_path = project_dir / row["session_path"]
    lines = session_path.read_text(encoding="utf-8").splitlines()
    for index in (1, 8):
        message = json.loads(lines[index])["content"]
        lines[index] = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": message}],
                },
            }
        )
    padding = [json.dumps({"role": "assistant", "content": "Work in progress."})] * 100
    session_path.write_text("\n".join([*lines[:7], *padding, *lines[7:]]) + "\n", encoding="utf-8")
    row["turns"][0]["turn_end_line"] += len(padding)
    row["turns"][1]["turn_start_line"] += len(padding)
    row["turns"][1]["turn_end_line"] += len(padding)
    row["target_end_line"] += len(padding)
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    context_reads: list[ReadSessionLinesCompactResult] = []
    original_read = EvidenceReadingWritingAgentRunner.read_assigned_turn

    def read_with_continuation_context(
        self: EvidenceReadingWritingAgentRunner,
        project_key: str,
        session_ref: str,
        target_turn: dict[str, Any],
    ) -> Iterator[ReadSessionLinesCompactResult]:
        pages = tuple(original_read(self, project_key, session_ref, target_turn))
        if any(
            isinstance(record, CompactRecord) and record.text == "continue"
            for page in pages
            for record in page.records
        ):
            # The fresh conversation discovers this line from its prompt, not a known fixture span.
            previous_trigger = target_turn["previous_turn"]["turn_start_line"]
            context = read_session_lines(
                workspace_path=self.config.working_directory,
                project_key=project_key,
                session_ref=session_ref,
                start_line=previous_trigger,
                end_line=previous_trigger,
                mode="compact",
            )
            assert isinstance(context, ReadSessionLinesCompactResult)
            context_reads.append(context)
        yield from pages

    monkeypatch.setattr(
        EvidenceReadingWritingAgentRunner, "read_assigned_turn", read_with_continuation_context
    )
    factory = EvidenceReadingWritingAgentSessionFactory()

    assert _run(factory, workspace).status == "success"

    assert len(factory.runners) == 2
    assert [record.line for context in context_reads for record in context.records] == [2]
    previous_trigger = context_reads[0].records[0]
    assert isinstance(previous_trigger, CompactRecord)
    assert previous_trigger.text == (
        "Please update the evidence contract docs for the MCP write surface."
    )
    continued = load_evidence_card(workspace)["evidence_chains"][1]
    assert _citation_lines(continued["trigger"]) == "109-109"
    assert _citation_lines(continued["terminal_state"]) == "110-110"


@pytest.mark.parametrize("interrupt_page", [False, True])
def test_runner_mock_agent_reads_all_pages_before_writing_the_assigned_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, interrupt_page: bool
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    path = session_file_path(workspace)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Detailed requirement. " * 10_000}],
            },
        }
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    factory = EvidenceReadingWritingAgentSessionFactory()
    original_read = EvidenceReadingWritingAgentRunner.read_assigned_turn
    interrupted = False
    read_error = RuntimeError("transient page read")

    def possibly_interrupted_read(
        self: EvidenceReadingWritingAgentRunner,
        project_key: str,
        session_ref: str,
        target_turn: dict[str, Any],
    ) -> Iterator[ReadSessionLinesCompactResult]:
        nonlocal interrupted
        for page in original_read(self, project_key, session_ref, target_turn):
            yield page
            if interrupt_page and page.next_cursor is not None and not interrupted:
                interrupted = True
                raise read_error

    monkeypatch.setattr(
        EvidenceReadingWritingAgentRunner, "read_assigned_turn", possibly_interrupted_read
    )

    result = _run(factory, workspace)

    assert result.status == "success"
    pages = [read.result for read in factory.reads if read.turn_ref == "T0001"]
    assert len(pages) > 1
    assert all(page.next_cursor is not None for page in pages[:-1])
    assert pages[-1].next_cursor is None
    assert pages[-1].records[-1].line == 8
    card = load_evidence_card(workspace)
    assert _citation_lines(card["evidence_chains"][0]["terminal_state"]) == "8-8"
    assert len(factory.runners) == 2
    assert [len(runner.prompts) for runner in factory.runners] == [2 if interrupt_page else 1, 1]
    if interrupt_page:
        assert "```json" not in factory.runners[0].prompts[1]
