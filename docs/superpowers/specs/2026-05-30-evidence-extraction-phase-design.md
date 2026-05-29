# Evidence Extraction Phase — Design

Status: approved (brainstorming) · Date: 2026-05-30 · Branch: `feature/evidence-extraction-phase`

## Purpose

Implement the evidence extraction generation phase end to end: the `EvidenceExtractionRunner`
that drives an agent to produce one evidence chain per indexed turn of one session, the test
scaffolding that validates its behavior against a mocked agent (fast, cheap, no SDK), and the
real-agent wiring proven by an opt-in Codex integration test.

The phase consumes a prepared workspace and produces one canonical session evidence card
(`projects/<project_key>/evidence/<session_ref>.json`) per indexed session, as defined by the
[Evidence Contract](../../src/generate/evidence-contract.md) and
[Evidence Extraction Tools](../../src/generate/mcp-tools/evidence-extraction.md).

The `write_evidence` MCP write surface and its validation already exist
(`src/prompt_diary/generate/evidence_extraction/{mcp,model}.py`). This phase is the orchestration
that drives an agent to call that surface, turn by turn.

## Scope

In scope:

1. `evidence_extraction/inputs.py` — build per-turn extractor prompt inputs for one session.
2. `EvidenceExtractionRunner.run` — drive the agent across a session's turns and verify results.
3. Real MCP server workspace resolution + per-run workspace-aware agent factory wiring so
   `report generate evidence` works against a live agent.
4. Test scaffolding: a parametric evidence-chain builder and a prompt-reading evidence-writing
   fake agent that calls the real `write_evidence` API.
5. Mock-driven behavior tests (the primary validation), then an opt-in Codex integration test.

Out of scope (explicit boundaries):

- Project synthesis and daily synthesis runners (still placeholders).
- Gap accounting for partial/incomplete cards. A failed mid-session run leaves a partial card;
  treating partial cards as evidence gaps is **project synthesis's** responsibility, per
  [generation-pipeline.md](../../src/dev/generation-pipeline.md). This phase fails loudly on an
  uncommitted turn and does not synthesize gap semantics.
- Changing the evidence data model, prompt templates, or `write_evidence` validation rules.

## Background (current code)

- `PhaseRunner` protocol: `async def run(*, workspace_path: Path, task: TaskSpec) -> TaskResult`.
  The pipeline calls it after declared prerequisites exist and, on `success`, re-checks declared
  output artifacts. A returned `failed`/`blocked` result is passed through unchanged.
- Agent seam (`prompt_diary.agent`): `AgentSessionFactory.runner(AgentConfig) -> AgentRunner`;
  `AgentRunner.turn(prompt, *, timeout_seconds=600, output_schema=None) -> AgentTurnResult`. One
  conversation per `AgentRunner`; turns run sequentially; concurrency comes from multiple runners
  off one shared backend. Backend lifecycle is owned at the workflow scope (entered once per run).
- Prompts (`generate/prompts/__init__.py`): `evidence_extractor_prompt(*, project_key,
  project_json, session_ref, session_path, session_index_record, target_turn)` and
  `evidence_extractor_next_turn_prompt(*, write_evidence_result, target_turn)`. Rendered with
  Jinja2 `StrictUndefined`. In both rendered prompts the **last fenced ```json block is the
  `target_turn`**.
- `write_evidence(*, workspace_path, project_key, session_ref, evidence_chain)` validates a chain
  (structure, controlled enums, non-empty summaries, citation containment inside the indexed turn
  span, "material outcomes cite reaction not only trigger", duplicate-turn rejection) and appends
  it to the canonical card with atomic replace.
- The MCP server (`mcp/server.py`) wraps the API and resolves the workspace from `Path.cwd()`.
- `mcp_config_overrides` on `CodexBackendConfig` are the only mechanism to register MCP servers
  for the real agent; today `build_generation_workflow()` passes an empty `CodexBackendConfig()`,
  so the real agent currently has no `write_evidence` tool.
- Console scripts: both `report` and `prompt-diary` map to `prompt_diary.cli:main`.
- `pyproject.toml` defines a `codex_mcp` pytest marker for opt-in integration tests, and omits
  `integrations/codex_runner.py` from coverage (100% gate elsewhere).

## Architecture

### 1. `evidence_extraction/inputs.py` (new, focused, independently testable)

```python
@dataclass(frozen=True)
class ExtractionTurn:
    turn_ref: str
    span: LineSpan            # from the validated index row, used for verification
    target_turn_json: str     # the raw turns[] item, serialized faithfully (preserves
                              # fields such as target_subagents)

@dataclass(frozen=True)
class SessionExtractionInputs:
    project_key: str
    session_ref: str
    project_json: str         # project.json content, normalized JSON
    session_path: str         # resolved relative to cwd: "projects/<key>/<session_path>"
    session_index_record: str # the index row with "turns" removed, serialized JSON
    turns: tuple[ExtractionTurn, ...]

