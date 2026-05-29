# Evidence Extraction Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the evidence extraction phase: a runner that drives an agent to write one evidence chain per indexed turn of a session, validated against a mocked agent, with real-agent wiring proven by an opt-in Codex integration test.

**Architecture:** A new `inputs.py` builds per-turn extractor prompt inputs from the prepared workspace. `EvidenceExtractionRunner.run` resets the session card, drives one agent conversation turn-by-turn (full prompt then next-turn prompts), and verifies each commit by reading the card. The real agent reaches `write_evidence` through the package MCP server, which resolves its workspace from an env var; the workflow builds a workspace-aware agent factory per run.

**Tech Stack:** Python 3.10+, `uv`, `pytest`, `jinja2`, `basedpyright`, `ruff`, optional `openai_codex` SDK.

**Spec:** `docs/superpowers/specs/2026-05-30-evidence-extraction-phase-design.md`

**Per-task review (controller):** After each task, dispatch paired reviewers — Claude **and** Codex — for each of: spec-compliance, code-quality, docs-and-plan-compliance. Fix loops until all approve before marking complete.

---

## File Structure

**Create (src):**
- `src/prompt_diary/generate/evidence_extraction/inputs.py` — per-turn extractor prompt inputs.
- `src/prompt_diary/mcp/codex_config.py` — `prompt_diary_mcp_overrides(workspace_path)`.

**Modify (src):**
- `src/prompt_diary/generate/evidence_extraction/model.py` — add `new_session_card`.
- `src/prompt_diary/generate/evidence_extraction/mcp.py` — `_new_card` delegates to `new_session_card`.
- `src/prompt_diary/generate/evidence_extraction/runner.py` — implement `run`.
- `src/prompt_diary/mcp/server.py` — resolve workspace from `PROMPT_DIARY_WORKSPACE`.
- `src/prompt_diary/generate/workflow.py` — per-run agent-factory + phase-runner builders.
- `src/prompt_diary/cmds/generate.py` — wire workspace-aware builders.

**Create (tests):**
- `tests/support/evidence_agent.py` — prompt-reading evidence-writing fake agent.
- `tests/generate/evidence_extraction/test_inputs.py`
- `tests/generate/evidence_extraction/test_runner.py`
- `tests/mcp/test_codex_config.py`
- `tests/integration/__init__.py`, `tests/integration/test_evidence_extraction_codex.py`

**Modify (tests):**
- `tests/support/evidence_extraction.py` — add `build_evidence_chain`.
- `tests/mcp/test_server.py` — add env-var resolution test.
- `tests/generate/test_workflow.py` — update to builder seam.

**Docs:** `docs/src/dev/generation-pipeline.md`, `README.md`.

---

## Task 1: `build_evidence_chain` test helper

**Files:**
- Modify: `tests/support/evidence_extraction.py`
- Test: `tests/support/evidence_extraction.py` is exercised via `tests/generate/evidence_extraction/test_runner.py` later; add a focused test now in `tests/generate/evidence_extraction/test_build_chain.py`
- Create: `tests/generate/evidence_extraction/test_build_chain.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/generate/evidence_extraction/test_build_chain.py
from __future__ import annotations

from typing import TYPE_CHECKING

from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    assert_appended_result,
    build_evidence_chain,
    call_write_evidence_api,
    copy_basic_evidence_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_build_material_chain_is_accepted_for_full_turn_span(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = build_evidence_chain(turn_ref="T0001", span=(2, 8))
    result = call_write_evidence_api(workspace_path=workspace, evidence_chain=chain)
    assert_appended_result(result, turn_ref="T0001")


def test_build_material_chain_is_accepted_for_single_line_turn(tmp_path: Path) -> None:
    # T0002 spans lines 9-10; use a 1-line sub-window to prove the material check holds.
    workspace = copy_basic_evidence_workspace(tmp_path)
    chain = build_evidence_chain(turn_ref="T0002", span=(10, 10))
    result = call_write_evidence_api(
        workspace_path=workspace, session_ref=SESSION_REF, evidence_chain=chain
    )
    assert_appended_result(result, turn_ref="T0002")


def test_build_no_material_chain_has_empty_outcomes(tmp_path: Path) -> None:
    chain = build_evidence_chain(turn_ref="T0002", span=(9, 10), kind="no_material")
    assert chain["outcomes"] == []
    assert chain["terminal_state"]["type"] == "no_material"
    assert chain["materiality"] == "none"
    assert PROJECT_KEY  # import sanity
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generate/evidence_extraction/test_build_chain.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_evidence_chain'`.

- [ ] **Step 3: Add `build_evidence_chain` to `tests/support/evidence_extraction.py`**

Append (after `valid_no_material_chain`):

```python
def build_evidence_chain(
    *,
    turn_ref: str,
    span: tuple[int, int],
    kind: str = "material",
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
    reactions = [{"summary": f"Agent reaction for {turn_ref}.", "citations": [{"lines": end_lines}]}]
    if kind == "material":
        outcomes: list[dict[str, Any]] = [
            {
                "category": "document_outcome",
                "summary": f"Material result for {turn_ref}.",
                "citations": [{"lines": end_lines}],
            }
        ]
        terminal = {
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generate/evidence_extraction/test_build_chain.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint/type/format**

Run: `uv run ruff check tests/ && uv run ruff format --check tests/ && uv run basedpyright`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add tests/support/evidence_extraction.py tests/generate/evidence_extraction/test_build_chain.py
git commit -m "test: add span-sized evidence chain builder"
```

