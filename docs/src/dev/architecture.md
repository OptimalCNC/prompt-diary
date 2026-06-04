# Architecture

## Page Role

This page defines stable implementation boundaries for Prompt Diary. It should not prescribe
phase-local classes, helper modules, migration steps, or other details that are likely to change.

Product behavior remains defined by [Prompt Diary Product](../product.md),
[Workspace Layout](../workspace-layout.md), and [Report Generation](../generate/index.md).

## Tool Shape

Prompt Diary is a Python CLI and MCP package with a small public root and workflow-owned
implementation packages.

The package root should stay small. Implementation code should live with the workflow or named
protocol adapter that owns its behavior instead of accumulating as package-root modules.

## Codemap

This codemap names stable homes by responsibility. It intentionally avoids phase-local helper
modules and other details that may change as the implementation evolves.

| Path | Stable meaning |
| --- | --- |
| `src/prompt_diary/` | Package root for stable imports, entry points, and shared package code. It should not be the default home for workflow internals. |
| `src/prompt_diary/cli.py` | Console command interface that parses options, presents results and errors, and delegates to workflow implementation modules. |
| `src/prompt_diary/models.py` | Shared cross-workflow result models and value types. |
| `src/prompt_diary/agent.py` | Neutral agent execution contract (port): `AgentRunner`/`AgentSessionFactory` protocols and shared agent value types (`AgentConfig`, `AgentTurnEvent`, `AgentTurnResult`), depended on by generation phases and runner adapters. |
| `src/prompt_diary/errors.py` | Shared user-facing exception hierarchy. |
| `src/prompt_diary/paths.py` | Reports-root resolution: maps `--reports-root` / `PROMPT_DIARY_HOME` / the per-user data directory to the reports root (the parent of `work/` and `private/`; a prepared workspace is `<reports-root>/work/<date>`), resolved once at the CLI boundary; fails loud if the platform default is non-absolute and no override is set. |
| `src/prompt_diary/targeting/` | Date and timezone resolution into typed report targets used by both workflows. |
| `src/prompt_diary/prepare/` | Preparation workflow implementation: source session ingestion and prepared workspace construction. |
| `src/prompt_diary/generate/` | Generation workflow implementation: phase orchestration, generation artifacts, prompt assets, and report output behavior. |
| `src/prompt_diary/generate/evidence_extraction/` | Evidence Extraction phase behavior and internal contracts for its canonical artifacts and tools. |
| `src/prompt_diary/generate/project_synthesis/` | Project Synthesis phase behavior and internal contracts for its canonical artifacts and tools. |
| `src/prompt_diary/generate/daily_synthesis/` | Daily Report Synthesis phase behavior and internal contracts for its canonical artifacts and tools. |
| `src/prompt_diary/generate/prompts/` | Runtime prompt templates and prompt-rendering helpers used by generation phases and prompt CLI commands. |
| `src/prompt_diary/mcp/` | MCP protocol adapter. MCP code adapts requests and responses; it does not own workflow semantics. |
| `src/prompt_diary/integrations/` | Optional external runner and bootstrap integrations that are not core workflow semantics. |

## Generation Placement

Generation implementation belongs under `src/prompt_diary/generate/`. The stable generation
boundaries are the artifact-producing phases defined by
[Report Generation](../generate/index.md):

- Evidence Extraction
- Project Synthesis
- Daily Report Synthesis

Generation subpackages mirror those broad phase boundaries. This architecture page should not name
every phase helper module; those details belong in code and phase-local tests.

`docs/src/generate/` defines generation contracts for humans and agents. It is not the Python
implementation layout. Runtime prompt templates are generation assets and should live with the
generation implementation while remaining includable from the documentation so docs and runtime use
one prompt source.

MCP tools are a protocol adapter over workflow APIs. MCP request parsing and response adaptation
belong in `src/prompt_diary/mcp/`; canonical validation, artifact reads and writes, and generation
behavior belong in the generation package that owns the relevant contract.

MCP tool contracts live under `docs/src/generate/mcp-tools/`, grouped by generation phase. Shared
workspace and error rules live on that section's index page; phase-specific tool schemas and write
rules live on the owning phase page.

## Test Layout

Tests should follow the same stable boundaries without mirroring every helper module:

| Path | Stable meaning |
| --- | --- |
| `tests/targeting/` | Target resolution tests. |
| `tests/prepare/` | Preparation workflow and prepared workspace tests. |
| `tests/generate/` | Generation pipeline, workflow, and prompt tests. |
| `tests/mcp/` | MCP adapter tests. |
| `tests/integrations/` | Optional external integration tests. |
| Top-level `tests/test_*.py` | CLI and end-to-end workflow tests that span multiple packages. |

## Workflows

### `prepare`

Resolves a report target from CLI options, then builds a bounded workspace for that target day.
The workspace contains only copied session files and deterministic indexes; it defines the evidence
boundary that generation must not expand.

Product contract: [Workspace Layout](../workspace-layout.md).

### `generate`

The CLI resolves a report target and ensures a prepared workspace exists, then calls the generation
workflow with that workspace path. The generation package does not map dates to workspace folders; it
consumes only the prepared workspace plus durable artifacts from earlier generation phases.

The generation agent-wiring composition root is `cmds/generate.py::build_generation_workflow()` —
the only place that imports both `generate/` and `integrations/`. It constructs one
`CodexAgentSessionFactory` (from `integrations/codex_runner.py`) and passes it to all three phase
runners and to the workflow. Generation phase code depends only on `prompt_diary.agent` (the
neutral port), never on `integrations/` directly.

Product contracts: [Report Generation](../generate/index.md),
[Evidence Contract](../generate/evidence-contract.md),
[Project Synthesis](../generate/project-synthesis.md), and
[Daily Report Synthesis](../generate/daily-synthesis.md).

Pipeline framework: [Generation Pipeline Framework](./generation-pipeline.md).

## CLI Interface

The user-facing CLI commands and date targeting rules are defined in
[Prompt Diary Product](../product.md#cli-surface). `report` and `prompt-diary` are both registered
as console entry points and invoke the same CLI.
