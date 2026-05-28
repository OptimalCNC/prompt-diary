# Architecture

## Page Role

This page defines stable implementation boundaries for Prompt Diary. It should not prescribe
phase-local classes, helper modules, migration steps, or other details that are likely to change.

Product behavior remains defined by [Prompt Diary Product](../product.md),
[Workspace Layout](../workspace-layout.md), and [Report Generation](../generate/index.md).

## Tool Shape

Prompt Diary is a Python CLI package with a small public root and workflow-owned implementation
packages.

The package root should stay small. Implementation code should live with the workflow or named
protocol adapter that owns its behavior instead of accumulating as package-root modules.

## Codemap

This codemap names stable homes by responsibility. It intentionally avoids phase-local helper
modules and other details that may change as the implementation evolves.

| Path | Stable meaning |
| --- | --- |
| `src/prompt_diary/` | Package root for stable imports, entry points, and shared package code. It should not be the default home for workflow internals. |
| `src/prompt_diary/api.py` | Transport-independent public workflow API for preparation and generation. |
| `src/prompt_diary/cli.py` | Console command interface that parses options, presents results and errors, and delegates to the public API. |
| `src/prompt_diary/models.py` | Shared cross-workflow result models and value types that are intentionally public or broadly reused. |
| `src/prompt_diary/errors.py` | Shared user-facing exception hierarchy. |
| `src/prompt_diary/targeting/` | Date and timezone resolution into typed report targets used by both workflows. |
| `src/prompt_diary/prepare/` | Preparation workflow implementation: source session ingestion and prepared workspace construction. |
| `src/prompt_diary/generate/` | Generation workflow implementation: phase orchestration, generation artifacts, prompt assets, and report output behavior. |
| `src/prompt_diary/generate/evidence_extraction/` | Evidence Extraction phase behavior and transport-independent APIs for its canonical artifacts and tools. |
| `src/prompt_diary/generate/project_synthesis/` | Project Synthesis phase behavior and transport-independent APIs for its canonical artifacts and tools. |
| `src/prompt_diary/generate/daily_synthesis/` | Daily Report Synthesis phase behavior and transport-independent APIs for its canonical artifacts and tools. |
| `src/prompt_diary/generate/prompts/` | Runtime prompt templates and prompt-rendering API used by generation phases. |
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
| `tests/generate/` | Generation report and prompt tests. |
| `tests/mcp/` | MCP adapter tests. |
| `tests/integrations/` | Optional external integration tests. |
| Top-level `tests/test_*.py` | Public API, CLI, and end-to-end workflow tests that span multiple packages. |

## Workflows

### `prepare`

Resolves a report target from CLI options, then builds a bounded workspace for that target day.
The workspace contains only copied session files and deterministic indexes; it defines the evidence
boundary that generation must not expand.

Product contract: [Workspace Layout](../workspace-layout.md).

### `generate`

Resolves a report target, ensures a prepared workspace exists, then runs generation from that
workspace. Generation consumes only the prepared workspace plus durable artifacts from earlier
generation phases.

Product contracts: [Report Generation](../generate/index.md),
[Evidence Contract](../generate/evidence-contract.md),
[Project Synthesis](../generate/project-synthesis.md), and
[Daily Report Synthesis](../generate/daily-synthesis.md).

## CLI Interface

The user-facing CLI commands and date targeting rules are defined in
[Prompt Diary Product](../product.md#cli-surface). `report` and `prompt-diary` are both registered
as console entry points and invoke the same CLI.
