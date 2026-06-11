# Codex Agent Runner

This page covers the neutral agent execution port (`prompt_diary/agent.py`) and the Codex SDK
adapter (`integrations/codex_runner.py`). It is for developers adding or testing model-backed
generation support.

## Role

The agent port defines the execution contracts that generation phases depend on, decoupled from any
specific backend. The Codex adapter implements those contracts using the OpenAI Codex Python SDK.

The runner should not know Prompt Diary generation phases as domain concepts. Callers provide the
prompt, input context, working directory, tool configuration, and any artifact checks they need.
Artifact-aware retry lives above this port in generation phase code; the runner only preserves the
same conversation across sequential `turn(...)` calls.

## Neutral Port: `prompt_diary/agent.py`

`src/prompt_diary/agent.py` is the neutral agent execution port. Generation phases and the
workflow layer depend only on this module — never on the Codex SDK adapter directly.

It defines two protocols:

- `AgentRunner` — one agent conversation. Its single `turn(prompt, *, timeout_seconds, output_schema)` method starts the conversation on first use and continues it on later calls.
- `AgentSessionFactory` — owns one shared backend and mints a fresh `AgentRunner` per call via `runner(config)`. It is an async context manager: `__aenter__` starts the backend; `__aexit__` stops it.

The shared agent value types also live here:

```python
@dataclass(frozen=True)
class AgentConfig:
    working_directory: Path
    model: str | None = None
    ...

@dataclass(frozen=True)
class AgentTurnEvent:
    kind: str
    summary: str
    metadata: Mapping[str, object]

@dataclass(frozen=True)
class AgentTurnResult:
    assistant_text: str
    events: tuple[AgentTurnEvent, ...]
```

`CodexAgentSessionFactory` in `integrations/codex_runner.py` is the production adapter: it owns
one `CodexBackend` (via `AsyncExitStack`) and mints a lifecycle-free `CodexAgentRunner`
conversation per `runner()` call. Each `CodexAgentRunner` is bound to the shared backend but has
no lifecycle of its own — it starts its SDK thread on the first `turn()` call.

The generation phase wiring composition root is `cmds/generate.py::build_generation_workflow()`,
the only place that imports both `generate/` and `integrations/`. It constructs one
`CodexAgentSessionFactory`, passes it to the three agent phase runners, and sets it as the workflow's
`agent_factory`. The fourth phase runner, rendering, is deterministic and takes no Codex backend.

## Needs

The wrapper should support:

- async execution as the primary API, with any sync helper built on top of the async API;
- one agent conversation per runner instance;
- one `turn` method that starts the conversation on first use and continues it on later calls;
- passing prompts and input context from the caller;
- configuring the working directory for the conversation;
- selecting a backend whose MCP server and tool policy matches the conversation's needs;
- collecting structured turn results, including assistant text, event summaries, tool-use metadata
  when available;
- enforcing turn-level timeouts and surfacing actionable errors;
- leaving artifact validation to callers.
- allowing callers to retry or repair by sending another prompt on the same runner instance.

Multi-turn support matters for tool rejection repair, deterministic validation feedback, and
artifact repair. The runner instance should preserve the SDK conversation state internally, so
callers do not assign or manage conversation identifiers.

A runner instance is not the concurrency unit for multiple sessions. Do not call `turn`
concurrently on the same instance. To execute multiple agent sessions concurrently, create one
runner instance per session and schedule those instances concurrently.

## Basic Design

The wrapper should separate backend ownership from conversation ownership. Backend configuration
only owns the MCP setup strings provided through Codex config overrides. Agent configuration owns
per-conversation settings.

```python
@dataclass(frozen=True)
class CodexBackendConfig:
    mcp_config_overrides: tuple[str, ...] = ()
```

The runner API is centered on a small agent configuration object (`AgentConfig`, from
`prompt_diary.agent`):

```python
@dataclass(frozen=True)
class AgentConfig:
    working_directory: Path
    model: str | None = None
    model_provider: str | None = None
    reasoning_effort: str | None = None
    approval_mode: str | None = None
    sandbox: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    personality: str | None = None
```

Timeout and structured-output schema are turn-level options because retries, repair turns, and
validation feedback may need different limits or schemas in the same conversation.

Package code should parse external or loosely structured configuration into internal typed values
before starting a conversation.

The primary async interface in `integrations/codex_runner.py`:

```python
class CodexBackend:
    def __init__(self, config: CodexBackendConfig) -> None: ...

    async def __aenter__(self) -> CodexBackend: ...

    async def __aexit__(self, *exc_info: object) -> None: ...


class CodexAgentRunner:
    def __init__(self, backend: CodexBackend, config: AgentConfig) -> None: ...

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult: ...


class CodexAgentSessionFactory:
    def __init__(self, backend_config: CodexBackendConfig) -> None: ...

    async def __aenter__(self) -> CodexAgentSessionFactory: ...

    async def __aexit__(self, *exc_info: object) -> bool | None: ...

    async def runner(self, config: AgentConfig) -> AgentRunner: ...
```

The first `turn` call starts the underlying SDK conversation. Later `turn` calls continue that same
conversation.

`AgentTurnEvent` and `AgentTurnResult` (the turn result types) live in `prompt_diary.agent`:

```python
@dataclass(frozen=True)
class AgentTurnEvent:
    kind: str
    summary: str
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class AgentTurnResult:
    assistant_text: str
    events: tuple[AgentTurnEvent, ...]
```

