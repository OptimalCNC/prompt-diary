# Generation Pipeline Framework

## Role

The generation pipeline framework runs the artifact-producing phases defined by
[Report Generation](../generate/index.md). It owns task ordering, dependency readiness, concurrency
limits, and common artifact checks. It does not own evidence extraction, project synthesis, or
daily synthesis semantics.

Generation remains artifact-first: every phase invocation consumes the prepared workspace plus
durable prerequisite artifacts, writes its own durable outputs, and returns success only after
those outputs exist.

## Task Model

The framework models phase invocations as task nodes:

| Task kind | Scope | Durable outputs |
| --- | --- | --- |
| `evidence_extraction` | one `(project_key, session_ref)` | `projects/<project_key>/evidence/<session_ref>.json` |
| `project_synthesis` | one `project_key` | `projects/<project_key>/project-synthesis.json` |
| `daily_synthesis` | the prepared workspace | `daily-report.json` |
| `rendering` | the prepared workspace | `report.md`, `report.notion.json` |

This is a real DAG, not only three coarse phase barriers. Project synthesis for one project depends
only on that project's evidence tasks. Daily synthesis depends on all project synthesis tasks.

## APIs

`TaskSpec` records the stable task id, kind, project/session scope, dependencies, expected inputs,
and expected outputs. `GenerationPlan` is the immutable task graph built from the prepared
workspace indexes.

Generation workflow APIs take a prepared workspace path. CLI and preparation code own date and
reports-root resolution and the mapping to `<reports-root>/work/<YYYY-MM-DD>`; the generation
package only inspects the workspace and its durable artifacts. The reports root is resolved once at
the CLI boundary by `prompt_diary.config.resolve_reports_root` (`--reports-root` over
`PROMPT_DIARY_HOME` over the stored config over the per-user data directory, the last supplied by
`prompt_diary.paths.platform_data_dir`).

Dependencies normally require successful prerequisite tasks. Project synthesis is the exception:
it waits for all evidence extraction attempts in that project to finish, but checks that each
expected evidence card exists before starting. A failed extraction can continue into project
synthesis only when it wrote a durable evidence card that represents the gap.

`PhaseRunner` is the narrow phase execution protocol:

```python
async def run(*, workspace_path: Path, task: TaskSpec) -> TaskResult: ...
```

Each real phase implementation should live in its phase package and implement this protocol. The
runner may use Codex, MCP tools, deterministic code, or mocks. The framework calls it only after
dependencies are complete.

The three agent phase runners hold an injected `AgentSessionFactory` but do not own backend
lifecycle. Backend ownership lives at the run scope: `GenerateWorkspaceWorkflow` enters one shared
factory once per run (inside `asyncio.run`), and every agent task mints its own conversation off that
shared backend via `factory.runner(config)`. The composition root
`cmds/generate.py::build_generation_workflow()` constructs one `CodexAgentSessionFactory`, wraps it
with the Prompt Diary content-language injector, passes the wrapper to the three agent phase
runners, and sets it as the workflow's `agent_factory`; the rendering runner is deterministic and
takes no agent factory. The wrapper writes the generated workspace `AGENTS.md` and appends the
same rendered language norm to every `AgentConfig.developer_instructions` before minting a
conversation. `GeneratePipelineRunner` itself is agent-agnostic — it schedules tasks and calls
`PhaseRunner.run`; backend and agent wiring are the workflow's concern.

A phase runner therefore does not need to be an async context manager to obtain its backend: the
shared `AgentSessionFactory` is entered once at the workflow scope, above the pipeline. The pipeline
still enters any phase runner that *is* an async context manager (once per run), but that mechanism
now serves only a runner's own additional resources, not the agent backend.

`GenerateWorkspaceWorkflow` is the shared workspace executor for both the full pipeline and one
standalone phase task. `run_generation_task` is the lower-level task API used after declared
prerequisites exist, which keeps phase development and debugging independent from the full pipeline.

`GeneratePipelineRunner` runs a full `GenerationPlan`. It schedules ready tasks, applies per-kind
concurrency limits, marks dependents blocked after failed prerequisites, and validates that a
successful task produced its declared outputs.

