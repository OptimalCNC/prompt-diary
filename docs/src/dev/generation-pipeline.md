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
| `daily_synthesis` | the prepared workspace | `daily-report.json`, `report.md` |

This is a real DAG, not only three coarse phase barriers. Project synthesis for one project depends
only on that project's evidence tasks. Daily synthesis depends on all project synthesis tasks.

## APIs

`TaskSpec` records the stable task id, kind, project/session scope, dependencies, expected inputs,
and expected outputs. `GenerationPlan` is the immutable task graph built from the prepared
workspace indexes.

Generation workflow APIs take a prepared workspace path. CLI and preparation code own date
resolution and the mapping from a target date to `.reports/work/<YYYY-MM-DD>`; the generation
package only inspects the workspace and its durable artifacts.

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

A runner may also implement async context-manager methods when it owns shared resources, such as one
Codex backend reused by multiple concurrent conversations. The full pipeline enters each unique
managed runner once for the plan run; standalone phase workflows enter the selected runner around
the single task.

`GenerateWorkspaceWorkflow` is the shared workspace executor for both the full pipeline and one
standalone phase task. `run_generation_task` is the lower-level task API used after declared
prerequisites exist, which keeps phase development and debugging independent from the full pipeline.

`GeneratePipelineRunner` runs a full `GenerationPlan`. It schedules ready tasks, applies per-kind
concurrency limits, marks dependents blocked after failed prerequisites, and validates that a
successful task produced its declared outputs.

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
```

The phase commands do not rerun earlier phases or prepare missing workspaces. They are development
and repair entrypoints for the phase boundary rule.

## Boundaries

The framework checks only generic output existence. Phase-local validation belongs to the phase
runner before it returns success. For example, evidence extraction should validate evidence card
structure, and daily synthesis should validate `daily-report.json` and `report.md`.

Failed extraction may become a durable evidence card that project synthesis accounts for as a gap.
An absent evidence card is a missing prerequisite artifact and prevents the project task from
starting. Other failed dependencies block their dependent tasks.