Artifact paths should usually be checked by the caller rather than trusted from assistant text.
The shared generation retry helper (`generate/agent_retry.py`) follows that rule: after every
successful or failed `turn(...)`, it re-reads durable artifacts and sends a phase-specific resume
prompt on the same runner only when the artifact still needs work.

`CodexBackend.__aenter__` lazily imports `openai_codex`, starts the SDK app-server.
`CodexAgentRunner.turn(...)` starts one SDK thread on first use and reuses it for later turns.
`CodexAgentSessionFactory` wraps a `CodexBackend` in an `AsyncExitStack` and mints a fresh
`CodexAgentRunner` per `runner()` call — each runner is lifecycle-free; only the factory is a
managed context. The package depends on the published `openai-codex` SDK and loads it lazily; use
`uv sync --prerelease=allow` when resolving a development environment. The adapter module is not
exported from `prompt_diary.__init__`.

## Codex SDK Usage

The SDK has three lifecycle layers:

- `AsyncCodex` owns the Codex app-server backend process.
- A SDK thread owns one conversation.
- A turn is one model execution inside that conversation.

Prompt Diary should use one shared `AsyncCodex` backend for concurrent conversations when their
backend-level configuration is compatible. Each `CodexAgentRunner` should own one SDK thread from
that backend, and each `turn` call should run one SDK turn on that thread.

Use separate `AsyncCodex` backends only when sessions need incompatible backend-level
configuration, which for Prompt Diary means incompatible MCP server or MCP tool policy setup. This
keeps normal concurrent generation cheap while still allowing configuration isolation when the SDK
requires it.

The runner should reject concurrent `turn` calls on the same instance. Concurrent generation should
come from multiple runner instances, not from overlapping turns on one conversation.

Because Prompt Diary does not need streaming, steering, or interrupt control, the wrapper's
`turn(...)` method should normally call the SDK convenience `AsyncThread.run(...)` internally.
The published SDK can use a bundled runtime dependency, but Prompt Diary passes the local `codex`
binary path explicitly when it is available. This keeps live tests aligned with the user's
authenticated Codex CLI environment.

For raw SDK usage, the shape is:

```python
from openai_codex import AsyncCodex, CodexConfig, Sandbox

async with AsyncCodex(
    config=CodexConfig(
        config_overrides=mcp_config_overrides,
    )
) as codex:
    thread = await codex.thread_start(
        cwd=str(workspace_path),
        model=model,
        approval_mode=approval_mode,
        sandbox=Sandbox.workspace_write,
        config={"model_reasoning_effort": reasoning_effort},
    )
    result = await thread.run(prompt, output_schema=output_schema)
    repair_result = await thread.run(repair_prompt)
```

For our wrapper, treat these as backend-level configuration:

- MCP server setup and MCP tool policy strings, passed through
  `CodexConfig.config_overrides` when the SDK needs Codex config entries.
- Optional `codex_bin`, only when callers intentionally want to override the bundled SDK runtime.

Treat these as runner/thread-level configuration:

- Conversation working directory: `thread_start(cwd=...)`.
- Model and provider: `thread_start(model=..., model_provider=...)`.
- Approval and sandbox policy: `thread_start(approval_mode=..., sandbox=...)`.
- Instructions and persona: `base_instructions`, `developer_instructions`, and `personality`.
- Reasoning effort or similar model config passed through `thread_start(config=...)`.

Treat these as turn-level configuration:

- Timeout budget for that SDK run.
- Output schema when a specific turn needs structured output: `thread.run(output_schema=...)`.

This split lets Prompt Diary share one backend across concurrent runners when MCP configuration
matches, while still allowing each runner to use its own workspace, model settings,
approval/sandbox settings, and per-turn schema.

## Basic Example

```python
async with CodexBackend(backend_config) as backend:
    runner = CodexAgentRunner(
        backend=backend,
        config=AgentConfig(
            working_directory=workspace_path,
        ),
    )

    result = await runner.turn(prompt, timeout_seconds=600.0)

    if not expected_artifact.exists():
        repair_result = await runner.turn(
            "The expected artifact was not created. Please repair it using the same constraints.",
            timeout_seconds=600.0,
        )
```

Generation phases normally use `run_agent_turn_with_resume(...)` instead of open-coding this
repair loop. The helper is same-process only: it does not resume a failed command after process
exit, replace a runner with a new conversation, or reconstruct higher-level phase state beyond the
durable artifact checks supplied by the phase.

To execute independent sessions concurrently, create independent instances:

```python
async with CodexBackend(backend_config) as backend:
    results = await asyncio.gather(
        CodexAgentRunner(backend=backend, config=config_a).turn(prompt_a),
        CodexAgentRunner(backend=backend, config=config_b).turn(prompt_b),
    )
```

## Coverage

Downstream phase tests mock at the `AgentSessionFactory` seam: they inject a `FakeAgentSessionFactory`
(`tests/agent_fakes.py`) that never starts Codex and returns scripted results. The Codex adapter's own
tests (`tests/integrations/test_codex_runner.py`) mock the `openai_codex` SDK import instead.

Real integration tests for this module may spend model tokens, so they remain opt-in rather than
part of the normal unit-test run.

Run the live wrapper tests from a development checkout after `uv sync --prerelease=allow` and Codex
authentication:

```bash
uv run pytest -m codex_mcp --run-codex-mcp tests/integrations/test_codex_mcp_integration.py
```