The scheduler does not retry failed tasks. Codex-backed phase runners own same-process agent retry
inside a task through `generate/agent_retry.py`: they keep the current `AgentRunner`, re-read durable
artifacts after each successful or failed turn, and send a phase-specific resume prompt when the
artifact shows more work is needed. The default policy permits three consecutive no-progress
attempts with exponential backoff from 1s up to 60s. If that budget is exhausted, the phase returns
a failed task with an `agent made no progress ...` error. Deterministic rendering and non-agent
failures remain outside this helper.

A full pipeline run succeeds when terminal deliverables succeed. Non-terminal tolerated failures,
such as failed extraction attempts that still wrote durable evidence cards for project synthesis,
remain visible on the run result without making the final report command fail.

## CLI

`report generate` runs the full pipeline for a target date, preparing the workspace first when it
is missing.

Standalone phase commands require an existing prepared workspace and run one task after checking
its declared prerequisites:

```bash
report generate evidence --date YYYY-MM-DD --project-key <project_key> --session-ref S0001
report generate project --date YYYY-MM-DD --project-key <project_key>
report generate daily --date YYYY-MM-DD
report generate render --date YYYY-MM-DD
report generate render --date YYYY-MM-DD --notion
```

The phase commands do not rerun earlier phases or prepare missing workspaces. They are development
and repair entrypoints for the phase boundary rule. `generate render` writes the views from an
existing `daily-report.json`; `generate render --notion` renders then publishes to Notion.

## Evidence Extraction Runner

The evidence extraction phase runner drives one agent conversation per session. It sends the full
extractor prompt on the first turn; each subsequent turn carries the prior committed result via the
next-turn prompt. Turns are driven in indexed order until the session is complete.

After each turn the runner verifies the result by reading the evidence card from the workspace
directly. It never trusts the assistant's text response. An uncommitted turn — one where the card
on disk does not reflect the expected turn — is retried on the same agent conversation until that
turn is committed or the no-progress budget is exhausted. The retry counter is scoped to the
current assigned turn and resets when the runner advances to the next committed turn.

At the start of every task run the runner deletes any existing evidence card and re-extracts all turns
from scratch. This reset means a re-run is always clean and never encounters `write_evidence`'s
duplicate-turn rejection. Within that task run, retries never delete the active partial card. A
failed mid-run may leave a partial card on disk; project synthesis treats an incomplete card as an
evidence gap, which is outside the scope of this phase.

The runner builds a workspace-aware agent factory once per run. For the Codex backend the factory
registers the package MCP server (`report mcp serve`) with the prepared workspace path in the
`PROMPT_DIARY_WORKSPACE` environment variable. A Codex-spawned stdio MCP server does not inherit
the calling thread's working directory, so the MCP `write_evidence` tool resolves its workspace
from that variable, falling back to cwd. The agent runs non-interactively
(`approval_mode="auto_review"`, `sandbox="workspace-write"`) using the system `codex` binary on
PATH.

## Project And Daily Agent Retry

Project synthesis uses the same helper with the current uncovered-turn count as its progress
marker. A retry continues on the same runner with the current uncovered-turn list; progress means
that list strictly shrinks, and completion means every indexed turn is covered. The runner deletes a
pre-existing `project-synthesis.json` only once at task start, never between retry turns.

Daily synthesis still uses one fresh agent conversation per pass: each project summary, report
title, engagement assessment, and team-learning pass gets its own runner. A pass retries on that
same runner until its target slot is written in `daily-report.json` or the no-progress budget is
exhausted. If a turn fails after writing the slot, the artifact inspection treats the pass as
complete.

## Progress

The scheduler emits `TaskStarted`/`TaskFinished` events and threads a `ProgressReporter` into each
phase runner's `run(...)`; the evidence runner emits `TurnAdvanced` per turn. See
[Progress Reporting](./progress-reporting.md).

## Boundaries

The framework checks only generic output existence. Phase-local validation belongs to the phase
runner before it returns success. For example, evidence extraction should validate evidence card
structure, daily synthesis should validate `daily-report.json`, and the rendering phase should
validate the rendered views.

Failed extraction may become a durable evidence card that project synthesis accounts for as a gap.
An absent evidence card is a missing prerequisite artifact and prevents the project task from
starting. Other failed dependencies block their dependent tasks.