---

## Task 2: `inputs.py` — session extraction inputs

**Files:**
- Create: `src/prompt_diary/generate/evidence_extraction/inputs.py`
- Test: `tests/generate/evidence_extraction/test_inputs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/generate/evidence_extraction/test_inputs.py
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.evidence_extraction.inputs import build_session_extraction_inputs
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_inputs_resolve_session_path_and_strip_turns(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert inputs.session_path == f"projects/{PROJECT_KEY}/sessions/codex/session-001.jsonl"
    record = json.loads(inputs.session_index_record)
    assert "turns" not in record
    assert record["session_ref"] == SESSION_REF
    assert json.loads(inputs.project_json)["project_key"] == PROJECT_KEY


def test_inputs_preserve_raw_target_turn_fields_in_order(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )

    assert [turn.turn_ref for turn in inputs.turns] == ["T0001", "T0002"]
    first = json.loads(inputs.turns[0].target_turn_json)
    assert first["turn_ref"] == "T0001"
    assert first["turn_start_line"] == 2
    assert first["turn_end_line"] == 8
    assert "target_subagents" in first  # raw field preserved
    assert inputs.turns[0].span.start == 2
    assert inputs.turns[0].span.end == 8


def test_inputs_reject_unknown_session(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    with pytest.raises(PromptDiaryError, match="unknown session_ref"):
        build_session_extraction_inputs(
            workspace_path=workspace, project_key=PROJECT_KEY, session_ref="S9999"
        )


def test_inputs_reject_unknown_project(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    with pytest.raises(PromptDiaryError, match="unknown project_key"):
        build_session_extraction_inputs(
            workspace_path=workspace, project_key="Missing-000", session_ref=SESSION_REF
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generate/evidence_extraction/test_inputs.py -v`
Expected: FAIL with `ModuleNotFoundError: ...inputs`.

- [ ] **Step 3: Create `inputs.py`**

```python
"""Build evidence extractor prompt inputs for one indexed session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.workspace import (
        IndexedSession,
        LineSpan,
        PreparedProject,
        PreparedWorkspace,
    )


@dataclass(frozen=True)
class ExtractionTurn:
    """One assigned turn with its verified span and faithful target-turn JSON."""

    turn_ref: str
    span: LineSpan
    target_turn_json: str


@dataclass(frozen=True)
class SessionExtractionInputs:
    """Rendered-ready inputs for extracting one session's evidence chains."""

    project_key: str
    session_ref: str
    project_json: str
    session_path: str
    session_index_record: str
    turns: tuple[ExtractionTurn, ...]


def build_session_extraction_inputs(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
) -> SessionExtractionInputs:
    """Build prompt inputs for one indexed session from the prepared workspace."""
    workspace = load_prepared_workspace(workspace_path)
    project = _find_project(workspace, project_key)
    session = _find_session(project, session_ref, project_key)

    project_dir = workspace_path / "projects" / project_key
    raw_row = _find_index_row(project_dir / "sessions.index.jsonl", session_ref, project_key)
    raw_turns = _raw_turns_by_ref(raw_row)
    record_without_turns = {key: value for key, value in raw_row.items() if key != "turns"}

    turns = tuple(
        ExtractionTurn(
            turn_ref=turn.turn_ref,
            span=turn.span,
            target_turn_json=json.dumps(raw_turns[turn.turn_ref], indent=2, ensure_ascii=False),
        )
        for turn in session.turns
    )
    return SessionExtractionInputs(
        project_key=project_key,
        session_ref=session_ref,
        project_json=_normalized_json(project_dir / "project.json"),
        session_path=f"projects/{project_key}/{session.session_path.as_posix()}",
        session_index_record=json.dumps(record_without_turns, indent=2, ensure_ascii=False),
        turns=turns,
    )


def _find_project(workspace: PreparedWorkspace, project_key: str) -> PreparedProject:
    project = next((item for item in workspace.projects if item.project_key == project_key), None)
    if project is None:
        raise PromptDiaryError(f"unknown project_key {project_key!r} in prepared workspace")
    return project


def _find_session(
    project: PreparedProject,
    session_ref: str,
    project_key: str,
) -> IndexedSession:
    session = next((item for item in project.sessions if item.session_ref == session_ref), None)
    if session is None:
        raise PromptDiaryError(
            f"unknown session_ref {session_ref!r} for project {project_key!r}"
        )
    return session


def _find_index_row(index_path: Path, session_ref: str, project_key: str) -> dict[str, Any]:
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw: object = json.loads(line)
        if isinstance(raw, dict) and raw.get("session_ref") == session_ref:
            return cast("dict[str, Any]", raw)
    raise PromptDiaryError(
        f"unknown session_ref {session_ref!r} for project {project_key!r} in {index_path}"
    )


def _raw_turns_by_ref(raw_row: dict[str, Any]) -> dict[str, Any]:
    turns = raw_row.get("turns")
    rows = cast("list[Any]", turns) if isinstance(turns, list) else []
    return {
        cast("dict[str, Any]", turn)["turn_ref"]: turn
        for turn in rows
        if isinstance(turn, dict)
    }


def _normalized_json(path: Path) -> str:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(raw, indent=2, ensure_ascii=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generate/evidence_extraction/test_inputs.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint/type/format**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run basedpyright`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/prompt_diary/generate/evidence_extraction/inputs.py tests/generate/evidence_extraction/test_inputs.py
git commit -m "feat: build evidence extractor prompt inputs for a session"
```

---

## Task 3: `new_session_card` skeleton + mcp dedup

**Files:**
- Modify: `src/prompt_diary/generate/evidence_extraction/model.py`
- Modify: `src/prompt_diary/generate/evidence_extraction/mcp.py:260-266` (`_new_card`)
- Test: `tests/generate/evidence_extraction/test_model.py` (create if absent; otherwise append)

- [ ] **Step 1: Write the failing test**

```python
# tests/generate/evidence_extraction/test_model.py  (append if file exists)
from __future__ import annotations

