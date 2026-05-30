from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from prompt_diary.cmds.generate import build_generation_workflow
from prompt_diary.generate.evidence_extraction.model import (
    ParsedEvidenceChain,
    parse_evidence_chain,
)
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
    load_evidence_card,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.codex_mcp


def test_real_agent_extracts_evidence_for_fixture_session(tmp_path: Path) -> None:
    pytest.importorskip("openai_codex")
    workspace = copy_basic_evidence_workspace(tmp_path)

    result = build_generation_workflow().run_phase(
        workspace_path=workspace,
        phase="evidence",
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
    )

    assert result.task_result.ok
    card = load_evidence_card(workspace)
    turn_refs = [chain["turn_ref"] for chain in card["evidence_chains"]]
    assert turn_refs == ["T0001", "T0002"]
    for chain in card["evidence_chains"]:
        assert isinstance(parse_evidence_chain(chain), ParsedEvidenceChain)
