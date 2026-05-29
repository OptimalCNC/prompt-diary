# Agent-Mock Seam for Generation Phases — Design

Status: proposed · Date: 2026-05-29

## Context

Generation phases (evidence extraction, project synthesis, daily synthesis) will each drive a
Codex agent to produce a durable artifact. Today the phase runners are placeholders and there is
no way for a phase to obtain a *mockable* agent: the only mocking seam is at the Codex SDK import
boundary (`importlib.import_module("openai_codex")`), which is the wrong altitude for testing a
phase's orchestration logic. `QA.md` describes the test style we want — "a fake extractor runner
that records prompts and submits scripted evidence chains through the same write API" — but the
code cannot support it yet.

This design adds the seam only. It is intentionally informed by what all three phases need, but it
does **not** implement any phase's `run()` body. Each phase is a later spec built on this seam.

## Goals

- A phase can spawn an agent through an injected port, and tests can substitute a fake with no
  Codex SDK and no real model.
- One Codex backend is instantiated once per generation run and shared by every phase and every
  per-task conversation. Spawning the backend is costly; we pay it once.
- The generation package depends only on a neutral agent contract, never on the Codex integration.

## Non-Goals

- Implementing any phase's `run()` logic, prompts, validation, or repair turns.
- The MCP write tools or the MCP payload carried in the backend configuration.
- Sharing one backend across multiple generation runs in a single process (see Lifecycle).

## Decisions (quality-first; backward compatibility is explicitly not a constraint)

- The agent dependency is **constructor-injected** into each phase runner (not passed per call and
  not ambient/global).
- The shared backend is a **single injected instance**, not a singleton. A module-global singleton
  would reintroduce ambient state (breaking mockability), bind a long-lived async resource to a
  dead event loop across runs, and complicate teardown.
- The workflow's `agent_factory` is **required**, not optional. A real generation run always has
  agent-backed phases, so "no factory" is an illegal state; requiring it removes the `None` branch.
- Value types move to a neutral module with **no re-export shims**; all importers are updated.
- Existing tests are **rewritten** to the new shapes where needed.

## Architecture

A port owned by the generation side, with Codex as one adapter, over a neutral contract module:

```
prompt_diary/agent.py                      ← NEW neutral contract (no codex, no phases)
    AgentConfig, AgentTurnEvent, AgentTurnResult   (moved here from codex_runner)
    AgentRunner            (Protocol: one turn)
    AgentSessionFactory    (Protocol: async-CM lifecycle + mints AgentRunner per AgentConfig)
        ▲                                   ▲
        │ imports port                      │ imports port
generate/ (phase runners, workflow)   integrations/codex_runner.py
                                          CodexAgentRunner          → satisfies AgentRunner
                                          CodexAgentSessionFactory  → satisfies AgentSessionFactory (NEW)
```

`agent.py` sits at the base of a diamond: `generate/` and `integrations/` both depend on it and
neither depends on the other. The Codex wrapper stays generic (it depends on a neutral contract,
not on generation phases). The only `generate → integrations` knowledge lives at the CLI
composition root.

### The port — `prompt_diary/agent.py`

```python
class AgentRunner(Protocol):
    async def turn(self, prompt: str, *, timeout_seconds: float = 600.0,
                   output_schema: Mapping[str, object] | None = None) -> AgentTurnResult: ...

class AgentSessionFactory(Protocol):
    async def __aenter__(self) -> AgentSessionFactory: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def runner(self, config: AgentConfig) -> AgentRunner: ...
```

`runner(config)` mints a fresh conversation per call (one task = one conversation = one SDK
thread); the factory's async-CM lifecycle owns the shared backend underneath. `runner` is async so
the adapter can start the shared backend lazily on first use if it chooses; this design starts it
eagerly (below), but the async signature keeps that an adapter detail.

`AgentConfig`, `AgentTurnEvent`, and `AgentTurnResult` move here unchanged from `codex_runner.py`.

### The Codex adapter — `integrations/codex_runner.py`

`CodexAgentRunner` already matches `AgentRunner` (we extracted the protocol from it). New:

```python
class CodexAgentSessionFactory:                        # satisfies AgentSessionFactory
    def __init__(self, backend_config: CodexBackendConfig) -> None: ...
    async def __aenter__(self) -> CodexAgentSessionFactory:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        self._backend = await self._stack.enter_async_context(   # CodexBackend's own `async with`
            CodexBackend(self._backend_config))
        return self
    async def __aexit__(self, et, e, tb):
        stack, self._stack, self._backend = self._stack, None, None
        return await stack.__aexit__(et, e, tb) if stack else None
    async def runner(self, config: AgentConfig) -> AgentRunner:
        assert self._backend is not None
        return CodexAgentRunner(self._backend, config)  # fresh conversation, shared backend
```