def build_session_extraction_inputs(
    *, workspace_path: Path, project_key: str, session_ref: str
) -> SessionExtractionInputs: ...
```

Responsibilities and rules:

- Use `load_prepared_workspace` for validated discovery (project → session → typed turns with
  `turn_ref` + `span` + `session_path`). Raise `PromptDiaryError` with an actionable message for
  unknown `project_key`/`session_ref`.
- Read `projects/<key>/project.json` text and normalize via `json.loads`/`json.dumps(indent=2)`.
- Read the matching raw row from `projects/<key>/sessions.index.jsonl`; `session_index_record` is
  that row minus `turns`; each `ExtractionTurn.target_turn_json` is the verbatim raw `turns[]`
  item (matched to the typed turn by `turn_ref`) serialized with `indent=2`.
- `session_path` = `f"projects/{project_key}/{session.session_path}"` (POSIX).
- Turn order follows the validated index order.

### 2. `EvidenceExtractionRunner.run` (implement the placeholder)

```python
@dataclass(frozen=True)
class EvidenceExtractionRunner:
    agent_factory: AgentSessionFactory

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult: ...
```

Flow:

1. Read `project_key`/`session_ref` from `task`; build inputs via `build_session_extraction_inputs`.
2. Resolve the card path with `evidence_card_artifact(project_key, session_ref).path` (DRY).
3. **Reset:** if the card exists, delete it (clean slate; avoids `write_evidence` duplicate-turn
   rejection on re-run). Re-extraction is the chosen re-run policy.
4. **Zero turns** (defensive): if `inputs.turns` is empty, write an empty canonical card
   (`new_session_card(...)`, see below) so the output artifact exists, and return `success`.
5. Mint one `AgentRunner` for the session via `agent_factory.runner(AgentConfig(...))`.
6. For each turn in order, `i`:
   - `prompt = evidence_extractor_prompt(...)` when `i == 0`, else
     `evidence_extractor_next_turn_prompt(write_evidence_result=<prev result JSON>, target_turn=...)`.
   - `await runner.turn(prompt)`.
   - **Verify by reading the card** (not the assistant text): confirm a chain with this
     `turn_ref` now exists. If missing → return `TaskResult(status="failed", errors=(<message
     naming session_ref + turn_ref>,))`.
   - Reconstruct `write_evidence_result = {"status":"appended","project_key":…,"session_ref":…,
     "turn_ref":…}` for the next prompt.
7. After all turns committed → `TaskResult(status="success")`. The pipeline confirms the card
   artifact exists.

Notes:

- The runner builds `AgentConfig(working_directory=workspace_path, …)` with non-interactive policy
  fields appropriate for unattended Codex runs (exact `approval_mode`/`sandbox` values confirmed
  against the SDK during implementation; irrelevant to the fake).
- The runner never enters the factory; lifecycle is owned by the workflow.

### 3. `model.py`: `new_session_card(project_key, session_ref) -> dict`

Add a pure public helper returning the canonical empty-card skeleton
(`schema_version`, `project_key`, `session_ref`, `evidence_chains: []`). Point the existing private
`mcp.py::_new_card` at it so there is one card skeleton (small, behavior-preserving dedup).

### 4. Real MCP server workspace resolution

`mcp/server.py`: resolve the workspace from a `PROMPT_DIARY_WORKSPACE` environment variable when
set, falling back to `Path.cwd()` (preserves existing `monkeypatch.chdir` tests). A Codex-spawned
stdio MCP server is a separate process whose cwd is not the agent thread's `cwd`, so the workspace
must be passed explicitly.

New `prompt_diary_mcp_overrides(workspace_path) -> tuple[str, ...]` (in the `mcp` package) builds
the Codex config-override strings that register the package MCP server (command = `report`,
args = `["mcp", "serve"]`) and make the workspace available to it. Exact override/env propagation
syntax is confirmed against `openai_codex` during implementation; the integration test is the
proof. Isolated and unit-tested for string content.

### 5. Per-run workspace-aware factory (composition refactor)

`GenerateWorkspaceWorkflow` switches from a prebuilt `agent_factory` to per-run builders:

```python
@dataclass(frozen=True)
class GenerateWorkspaceWorkflow:
    build_agent_factory: Callable[[Path], AgentSessionFactory]
    build_phase_runners: Callable[[AgentSessionFactory], Mapping[TaskKind, PhaseRunner]]