from prompt_diary.generate.evidence_extraction.model import new_session_card


def test_new_session_card_skeleton() -> None:
    card = new_session_card("Proj-1", "S0001")
    assert card == {
        "schema_version": 1,
        "project_key": "Proj-1",
        "session_ref": "S0001",
        "evidence_chains": [],
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generate/evidence_extraction/test_model.py::test_new_session_card_skeleton -v`
Expected: FAIL with `ImportError: cannot import name 'new_session_card'`.

- [ ] **Step 3: Add `new_session_card` to `model.py`**

Add near the bottom of `model.py` (before the private message helpers):

```python
def new_session_card(project_key: str, session_ref: str) -> dict[str, Any]:
    """Return the canonical empty session-evidence-card skeleton."""
    return {
        "schema_version": 1,
        "project_key": project_key,
        "session_ref": session_ref,
        "evidence_chains": [],
    }
```

- [ ] **Step 4: Point `mcp.py::_new_card` at the shared helper**

In `src/prompt_diary/generate/evidence_extraction/mcp.py`, update the import block:

```python
from prompt_diary.generate.evidence_extraction.model import (
    CitationSpan,
    EvidenceChain,
    EvidenceWriteError,
    InvalidEvidenceChain,
    Outcome,
    evidence_chain_to_json,
    new_session_card,
    parse_evidence_chain,
)
```

Replace the body of `_new_card`:

```python
def _new_card(project_key: str, session_ref: str) -> dict[str, Any]:
    return new_session_card(project_key, session_ref)
```

- [ ] **Step 5: Run tests to verify pass (model + existing write API + MCP server)**

Run: `uv run pytest tests/generate/evidence_extraction tests/mcp -v`
Expected: PASS (including the existing `test_write_api.py` and `test_server.py`).

- [ ] **Step 6: Lint/type/format**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run basedpyright`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/prompt_diary/generate/evidence_extraction/model.py src/prompt_diary/generate/evidence_extraction/mcp.py tests/generate/evidence_extraction/test_model.py
git commit -m "feat: share canonical empty session-card skeleton"
```

---

## Task 4: Evidence-writing fake agent

**Files:**
- Create: `tests/support/evidence_agent.py`
- Test: `tests/generate/evidence_extraction/test_evidence_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/generate/evidence_extraction/test_evidence_agent.py
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


def test_fake_parses_prompt_and_writes_evidence(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )
    prompt = evidence_extractor_prompt(
        project_key=inputs.project_key,
        project_json=inputs.project_json,
        session_ref=inputs.session_ref,
        session_path=inputs.session_path,
        session_index_record=inputs.session_index_record,
        target_turn=inputs.turns[0].target_turn_json,
    )
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
    inputs = build_session_extraction_inputs(
        workspace_path=workspace, project_key=PROJECT_KEY, session_ref=SESSION_REF
    )
    prompt = evidence_extractor_prompt(
        project_key=inputs.project_key,
        project_json=inputs.project_json,
        session_ref=inputs.session_ref,
        session_path=inputs.session_path,
        session_index_record=inputs.session_index_record,
        target_turn=inputs.turns[0].target_turn_json,
    )
    factory = EvidenceWritingAgentSessionFactory(fail_turns=frozenset({"T0001"}))

    async def run() -> None:
        async with factory:
            runner = await factory.runner(AgentConfig(working_directory=workspace))
            await runner.turn(prompt)

    asyncio.run(run())

    card_path = workspace / "projects" / PROJECT_KEY / "evidence" / f"{SESSION_REF}.json"
    assert not card_path.exists()
    assert factory.processed == [(SESSION_REF, "T0001")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generate/evidence_extraction/test_evidence_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: tests.support.evidence_agent`.

- [ ] **Step 3: Create `tests/support/evidence_agent.py`**

```python
"""Prompt-reading fake agent that performs the real write_evidence side effect."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentTurnResult
from prompt_diary.generate.evidence_extraction.mcp import write_evidence
from tests.support.evidence_extraction import build_evidence_chain

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prompt_diary.agent import AgentConfig

_JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_PROJECT_KEY_RE = re.compile(r"^- Project key: (.+)$", re.MULTILINE)
_SESSION_REF_RE = re.compile(r"^- Session reference: (.+)$", re.MULTILINE)


@dataclass
class EvidenceWritingAgentRunner:
    """One per-session fake conversation that writes evidence via the real API."""

    config: AgentConfig
    processed: list[tuple[str, str]]
    fail_turns: frozenset[str]
    prompts: list[str] = field(default_factory=list)
    project_key: str | None = None
    session_ref: str | None = None

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del timeout_seconds, output_schema
        self.prompts.append(prompt)
        target_turn = _last_json_block(prompt)
        turn_ref = cast("str", target_turn["turn_ref"])
        self.project_key = _parse_first(_PROJECT_KEY_RE, prompt) or self.project_key
        self.session_ref = _parse_first(_SESSION_REF_RE, prompt) or self.session_ref
        project_key = _require(self.project_key, "project_key")
        session_ref = _require(self.session_ref, "session_ref")
        if turn_ref not in self.fail_turns:
            span = (int(target_turn["turn_start_line"]), int(target_turn["turn_end_line"]))
            write_evidence(
                workspace_path=self.config.working_directory,
                project_key=project_key,
                session_ref=session_ref,
                evidence_chain=build_evidence_chain(turn_ref=turn_ref, span=span),
            )
        self.processed.append((session_ref, turn_ref))
        return AgentTurnResult(assistant_text=f"processed {turn_ref}", events=())


@dataclass
class EvidenceWritingAgentSessionFactory:
    """Mints per-session evidence-writing fake runners off a shared record."""

    fail_turns: frozenset[str] = frozenset()
    entered: int = 0
    exited: int = 0
    processed: list[tuple[str, str]] = field(default_factory=list)
    runners: list[EvidenceWritingAgentRunner] = field(default_factory=list)

    async def __aenter__(self) -> EvidenceWritingAgentSessionFactory:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None:
        del exc_type, exc, traceback
        self.exited += 1

    async def runner(self, config: AgentConfig) -> EvidenceWritingAgentRunner:
        new_runner = EvidenceWritingAgentRunner(
            config=config, processed=self.processed, fail_turns=self.fail_turns
        )
        self.runners.append(new_runner)
        return new_runner


def _last_json_block(prompt: str) -> dict[str, Any]:
    blocks = _JSON_BLOCK_RE.findall(prompt)
    if not blocks:
        raise AssertionError("prompt has no ```json block to read the target turn from")
    raw: object = json.loads(blocks[-1])
    return cast("dict[str, Any]", raw)


def _parse_first(pattern: re.Pattern[str], prompt: str) -> str | None:
    match = pattern.search(prompt)
    return match.group(1).strip() if match else None


def _require(value: str | None, label: str) -> str:
    if value is None:
        raise AssertionError(f"fake agent could not determine {label} from the prompt")
    return value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generate/evidence_extraction/test_evidence_agent.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint/type/format**

Run: `uv run ruff check tests && uv run ruff format --check tests && uv run basedpyright`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add tests/support/evidence_agent.py tests/generate/evidence_extraction/test_evidence_agent.py
git commit -m "test: add prompt-reading evidence-writing fake agent"
```

---

## Task 5: `EvidenceExtractionRunner.run`

**Files:**
- Modify: `src/prompt_diary/generate/evidence_extraction/runner.py`
- Test: `tests/generate/evidence_extraction/test_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/generate/evidence_extraction/test_runner.py
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from prompt_diary.generate.evidence_extraction.runner import EvidenceExtractionRunner
from prompt_diary.generate.pipeline import TaskSpec, evidence_card_artifact, evidence_task_id
from tests.support.evidence_agent import EvidenceWritingAgentSessionFactory
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
    load_evidence_card,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult


def _evidence_task() -> TaskSpec:
    return TaskSpec(
        task_id=evidence_task_id(PROJECT_KEY, SESSION_REF),
        kind="evidence_extraction",
        project_key=PROJECT_KEY,
        session_ref=SESSION_REF,
        output_artifacts=(evidence_card_artifact(PROJECT_KEY, SESSION_REF),),
    )


def _run(factory: EvidenceWritingAgentSessionFactory, workspace: Path) -> TaskResult:
    runner = EvidenceExtractionRunner(agent_factory=factory)

    async def run() -> TaskResult:
        async with factory:
            return await runner.run(workspace_path=workspace, task=_evidence_task())

    return asyncio.run(run())


def test_runner_extracts_all_turns_in_index_order(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert factory.processed == [(SESSION_REF, "T0001"), (SESSION_REF, "T0002")]
    assert len(factory.runners) == 1  # one conversation per session
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]


def test_runner_second_turn_uses_next_turn_prompt_with_prior_result(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory()

    _run(factory, workspace)

    prompts = factory.runners[0].prompts
    assert len(prompts) == 2
    assert "## Role" in prompts[0]  # full extractor prompt
    assert "The previous turn was written successfully." in prompts[1]
    assert '"turn_ref": "T0001"' in prompts[1]  # prior committed result fed forward
    assert prompts[1].rstrip().endswith("}") is False or '"turn_ref": "T0002"' in prompts[1]


def test_runner_resets_a_preexisting_partial_card(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    card_path = workspace / evidence_card_artifact(PROJECT_KEY, SESSION_REF).path
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_key": PROJECT_KEY,
                "session_ref": SESSION_REF,
                "evidence_chains": [{"turn_ref": "T0001", "stale": True}],
            }
        ),
        encoding="utf-8",
    )
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001", "T0002"]
    assert all("stale" not in chain for chain in card["evidence_chains"])  # rebuilt fresh


def test_runner_fails_when_a_turn_is_not_committed(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    factory = EvidenceWritingAgentSessionFactory(fail_turns=frozenset({"T0002"}))

    result = _run(factory, workspace)

    assert result.status == "failed"
    assert any("T0002" in error for error in result.errors)
    card = load_evidence_card(workspace)
    assert [chain["turn_ref"] for chain in card["evidence_chains"]] == ["T0001"]  # partial


def test_runner_writes_empty_card_for_zero_turn_session(tmp_path: Path) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    _strip_turns_from_index(workspace)
    factory = EvidenceWritingAgentSessionFactory()

    result = _run(factory, workspace)

    assert result.status == "success"
    assert factory.processed == []
    card = load_evidence_card(workspace)
    assert card["evidence_chains"] == []


def _strip_turns_from_index(workspace: Path) -> None:
    index_path = workspace / "projects" / PROJECT_KEY / "sessions.index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        row["turns"] = []
    index_path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")
```

> Note for QA subagent: the assertion on `prompts[1]` checking the next-turn marker and prior
> `turn_ref` is the key cross-check; keep it concrete. Remove the awkward last line of
> `test_runner_second_turn_uses_next_turn_prompt_with_prior_result` and instead assert
> `'"turn_ref": "T0002"' in prompts[1]` (the assigned turn) and
> `'"turn_ref": "T0001"' in prompts[1]` (the prior result) separately.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/generate/evidence_extraction/test_runner.py -v`
Expected: FAIL with `PromptDiaryError: evidence extraction phase runner is not implemented yet`.

- [ ] **Step 3: Implement `runner.py`**

```python
"""Evidence extraction phase runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentConfig
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.evidence_extraction.inputs import (
    ExtractionTurn,
    SessionExtractionInputs,
    build_session_extraction_inputs,
)
from prompt_diary.generate.evidence_extraction.model import new_session_card
from prompt_diary.generate.pipeline import TaskResult, evidence_card_artifact
from prompt_diary.generate.prompts import (
    evidence_extractor_next_turn_prompt,
    evidence_extractor_prompt,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentRunner, AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskSpec


@dataclass(frozen=True)
class EvidenceExtractionRunner:
    """Drive an agent to extract one evidence chain per indexed turn of a session."""

    agent_factory: AgentSessionFactory

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        """Run one session evidence extraction task."""
        project_key, session_ref = _require_scope(task)
        inputs = build_session_extraction_inputs(
            workspace_path=workspace_path,
            project_key=project_key,
            session_ref=session_ref,
        )
        card_path = workspace_path / evidence_card_artifact(project_key, session_ref).path
        if card_path.exists():
            card_path.unlink()

        if not inputs.turns:
            _write_empty_card(card_path, project_key, session_ref)
            return TaskResult(task_id=task.task_id, status="success")

        runner = await self.agent_factory.runner(AgentConfig(working_directory=workspace_path))
        previous_result_json: str | None = None
        for index, turn in enumerate(inputs.turns):
            await runner.turn(_prompt_for_turn(inputs, turn, index, previous_result_json))
            if not _card_has_turn(card_path, turn.turn_ref):
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    errors=(_uncommitted_turn_message(session_ref, turn.turn_ref),),
                )
            previous_result_json = _committed_result_json(project_key, session_ref, turn.turn_ref)
        return TaskResult(task_id=task.task_id, status="success")


def _require_scope(task: TaskSpec) -> tuple[str, str]:
    if task.project_key is None or task.session_ref is None:
        raise PromptDiaryError(
            f"evidence extraction task {task.task_id} requires project_key and session_ref"
        )
    return task.project_key, task.session_ref


def _prompt_for_turn(
    inputs: SessionExtractionInputs,
    turn: ExtractionTurn,
    index: int,
    previous_result_json: str | None,
) -> str:
    if index == 0 or previous_result_json is None:
        return evidence_extractor_prompt(
            project_key=inputs.project_key,
            project_json=inputs.project_json,
            session_ref=inputs.session_ref,
            session_path=inputs.session_path,
            session_index_record=inputs.session_index_record,
            target_turn=turn.target_turn_json,
        )
    return evidence_extractor_next_turn_prompt(
        write_evidence_result=previous_result_json,
        target_turn=turn.target_turn_json,
    )


def _committed_result_json(project_key: str, session_ref: str, turn_ref: str) -> str:
    return json.dumps(
        {
            "status": "appended",
            "project_key": project_key,
            "session_ref": session_ref,
            "turn_ref": turn_ref,
        },
        indent=2,
        ensure_ascii=False,
    )


def _card_has_turn(card_path: Path, turn_ref: str) -> bool:
    if not card_path.exists():
        return False
    raw: object = json.loads(card_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return False
    chains = cast("dict[str, Any]", raw).get("evidence_chains")
    if not isinstance(chains, list):
        return False
    return any(
        isinstance(chain, dict) and cast("dict[str, Any]", chain).get("turn_ref") == turn_ref
        for chain in cast("list[Any]", chains)
    )


def _write_empty_card(card_path: Path, project_key: str, session_ref: str) -> None:
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        json.dumps(new_session_card(project_key, session_ref), indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _uncommitted_turn_message(session_ref: str, turn_ref: str) -> str:
    return (
        f"no evidence chain was committed for session {session_ref} turn {turn_ref}; "
        "the agent did not write a valid chain for the assigned turn"
    )
```

Note: `AgentRunner` is imported under `TYPE_CHECKING` only for typing; if basedpyright reports it
unused, remove it from the import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generate/evidence_extraction/test_runner.py -v`
Expected: PASS (all runner behaviors).

- [ ] **Step 5: Full suite + lint/type/format/coverage**

Run: `uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests && uv run basedpyright`
Expected: PASS, 100% coverage.

- [ ] **Step 6: Commit**

```bash
git add src/prompt_diary/generate/evidence_extraction/runner.py tests/generate/evidence_extraction/test_runner.py
git commit -m "feat: implement evidence extraction phase runner"
```

---

## Task 6: MCP server resolves workspace from env var

**Files:**
- Modify: `src/prompt_diary/mcp/server.py:17-28` (`write_evidence`)
- Test: `tests/mcp/test_server.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_server.py  (append)
def test_write_evidence_uses_workspace_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = copy_basic_evidence_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)  # cwd is NOT the workspace
    monkeypatch.setenv("PROMPT_DIARY_WORKSPACE", str(workspace))

    result = mcp_server.write_evidence(PROJECT_KEY, SESSION_REF, valid_material_doc_chain())

    assert result_to_dict(result)["status"] == "appended"
```

(Ensure `copy_basic_evidence_workspace`, `valid_material_doc_chain`, `result_to_dict`,
`PROJECT_KEY`, `SESSION_REF` are imported in the test module — they already are.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/mcp/test_server.py::test_write_evidence_uses_workspace_env_var -v`
Expected: FAIL (workspace resolved from cwd, so unknown project_key → invalid, not appended).

- [ ] **Step 3: Implement env resolution in `server.py`**

```python
"""Boilerplate MCP stdio server for Prompt Diary."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from prompt_diary.generate.evidence_extraction.mcp import write_evidence as write_evidence_api

_WORKSPACE_ENV = "PROMPT_DIARY_WORKSPACE"


def _resolve_workspace() -> Path:
    """Resolve the prepared workspace root for MCP tool calls."""
    override = os.environ.get(_WORKSPACE_ENV)
    return Path(override) if override else Path.cwd()
```

Update `write_evidence` to call `_resolve_workspace()` instead of `Path.cwd()`:

```python
def write_evidence(
    project_key: str,
    session_ref: str,
    evidence_chain: dict[str, object],
) -> object:
    """Validate and append one evidence chain from the resolved prepared workspace."""
    return write_evidence_api(
        workspace_path=_resolve_workspace(),
        project_key=project_key,
        session_ref=session_ref,
        evidence_chain=evidence_chain,
    )
```

- [ ] **Step 4: Run tests to verify pass (new + existing chdir-based)**

Run: `uv run pytest tests/mcp/test_server.py -v`
Expected: PASS (env-var test + the existing cwd-fallback tests).

- [ ] **Step 5: Lint/type/format**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run basedpyright`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/prompt_diary/mcp/server.py tests/mcp/test_server.py
git commit -m "feat: resolve MCP workspace from PROMPT_DIARY_WORKSPACE env var"
```

---

## Task 7: `prompt_diary_mcp_overrides`

**Files:**
- Create: `src/prompt_diary/mcp/codex_config.py`
- Test: `tests/mcp/test_codex_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_codex_config.py
from __future__ import annotations

from pathlib import Path

from prompt_diary.mcp.codex_config import prompt_diary_mcp_overrides


def test_overrides_register_server_command_args_and_workspace(tmp_path: Path) -> None:
    overrides = prompt_diary_mcp_overrides(tmp_path)
    joined = "\n".join(overrides)

    assert any("mcp_servers.prompt_diary.command" in item for item in overrides)
    assert any('"mcp"' in item and '"serve"' in item for item in overrides)
    assert str(tmp_path.resolve()) in joined
    assert "PROMPT_DIARY_WORKSPACE" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/mcp/test_codex_config.py -v`
Expected: FAIL with `ModuleNotFoundError: ...codex_config`.

- [ ] **Step 3: Create `codex_config.py`**

```python
"""Codex config overrides that register the Prompt Diary MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SERVER_NAME = "prompt_diary"


def prompt_diary_mcp_overrides(workspace_path: Path) -> tuple[str, ...]:
    """Return Codex config-override strings registering the package MCP server.

    The server is launched as ``report mcp serve`` and is told which prepared workspace to
    write to through the ``PROMPT_DIARY_WORKSPACE`` environment variable, since a Codex-spawned
    stdio server does not inherit the agent thread's working directory.
    """
    workspace = str(workspace_path.resolve())
    prefix = f"mcp_servers.{_SERVER_NAME}"
    return (
        f'{prefix}.command="report"',
        f'{prefix}.args=["mcp","serve"]',
        f'{prefix}.env.PROMPT_DIARY_WORKSPACE="{workspace}"',
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/mcp/test_codex_config.py -v`
Expected: PASS.

- [ ] **Step 5: Lint/type/format**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run basedpyright`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/prompt_diary/mcp/codex_config.py tests/mcp/test_codex_config.py
git commit -m "feat: build Codex MCP overrides for the workspace-aware server"
```

> Implementation note: the exact override key/quoting and env-propagation form for `openai_codex`
> is confirmed by Task 9's integration test; adjust the strings here if the live run shows the SDK
> expects a different shape (e.g. per-server `env` table vs app-server `env`).

---

## Task 8: Per-run workspace-aware factory wiring

**Files:**
- Modify: `src/prompt_diary/generate/workflow.py:55-127`
- Modify: `src/prompt_diary/cmds/generate.py:45-55`
- Test: `tests/generate/test_workflow.py` (update constructor calls), `tests/cmds/test_generate.py` (add a builder-coverage test; create if absent)

- [ ] **Step 1: Update `GenerateWorkspaceWorkflow` to per-run builders**

Replace the dataclass fields and the two factory-entry methods. New shape:

```python
@dataclass(frozen=True)
class GenerateWorkspaceWorkflow:
    """Run generation workflows against one prepared workspace."""

    build_agent_factory: Callable[[Path], AgentSessionFactory]
    build_phase_runners: Callable[[AgentSessionFactory], Mapping[TaskKind, PhaseRunner]]
```

In `run_pipeline`, after `_require_workspace(workspace_path)`:

```python
        factory = self.build_agent_factory(workspace_path)
        phase_runners = self.build_phase_runners(factory)
        plan = build_generation_plan(workspace_path)
        pipeline_result = asyncio.run(
            self._run_plan(
                workspace_path=workspace_path,
                plan=plan,
                factory=factory,
                phase_runners=phase_runners,
            )
        )
```

In `run_phase`, after selecting the task:

```python
        factory = self.build_agent_factory(workspace_path)
        phase_runners = self.build_phase_runners(factory)
        task_result = asyncio.run(
            self._run_task(
                workspace_path=workspace_path,
                task=task,
                factory=factory,
                phase_runners=phase_runners,
            )
        )
```

Update the private helpers to take the per-run factory + runners:

```python
    async def _run_plan(
        self,
        *,
        workspace_path: Path,
        plan: GenerationPlan,
        factory: AgentSessionFactory,
        phase_runners: Mapping[TaskKind, PhaseRunner],
    ) -> PipelineRunResult:
        runner = GeneratePipelineRunner(phase_runners=phase_runners)
        async with factory:
            return await runner.run(workspace_path=workspace_path, plan=plan)

    async def _run_task(
        self,
        *,
        workspace_path: Path,
        task: TaskSpec,
        factory: AgentSessionFactory,
        phase_runners: Mapping[TaskKind, PhaseRunner],
    ) -> TaskResult:
        phase_runner = phase_runners[task.kind]
        async with factory:
            return await run_generation_task_with_lifecycle(
                workspace_path=workspace_path,
                task=task,
                phase_runner=phase_runner,
            )
```

Add `Callable` to the `typing`/`collections.abc` imports as needed.

- [ ] **Step 2: Update `build_generation_workflow` in `cmds/generate.py`**

```python
def build_generation_workflow() -> GenerateWorkspaceWorkflow:
    """Build the default generation workflow with a workspace-aware Codex backend per run."""

    def build_agent_factory(workspace_path: Path) -> AgentSessionFactory:
        return CodexAgentSessionFactory(
            CodexBackendConfig(mcp_config_overrides=prompt_diary_mcp_overrides(workspace_path))
        )

    def build_phase_runners(
        factory: AgentSessionFactory,
    ) -> dict[TaskKind, PhaseRunner]:
        return {
            "evidence_extraction": EvidenceExtractionRunner(agent_factory=factory),
            "project_synthesis": ProjectSynthesisRunner(agent_factory=factory),
            "daily_synthesis": DailySynthesisRunner(agent_factory=factory),
        }

    return GenerateWorkspaceWorkflow(
        build_agent_factory=build_agent_factory,
        build_phase_runners=build_phase_runners,
    )
```

Add imports: `from prompt_diary.agent import AgentSessionFactory`, `from prompt_diary.generate.pipeline import PhaseRunner, TaskKind`, `from prompt_diary.mcp.codex_config import prompt_diary_mcp_overrides`.

- [ ] **Step 3: Update `tests/generate/test_workflow.py`**

Replace the `_workflow` helper and the two inline `GenerateWorkspaceWorkflow(...)` constructions to use builders that ignore the workspace path and return the existing fakes:

```python
def _workflow(phase_runner: PhaseRunner) -> GenerateWorkspaceWorkflow:
    factory = FakeAgentSessionFactory(script=_no_agent_turns)
    return GenerateWorkspaceWorkflow(
        build_agent_factory=lambda _workspace: factory,
        build_phase_runners=lambda _factory: _all_phase_runners(phase_runner),
    )
```

For the two tests that assert `factory.entered == 1` / `factory.exited == 1`, capture the factory:

```python
    factory = FakeAgentSessionFactory(script=_no_agent_turns)
    result = GenerateWorkspaceWorkflow(
        build_agent_factory=lambda _workspace: factory,
        build_phase_runners=lambda _factory: _all_phase_runners(phase_runner),
    ).run_pipeline(workspace_path=workspace, messages=("Reusing existing workspace.",))
    ...
    assert factory.entered == 1
    assert factory.exited == 1
```

- [ ] **Step 4: Add builder-coverage test for the composition root**

```python
# tests/cmds/test_generate.py  (create or append)
from __future__ import annotations

from pathlib import Path

from prompt_diary.cmds.generate import build_generation_workflow
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory


def test_build_generation_workflow_builds_workspace_aware_codex_runners(tmp_path: Path) -> None:
    workflow = build_generation_workflow()
    factory = workflow.build_agent_factory(tmp_path)
    runners = workflow.build_phase_runners(factory)

    assert isinstance(factory, CodexAgentSessionFactory)
    assert set(runners) == {"evidence_extraction", "project_synthesis", "daily_synthesis"}
    for runner in runners.values():
        assert runner.agent_factory is factory
```

(Constructing the factory does not start Codex, so this runs without the SDK.)

- [ ] **Step 5: Run tests + lint/type/format/coverage**

Run: `uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests && uv run basedpyright`
Expected: PASS, 100% coverage.

- [ ] **Step 6: Commit**

```bash
git add src/prompt_diary/generate/workflow.py src/prompt_diary/cmds/generate.py tests/generate/test_workflow.py tests/cmds/test_generate.py
git commit -m "refactor: build a workspace-aware agent factory per generation run"
```

---

## Task 9: Opt-in Codex integration test

**Files:**
- Create: `tests/integration/__init__.py` (empty)
- Create: `tests/integration/test_evidence_extraction_codex.py`

- [ ] **Step 1: Write the test (gated; skipped by default)**

```python
# tests/integration/test_evidence_extraction_codex.py
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from prompt_diary.cmds.generate import build_generation_workflow
from prompt_diary.generate.evidence_extraction.model import parse_evidence_chain
from prompt_diary.generate.evidence_extraction.model import ParsedEvidenceChain
from tests.support.evidence_extraction import (
    PROJECT_KEY,
    SESSION_REF,
    copy_basic_evidence_workspace,
    load_evidence_card,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.codex_mcp,
    pytest.mark.skipif(
        os.environ.get("RUN_CODEX_INTEGRATION") != "1",
        reason="set RUN_CODEX_INTEGRATION=1 to run the live Codex evidence extraction test",
    ),
]


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
    assert json  # serialization import sanity
```

- [ ] **Step 2: Verify it is skipped in the normal suite**

Run: `uv run pytest tests/integration -v`
Expected: SKIPPED (reason mentions `RUN_CODEX_INTEGRATION`).

- [ ] **Step 3: Run it for real against live Codex**

Run: `RUN_CODEX_INTEGRATION=1 uv run pytest tests/integration -v -m codex_mcp`
Expected: PASS — a real Codex agent reads the fixture transcript, calls `write_evidence` via the
MCP server, and the card contains valid `T0001`/`T0002` chains. If it fails on MCP registration or
workspace resolution, adjust `prompt_diary_mcp_overrides` (Task 7) and rerun. If the fixture
transcript is too thin for the agent, enrich `session-001.jsonl` minimally and rerun.

- [ ] **Step 4: Lint/type/format**

Run: `uv run ruff check tests && uv run ruff format --check tests && uv run basedpyright`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_evidence_extraction_codex.py
git commit -m "test: add opt-in Codex evidence extraction integration test"
```

---

## Task 10: Docs

**Files:**
- Modify: `docs/src/dev/generation-pipeline.md`
- Modify: `README.md`

- [ ] **Step 1: Document runner behavior in `generation-pipeline.md`**

Add a short subsection under Boundaries (or a new "Evidence extraction runner" note) covering:
the runner resets the session card and re-extracts all turns on each run (no resume); it drives one
agent conversation per session turn-by-turn in index order and verifies each commit by reading the
card; a turn that is not committed fails the task and leaves a partial card, which downstream
project synthesis treats as a gap; the workflow builds a workspace-aware agent factory per run so
the MCP `write_evidence` server resolves the correct workspace.

- [ ] **Step 2: Document the opt-in integration test in `README.md`**

In the development/testing section, add how to run the opt-in Codex integration test:

```bash
RUN_CODEX_INTEGRATION=1 uv run pytest -m codex_mcp
```

Note it requires the Codex SDK and authenticated Codex, and is skipped by default.

- [ ] **Step 3: Verify docs build/links (if mdBook configured) and full gate**

Run: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run basedpyright`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/src/dev/generation-pipeline.md README.md
git commit -m "docs: document evidence extraction runner and integration test"
```

---

## Self-Review (controller, before dispatching)

- **Spec coverage:** inputs.py (T2), runner reset/verify/zero-turn/in-order (T5), new_session_card
  (T3), MCP env resolution (T6), overrides (T7), per-run factory (T8), builder + fake (T1/T4),
  integration test (T9), docs (T10). All spec sections mapped.
- **Type consistency:** `build_session_extraction_inputs`/`SessionExtractionInputs`/`ExtractionTurn`,
  `evidence_card_artifact(...).path`, `new_session_card`, `prompt_diary_mcp_overrides`,
  `build_agent_factory`/`build_phase_runners`, `EvidenceWritingAgentSessionFactory.processed`
  used consistently across tasks.
- **Placeholder scan:** the only "confirm during implementation" item (Codex override syntax) is
  isolated to one function and proven by T9 — flagged, not vague.
- **Known follow-up:** if `openai_codex` rejects the override shape, T7 strings change; T9 is the
  proof gate.