The factory owns the single backend context through `AsyncExitStack` rather than hand-pairing the
backend's `__aenter__`/`__aexit__`. This fits the backend's context-manager design cleanly because
all lifecycle is at the backend level: `CodexAgentRunner` has no teardown (the backend owns and
closes every thread), so the factory ever manages exactly one context and mints lifecycle-free
runners from it. Eager spawn keeps `runner()` trivial.

### Phase runner contract

Each concrete phase runner holds a required factory and mints a per-task conversation:

```python
@dataclass(frozen=True)
class EvidenceExtractionRunner:
    agent_factory: AgentSessionFactory
    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        # DEFERRED to the evidence-extraction spec. For now this raises "not implemented".
        # Target shape:
        #   config = AgentConfig(working_directory=workspace_path, ...)
        #   agent = await self.agent_factory.runner(config)
        #   result = await agent.turn(prompt, output_schema=...)
        #   # validate declared artifact, repair turn if needed, return TaskResult
        ...
```

The three placeholder runners gain the `agent_factory` field now (their honest dependency); their
`run()` bodies still raise until implemented. The redundant standalone `run_evidence_extraction`
/ `run_project_synthesis` / `run_daily_synthesis` free functions are removed — the runner method is
the single entry point.

### Workflow factory scope

The shared factory is entered once, inside `asyncio.run`, around the whole run, so the backend
binds to that run's event loop and every phase mints conversations off it:

```python
@dataclass(frozen=True)
class GenerateWorkspaceWorkflow:
    phase_runners: Mapping[TaskKind, PhaseRunner]
    agent_factory: AgentSessionFactory            # required

    # run_pipeline / run_phase wrap their asyncio.run body in:
    #   async with self.agent_factory:
    #       <existing pipeline / single-task run>
```

The agent-agnostic `GeneratePipelineRunner` is unchanged and never sees the factory; the factory
scope is the workflow's responsibility, one level up. The existing `_phase_runner_lifecycle`
remains for any phase-owned resources but no longer carries the backend.

### Composition root and test override seam

A single builder at the CLI layer is the only place that knows both `generate/` and
`integrations/`. The CLI commands call it; tests monkeypatch it to inject fakes (mirroring how
`default_phase_runners` is monkeypatched today).

```python
# cmds/generate.py (composition root)
def build_generation_workflow() -> GenerateWorkspaceWorkflow:
    factory = CodexAgentSessionFactory(default_backend_config())
    runners = {
        "evidence_extraction": EvidenceExtractionRunner(agent_factory=factory),
        "project_synthesis":   ProjectSynthesisRunner(agent_factory=factory),
        "daily_synthesis":     DailySynthesisRunner(agent_factory=factory),
    }
    return GenerateWorkspaceWorkflow(phase_runners=runners, agent_factory=factory)
```

The `phase_runners=None → default_phase_runners()` fallback inside `generate/workflow.py` is
removed; the workflow is always constructed with explicit `phase_runners` + `agent_factory`. The
`run_generate_pipeline` / `run_generate_phase` free functions are removed; callers (the CLI and
tests) use `GenerateWorkspaceWorkflow.run_pipeline` / `.run_phase` directly.

## Lifecycle, concurrency, event-loop binding

- One backend per workflow run, shared across all three phases and across the full-pipeline and
  standalone-phase paths.
- `runner()` returns a distinct conversation per task; concurrent tasks share the one backend.
- The factory is entered inside `asyncio.run`, so the backend is bound to that run's loop. Reusing
  one backend across multiple `asyncio.run` calls in one process is unsafe (cross-loop binding) and
  is out of scope; the injection design leaves room to widen the scope later with no phase changes.

## Error handling

Reuses existing errors: missing SDK → `CodexRunnerError` (a `PromptDiaryError`) at factory enter;
`turn()` timeout → `TimeoutError`; concurrent turns on one runner → `CodexRunnerError`. Phases may
catch these; the pipeline already converts exceptions from `run()` into failed `TaskResult`s.

## Testing strategy

Shared fake (`tests/agent_fakes.py`, importable by both `tests/generate/` and
`tests/integrations/`) implementing the port:

```python
@dataclass
class FakeAgentRunner:               # satisfies AgentRunner
    config: AgentConfig
    script: Callable[[str, AgentConfig], AgentTurnResult]
    prompts: list[str] = field(default_factory=list)
    async def turn(self, prompt, *, timeout_seconds=600.0, output_schema=None):
        self.prompts.append(prompt)
        return self.script(prompt, self.config)   # may perform real side-effects

@dataclass
class FakeAgentSessionFactory:        # satisfies AgentSessionFactory
    script: Callable[[str, AgentConfig], AgentTurnResult]
    entered: int = 0
    exited: int = 0
    runners: list[FakeAgentRunner] = field(default_factory=list)
    async def __aenter__(self): self.entered += 1; return self
    async def __aexit__(self, *exc): self.exited += 1
    async def runner(self, config):
        r = FakeAgentRunner(config, self.script); self.runners.append(r); return r
```

Proof tests (no SDK):
1. Sharing + lifecycle: one fake factory, several agent-driving phase runners → entered once,
   exited once, one runner minted per task.
2. Agent-driving proof: a representative in-test phase runner mints a runner, calls `turn()`,
   writes its declared artifact, returns success — through the real workflow; asserts prompts
   recorded and artifact written.
3. Codex adapter: `CodexAgentSessionFactory` over the existing `importlib` SDK-fake → one backend
   started, many `runner()` calls share it, closed on exit.
4. Conformance: `CodexAgentRunner` / `CodexAgentSessionFactory` satisfy the protocols (basedpyright
   plus a small runtime assertion).

## Documentation updates (deliverable)

- `docs/src/dev/codex-agent-runner.md`: the neutral `agent.py` port (protocols + moved value
  types), the `CodexAgentSessionFactory` adapter, one-backend-many-runners via the factory, and a
  Coverage note that phase tests mock at the factory seam (vs. SDK-level mocking for the wrapper).
- `docs/src/dev/generation-pipeline.md`: backend ownership hoists from the phase to the run scope;
  phases hold an injected `AgentSessionFactory`; revise the "runner may own one Codex backend /
  pipeline enters each unique managed runner once" language to the factory-at-workflow-scope model.
- `docs/src/dev/architecture.md`: add `src/prompt_diary/agent.py` to the codemap (neutral agent
  port + value types); name the CLI composition root for agent wiring; note that
  `integrations/codex_runner.py` provides the `AgentSessionFactory` adapter.

## Code and test change inventory

New:
- `src/prompt_diary/agent.py` (port + moved value types).
- `CodexAgentSessionFactory` in `src/prompt_diary/integrations/codex_runner.py`.
- `build_generation_workflow()` composition root in `src/prompt_diary/cmds/generate.py`.
- Shared test fake helper `tests/agent_fakes.py`.

Changed:
- `codex_runner.py`: import value types from `agent.py`; `CodexAgentRunner` annotated as
  `AgentRunner`.
- `generate/evidence_extraction|project_synthesis|daily_synthesis/runner.py`: add required
  `agent_factory` field; drop standalone `run_*` free functions; `run()` still raises for now.
- `generate/workflow.py`: `GenerateWorkspaceWorkflow.agent_factory` (required) + `async with`
  scope in the `run_pipeline`/`run_phase` methods; remove the `default_phase_runners` fallback, the
  `phase_runners=None` defaults, and the `run_generate_pipeline`/`run_generate_phase` free
  functions.
- `cmds/generate.py`: build and use `build_generation_workflow()`.

Tests rewritten to the new shapes:
- `tests/integrations/test_codex_runner.py`: moved-type imports; add `CodexAgentSessionFactory`
  shared-backend + conformance tests.
- `tests/generate/test_pipeline.py`: construct placeholder runners with a fake factory; drop
  removed free-function tests. (Pipeline-level tests using `GeneratePipelineRunner` are unchanged —
  the framework stays agent-agnostic.)
- `tests/generate/test_workflow.py`: pass a fake factory alongside fake phase runners; rewrite the
  default-pipeline test for the new composition.
- `tests/test_cli.py` and `tests/test_prompt_diary_e2e_qa.py`: retarget the generation override to
  `build_generation_workflow()` with fake phase runners + a fake factory.

## Deferred to phase specs

Each phase's `run()` body and validation, the MCP write tools, and the MCP payload in
`default_backend_config()`. Until those land, `generate` requires the Codex SDK bootstrapped and
spawns the backend before phases raise "not implemented" — an accepted interim consequence of the
quality-first wiring.
```