```

`run_pipeline`/`run_phase` build the factory from `workspace_path`, then the phase runners from the
factory, then run (one factory per run, entered once, concurrency preserved).
`cmds/generate.py::build_generation_workflow` wires the real builders: the factory carries
`CodexBackendConfig(mcp_config_overrides=prompt_diary_mcp_overrides(workspace_path), …)`.
`tests/generate/test_workflow.py` updates to the builder seam.

## Test scaffolding

### `build_evidence_chain` (in `tests/support/evidence_extraction.py`)

`build_evidence_chain(*, turn_ref, span, kind="material"|"no_material") -> dict`. Citations are
sized to `span`: trigger + quoted at `span.start`; reaction, outcome, terminal at `span.end`
(`material`), so the material-outcome-cites-reaction check passes for any span ≥ 1 line; `no_material`
uses empty outcomes + `terminal_state.type="no_material"` + `materiality="none"`. Existing
handcrafted MCP-contract helpers are left untouched (the modified `test_write_api.py` depends on
their exact spans).

### Evidence-writing fake agent (`tests/support/evidence_agent.py`, new)

`EvidenceWritingAgentSessionFactory` + `EvidenceWritingAgentRunner` (one runner per session):

- Parse the **last ```json fence** of each prompt as `target_turn`.
- On the first-turn prompt, also parse `project_key` and `session_ref` (from the prompt's context
  lines) and remember them on the runner for subsequent next-turn prompts (which omit them).
- Build a chain via `build_evidence_chain(turn_ref, span)` and call the real `write_evidence` API
  with `workspace_path = config.working_directory`.
- Record `(session_ref, turn_ref)` in a shared, ordered list and record each prompt, for
  assertions.
- Configurable `fail_turns: frozenset[str]` to skip the `write_evidence` call (drives the
  verify-then-fail test).

## Behaviors to validate (mock-driven, primary)

- Turns processed in **indexed order** within a session; card `evidence_chains` end in that order.
- Exactly one conversation per session, reused across turns (first prompt = full template, later =
  next-turn template carrying the prior committed result and the correct `target_turn`).
- **Reset:** a pre-seeded card is discarded and re-extracted.
- **Verify-then-fail:** a skipped write → `TaskResult` failed with a message naming
  session_ref + turn_ref; downstream sees the failure.
- **Zero-turn session:** an empty canonical card is written and the task succeeds.
- Pipeline-level (multiple concurrent sessions): each session's turn subsequence is ordered
  (global cross-session order is non-deterministic and must not be asserted).
- `inputs.py`: `session_index_record` excludes `turns`; `target_turn` preserves raw fields;
  `session_path` resolved correctly.

## Real-agent validation (opt-in)

`tests/integration/test_evidence_extraction_codex.py`, marked `codex_mcp` and gated on both
`importorskip("openai_codex")` and an explicit env flag, copies the `basic-two-turns` fixture to a
temp workspace and runs `build_generation_workflow().run_phase(phase="evidence", project_key=…,
session_ref=…)`, then asserts the produced card parses and contains valid chains for `T0001` and
`T0002`. This exercises runner + real agent + real MCP + per-run wiring together.

## Error handling

- Missing/invalid workspace files → `PromptDiaryError` from `inputs.py`/`load_prepared_workspace`;
  the pipeline converts a raised `PromptDiaryError` to a failed `TaskResult`.
- Uncommitted turn after a turn → failed `TaskResult` (verify-then-fail).
- Unknown `project_key`/`session_ref` → actionable `PromptDiaryError`.

## Sequencing (TDD, mock green before real run)

- **A. Foundations:** `inputs.py` (+ `test_inputs.py`), `build_evidence_chain`, evidence-writing
  fake. QA writes failing tests first per task.
- **B. Runner against mocks:** `new_session_card` (+ `mcp.py` dedup), `EvidenceExtractionRunner.run`,
  `test_runner.py` covering all behaviors above. Green here = behaviors validated.
- **C. Composition + real MCP wiring:** `mcp/server.py` env resolution (+ test),
  `prompt_diary_mcp_overrides` (+ test), per-run factory refactor in `workflow.py` +
  `cmds/generate.py`, update `test_workflow.py`. All mock/unit tests stay green.
- **D. Real agent run:** opt-in integration test; run with live Codex; iterate override/env syntax;
  docs + README updates.

## Verification gates

`uv run pytest`, `uv run basedpyright`, `uv run ruff check`, `uv run ruff format --check`, 100%
coverage maintained (new package modules covered by mock tests; the integration test is opt-in and
its Codex-only paths are coverage-omitted like `codex_runner.py`). Integration test run manually
with live Codex.

## Docs to update

- `docs/src/dev/generation-pipeline.md`: evidence runner behavior (reset-on-rerun, verify-then-fail,
  partial-card boundary, per-run workspace-aware factory).
- `README.md`: how to run the opt-in Codex integration test (env flag + marker), per AGENTS.md.
- `docs/src/generate/evidence-contract.md`: light note on reset/verify behavior if it clarifies.

## Details to confirm during implementation

- Exact Codex MCP-server override/env-propagation syntax for `openai_codex` (env var vs per-server
  config); isolated in `prompt_diary_mcp_overrides`, proven by the integration test.
- Non-interactive `approval_mode`/`sandbox` enum values for unattended runs.
- Whether the `basic-two-turns` fixture transcript is rich enough for the real agent to extract
  meaningful chains; enrich only if needed.
