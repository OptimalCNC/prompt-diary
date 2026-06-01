from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from prompt_diary.agent import AgentConfig
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from prompt_diary.generate.prompts import evidence_extractor_prompt
from prompt_diary.integrations.codex_runner import (
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
)
from prompt_diary.mcp.codex_config import prompt_diary_mcp_overrides
from tests.support.evidence_extraction import (
    PROJECT_KEY as EVIDENCE_PROJECT_KEY,
)
from tests.support.evidence_extraction import (
    SESSION_REF as EVIDENCE_SESSION_REF,
)
from tests.support.evidence_extraction import copy_basic_evidence_workspace
from tests.support.session_reader import (
    PROJECT_KEY as READER_PROJECT_KEY,
)
from tests.support.session_reader import (
    SESSION_REF as READER_SESSION_REF,
)
from tests.support.session_reader import copy_session_reader_workspace

if TYPE_CHECKING:
    from prompt_diary.agent import AgentTurnEvent, AgentTurnResult

pytestmark = pytest.mark.codex_mcp

# The basic-two-turns fixture's session transcript lives at this filename; the compliance
# invariant forbids a real agent from shell-reading it directly (see the module docstring).
_SESSION_FILENAME = "session-001.jsonl"
# Raw-read shell tools that, paired with the session filename in a shell command, would mean the
# agent bypassed the MCP read seam. ``read_session_lines`` is the only sanctioned read path.
_RAW_READ_TOOLS = ("cat", "awk", "sed", "grep", "head", "tail", "nl", "jq", "less", "python")


def test_codex_runner_live_replies_pong() -> None:
    pytest.importorskip("openai_codex")
    codex_path = shutil.which("codex")

    async def exercise() -> None:
        async with CodexBackend(
            CodexBackendConfig(codex_bin=Path(codex_path) if codex_path is not None else None)
        ) as backend:
            runner = CodexAgentRunner(
                backend,
                AgentConfig(
                    working_directory=Path.cwd(),
                    approval_mode="deny_all",
                    sandbox="workspace-write",
                ),
            )
            result = await runner.turn("Reply exactly with PONG.", timeout_seconds=30.0)
            assert result.assistant_text.strip() == "PONG"

    asyncio.run(exercise())


def test_codex_runner_live_approved_prompt_diary_mcp_tool_under_auto_review(
    tmp_path: Path,
) -> None:
    pytest.importorskip("openai_codex")
    codex_path = shutil.which("codex")

    async def exercise() -> None:
        async with CodexBackend(
            CodexBackendConfig(
                codex_bin=Path(codex_path) if codex_path is not None else None,
                mcp_config_overrides=prompt_diary_mcp_overrides(tmp_path),
            )
        ) as backend:
            runner = CodexAgentRunner(
                backend,
                AgentConfig(
                    working_directory=tmp_path,
                    approval_mode="auto_review",
                    sandbox="workspace-write",
                ),
            )
            result = await runner.turn(
                "Call the prompt_diary_ping MCP tool exactly once. "
                "If it returns status ok, reply exactly MCP_OK.",
                timeout_seconds=90.0,
            )
            assert result.assistant_text.strip() == "MCP_OK"

    asyncio.run(exercise())


def test_codex_runner_live_approved_read_session_lines_under_auto_review(
    tmp_path: Path,
) -> None:
    pytest.importorskip("openai_codex")
    codex_path = shutil.which("codex")
    workspace = copy_session_reader_workspace(tmp_path)

    async def exercise() -> None:
        async with CodexBackend(
            CodexBackendConfig(
                codex_bin=Path(codex_path) if codex_path is not None else None,
                mcp_config_overrides=prompt_diary_mcp_overrides(workspace),
            )
        ) as backend:
            runner = CodexAgentRunner(
                backend,
                AgentConfig(
                    working_directory=tmp_path,
                    approval_mode="auto_review",
                    sandbox="workspace-write",
                ),
            )
            result = await runner.turn(
                "Call the read_session_lines MCP tool exactly once with "
                f'project_key="{READER_PROJECT_KEY}", session_ref="{READER_SESSION_REF}", '
                "start_line=4, end_line=6, mode=compact. "
                "If the result status is ok, reply exactly READ_OK.",
                timeout_seconds=90.0,
            )
            assert result.assistant_text.strip() == "READ_OK"

    asyncio.run(exercise())


