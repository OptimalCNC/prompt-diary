# Codex Agent Runner

This page records the initial package-level skeleton for a future wrapper over the OpenAI Codex
Python SDK. It is for developers adding model-backed generation support.

## Role

The Codex agent runner is a generic async execution boundary for model-backed work. It should make
Codex conversations easier to start, continue, observe, and configure from this package.

The runner should not know Prompt Diary generation phases as domain concepts. Callers provide the
prompt, input context, working directory, tool configuration, and any artifact checks they need.

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

Multi-turn support matters for tool rejection repair, deterministic validation feedback, and
audit-driven revision. The runner instance should preserve the SDK conversation state internally,
so callers do not assign or manage conversation identifiers.

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

The runner API is centered on a small agent configuration object:

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

The primary interface should be async:

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
```

The first `turn` call starts the underlying SDK conversation. Later `turn` calls continue that same
conversation.

Each turn result should include at least:

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

The current package module is an optional runtime wrapper: `CodexBackend.__aenter__` lazily imports
`openai_codex`, starts the SDK app-server, and `CodexAgentRunner.turn(...)` starts one SDK thread on
first use and reuses it for later turns. The package intentionally has no package-metadata Codex SDK
dependency, and the module is not exported from `prompt_diary.__init__`.

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

For raw SDK usage, the shape is:

```python
from openai_codex import AppServerConfig, AsyncCodex

async with AsyncCodex(
    config=AppServerConfig(
        config_overrides=mcp_config_overrides,
    )
) as codex:
    thread = await codex.thread_start(
        cwd=str(workspace_path),
        model=model,
        approval_mode=approval_mode,
        sandbox=sandbox,
        config={"model_reasoning_effort": reasoning_effort},
    )
    result = await thread.run(prompt, output_schema=output_schema)
    repair_result = await thread.run(repair_prompt)
```

For our wrapper, treat these as backend-level configuration:

- MCP server setup and MCP tool policy strings, passed through
  `AppServerConfig.config_overrides` when the SDK needs Codex config entries.

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

To execute independent sessions concurrently, create independent instances:

```python
async with CodexBackend(backend_config) as backend:
    results = await asyncio.gather(
        CodexAgentRunner(backend=backend, config=config_a).turn(prompt_a),
        CodexAgentRunner(backend=backend, config=config_b).turn(prompt_b),
    )
```

## Coverage

Default unit tests mock the Codex SDK and cover the wrapper contracts without starting a real agent.
Real integration tests for this module may spend model tokens, so they remain opt-in rather than
part of the normal unit-test run.

Run the live wrapper test from a development checkout by bootstrapping the optional SDK first:

```bash
uv run prompt-diary codex bootstrap
uv run pytest -m codex_mcp --run-codex-mcp tests/test_codex_mcp_integration.py
```