def _event_haystacks(event: AgentTurnEvent) -> tuple[str, str, str]:
    """Return the three lower-cased text surfaces a compliance check searches per event.

    ``_agent_turn_event`` maps SDK items so the called tool name lands in ``.summary`` (via the
    item's ``name``/``command`` field) and ``.kind`` (the item ``type``), while ``.metadata`` is
    the full ``model_dump`` where nested ``payload`` fields (e.g. ``payload.arguments.cmd``) live.
    Searching all three tolerates codex item-shape variation.
    """
    return (event.summary.lower(), event.kind.lower(), str(event.metadata).lower())


def _read_session_lines_was_called(result: AgentTurnResult) -> bool:
    """True when some turn event references the ``read_session_lines`` MCP tool by name.

    A real Codex run surfaces this as a ``function_call`` payload whose ``name`` is the tool, so
    the tool name appears in the event ``summary`` and/or the dumped ``metadata``.
    """
    return any(
        any("read_session_lines" in surface for surface in _event_haystacks(event))
        for event in result.events
    )


def _session_file_was_shell_read(result: AgentTurnResult) -> bool:
    """True when any event is a shell command that raw-reads the session ``.jsonl`` file.

    The metadata of a shell event (e.g. ``exec_command``) carries the command text under
    ``payload.arguments.cmd``; a violation is one surface that holds the session filename, a
    raw-read tool (``cat``/``awk``/``sed``/...), and a shell-command marker all together.
    ``mode="full"`` reads through ``read_session_lines`` are NOT violations and are not detected
    here, because they are MCP tool calls rather than shell command executions. Prompt/user-message
    events are also ignored because they may contain the compliance rules themselves.
    """
    for event in result.events:
        if event.kind.lower() != "commandexecution":
            continue
        for surface in _event_haystacks(event):
            if _SESSION_FILENAME in surface and any(tool in surface for tool in _RAW_READ_TOOLS):
                return True
    return False


def test_codex_runner_live_evidence_prompt_reads_only_via_read_session_lines(
    tmp_path: Path,
) -> None:
    """Real-agent compliance: the evidence extractor reads sessions ONLY via the MCP tool.

    Renders the real first-turn evidence-extractor prompt for the basic-two-turns fixture's
    T0001 and runs it against a live Codex agent. The fixture-agnostic invariant is that the
    agent reads transcript content through ``read_session_lines`` and never shell-reads the raw
    ``session-001.jsonl`` (``cat``/``awk``/``sed``/...). ``mode="full"`` remains a legitimate
    escape hatch, so this asserts tool-use + no-raw-shell-read, not "compact only".

    Requires the optional ``openai_codex`` SDK and a ``codex`` binary; it SKIPS otherwise (and in
    the dev container, where the SDK is absent). Compliance was additionally confirmed via a live
    ``codex exec`` run, in which the agent read transcript lines solely through
    ``read_session_lines`` and issued no shell command touching the session ``.jsonl``.
    """
    pytest.importorskip("openai_codex")
    codex_path = shutil.which("codex")
    workspace = copy_basic_evidence_workspace(tmp_path)

    inputs = build_session_extraction_inputs(
        workspace_path=workspace,
        project_key=EVIDENCE_PROJECT_KEY,
        session_ref=EVIDENCE_SESSION_REF,
    )
    prompt = evidence_extractor_prompt(
        project_key=inputs.project_key,
        project_json=inputs.project_json,
        session_ref=inputs.session_ref,
        session_index_record=inputs.session_index_record,
        target_turn=inputs.turns[0].target_turn_json,
    )

    async def exercise() -> AgentTurnResult:
        async with CodexBackend(
            CodexBackendConfig(
                codex_bin=Path(codex_path) if codex_path is not None else None,
                mcp_config_overrides=prompt_diary_mcp_overrides(workspace),
            )
        ) as backend:
            runner = CodexAgentRunner(
                backend,
                AgentConfig(
                    working_directory=workspace,
                    approval_mode="auto_review",
                    sandbox="workspace-write",
                ),
            )
            return await runner.turn(prompt, timeout_seconds=300.0)

    result = asyncio.run(exercise())

    assert _read_session_lines_was_called(result), (
        "expected the agent to read transcript content via the read_session_lines MCP tool; "
        f"events={result.events!r}"
    )
    assert not _session_file_was_shell_read(result), (
        f"expected no shell raw-read of {_SESSION_FILENAME}; events={result.events!r}"
    )
