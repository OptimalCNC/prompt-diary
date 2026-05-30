# Progress Reporting for `prepare` and `generate` — Design

Status: approved (brainstorming) · Date: 2026-05-30 · Branch: `feature/progress-reporting`

## Purpose

Give the user continuous, legible feedback while `prepare` and `generate` run, so a long evidence
run never looks stuck. The display surfaces what is happening across the prepare steps and the three
generation phases — including the work that runs in parallel — using a live terminal dashboard on a
TTY and equivalent append-only log lines everywhere else.

Today every command is silent during the run and batch-prints a summary at the end
(`cmds/common.py::echo_messages`). `asyncio.run(...)` blocks with no output, which is the root of
the "is it stuck?" experience.

## Scope

In scope:

1. A decoupled progress seam: pure event types, a pure state reducer, a narrow reporter protocol,
   and a no-op default reporter — none of which depend on Rich or the terminal.
2. A live Rich dashboard reporter for TTYs and a tested log-line reporter for non-TTY/CI, selected
   automatically; a single new `--quiet` flag.
3. Emit sites in `prepare/workspace.py`, the generation pipeline scheduler, and the evidence
   extraction runner.
4. Tests that drive the reporter layer by submitting the same events the pipeline submits, plus
   emit-site tests asserting the produced event sequences.

Out of scope (explicit boundaries):

- Sub-turn / token-level streaming. The agent seam returns `AgentTurnResult` only after a full turn
  completes (`agent.py`), so **per-turn is the finest live granularity**.
- A replay/demo command and golden fixed-width frame snapshots. The reporter is decoupled from the
  pipeline via events, so submitting representative event streams in unit tests is sufficient
  confidence; the animated Rich dashboard is trusted and tuned during daily use.
- ETA estimation. Agent-turn durations are too variable to estimate honestly; elapsed time only.
- Implementing the `project_synthesis` / `daily_synthesis` runners (still placeholders that raise).
  They gain phase + task rows for free because the scheduler emits task events for any kind.
- Changing pipeline scheduling, dependency semantics, or the workflow's success/failure contract.

## Background (current code)

- `cmds/{prepare,generate}.py` resolve a target, run the workflow synchronously via
  `asyncio.run(...)` inside the workflow, then call `echo_messages(result.messages)`. No output
  during the run.
- `prepare/workspace.py::prepare_workspace` performs discovery → project assignment → turn parsing
  → transcript copy → index/metadata write, and returns a single summary message.
- `generate/pipeline.py::GeneratePipelineRunner._run_tasks` is the scheduler: it knows when each
  task is scheduled, completes (`success`/`failed`), or is `blocked` by a failed dependency, and it
  enforces per-kind concurrency (`DEFAULT_CONCURRENCY_LIMITS`: evidence 4, project 2, daily 1).
  Failures do not abort the run — siblings continue; dependents block or proceed per
  `dependency_failure_blocks`.
- `generate/evidence_extraction/runner.py::EvidenceExtractionRunner.run` loops over
  `inputs.turns` in index order, awaiting one agent turn each — the natural `turn x/y` seam.
- The pipeline runs entirely on one `asyncio` loop created by the workflow's `asyncio.run`.
- `pyproject.toml` already omits `integrations/codex_runner.py` from the 100% coverage gate; `rich`
  15.0 is already installed (transitive via `typer`).
- `docs/src/product.md` mandates a **thin CLI surface** and treats **session content as untrusted**.

## Architecture

A pure event seam feeds a pure state reducer; thin reporters render that state. The pipeline depends
only on the narrow reporter protocol, never on Rich.

```
 emit site            pure & tested                      thin reporters
 ---------            -------------                      --------------
 prepare/   ─┐
 pipeline/  ─┼─ emit ▶ ProgressEvent (frozen dataclasses)
 evidence/  ─┘            │
                          ▼
                    ProgressState  (reduce(state, event) -> state)
                          │                ▲ fully unit-tested
              ┌───────────┴───────────┐
              ▼                       ▼
        NullProgressReporter    selected by (isatty, quiet):
        (default, no-op)          • LiveConsoleReporter  → Rich Live (TTY)   [omitted]
                                  • LogReporter          → log lines (non-TTY)[tested]
                                  • NullProgressReporter (quiet; summary only)
```

### Package: `src/prompt_diary/progress/`

- **`events.py`** — frozen dataclass event types (pure data). Tested.
- **`state.py`** — `ProgressState` plus a pure `reduce(state, event) -> ProgressState` that folds
  events into a renderable snapshot (prepare steps; per-phase totals/done/running; per-task rows
  with `turn x/y`; elapsed via injected timestamps). All display *logic* lives here. Tested to 100%.
- **`reporter.py`** — `ProgressReporter` Protocol with one method `emit(event: ProgressEvent)`;
  `NullProgressReporter` (no-op); and pure `select_reporter_mode(*, quiet: bool, isatty: bool) ->
  Literal["live", "log", "quiet"]`. Tested.
- **`log.py`** — `LogReporter`: pure `format_event(event) -> str | None` plus a thin writer to an
  injected text stream. The non-TTY/CI output path is therefore *tested*, not believed. Tested.
- **`console.py`** — `LiveConsoleReporter` (Rich `Live` dashboard, periodic refresh ticker on the
  running loop) and a `build_reporter(mode, *, stream)` factory mapping the selected mode to a
  concrete reporter. Coverage-omitted (added to `[tool.coverage.run] omit`), like
  `codex_runner.py`. This is the only "believed" code.

### Time handling

`reduce` takes timestamps from event fields (events carry a monotonic seconds value supplied by the
emitter), so elapsed/rate computation is deterministic and testable without wall-clock calls in the
reducer. The live ticker in `console.py` (omitted) supplies the periodic "now" for spinner/elapsed
refresh between events.

### Plumbing

Workflow, pipeline runner, phase runners, and `prepare_workspace` accept an optional
`reporter: ProgressReporter = NullProgressReporter()` and call `reporter.emit(...)` at seams.
Default behavior is unchanged: existing tests and library callers emit into a no-op. Only the CLI
constructs a real reporter (via `build_reporter`) and threads it in. `emit` is synchronous — it
updates state and requests a redraw — and runs on the same `asyncio` loop the pipeline already uses,
so no threads or extra loops are introduced.

## Events (vocabulary)

Prepare:

- `PrepareStarted(target, sources)`
- `PrepareStep(name, done, total)` — e.g. `parsing_turns`, `copying_transcripts` counters
- `PrepareFinished(projects, sessions)`

Generate:

- `RunStarted(date, kind_totals)` — totals per phase derived from the plan
- `TaskStarted(kind, task_id, project_key, session_ref, total_turns)`
- `TurnAdvanced(task_id, turn_index, total_turns, turn_ref)` — evidence only, headline counter
- `TaskFinished(kind, task_id, status, detail)` — `status` ∈ success/failed/blocked; `detail` is the
  deterministic metric (e.g. evidence `N turns`) or the first error line
- `RunFinished(...)`

Every event also carries `at` (a monotonic seconds value supplied by the emitter). Elapsed and
running durations are **derived solely by the reducer** from `at`; no event carries a precomputed
duration and the reducer never reads a clock.

Emit sites: prepare steps in `prepare/workspace.py`; `TaskStarted`/`TaskFinished` in the scheduler
(`_run_limited` / completion handling); `TurnAdvanced` in the evidence runner's turn loop. The
scheduler already collects failures and continues, so the display reflects that with no new control
flow.

### Untrusted-content rule

Events carry only **deterministic identifiers and counts** — `project_key`, `session_ref`,
`turn_ref`, indices, totals, durations, controlled status enums. They never carry transcript text,
prompt text, or agent assistant text, honoring `product.md`'s untrusted-content principle.

## Display

**Header:** `Generate · <date> · N projects · M sessions` (+ `partial`/`final`).

**Prepare rows** (stepped): discovered sessions per source → projects assigned →
`parsing turns x/y` → `copying transcripts x/y` → index/metadata written.

**Generate — one row per phase** (evidence / project / daily): a bar `done/total tasks` + running
count, with **nested rows for in-flight tasks only**:

- evidence in-flight: `<project>/<session_ref>  turn x/y`
- project / daily in-flight: `<project>` / `daily` + spinner
- not-yet-startable phases: `waiting on evidence` / `waiting on projects`

**Per finished task:** `✔`/`✖`, elapsed, and one deterministic metric — evidence → `N turns`
(chains written); project / daily → done. Failures show the first line of `TaskResult.errors`.

**Final summary** (always printed, including `--quiet` and non-TTY): totals, any failures, elapsed,
and the existing output-path messages (`report.md`, `daily-report.json`).

## Mode selection & CLI surface (kept thin)

- **Default:** auto — `live` when `stdout.isatty()`, else `log` (same events, one timestamped line
  each; identical information).
- **One new flag `--quiet`:** suppress progress; print only the final summary and errors. Added to
  `prepare`, `generate`, and the `generate` phase subcommands.
- No `--verbose` and no agent-event dump (would risk untrusted content and thicken the CLI).
- `docs/src/product.md` CLI surface block gains `--quiet`.

## Testing & gates

- **Tested by event submission** (no workload): `events`, `state.reduce` (counts, in-order
  `turn x/y`, failed/blocked transitions, prepare counters, elapsed from injected timestamps),
  `select_reporter_mode`, `NullProgressReporter`, and `log.format_event` / `LogReporter` writing to
  a `StringIO` — so the non-TTY/CI output is verified.
- **Emit-site tests:** a `RecordingReporter` test double (in `tests/support/`) captures events;
  assert `prepare_workspace`, the scheduler, and the evidence runner emit the expected sequences
  (this strengthens existing ordered-turn behavior into an asserted event stream).
- **Believed / coverage-omitted:** only `progress/console.py` (Rich `Live` dashboard + factory),
  added to `[tool.coverage.run] omit`. Tuned during daily real runs.
- 100% coverage maintained; `uv run basedpyright`, `uv run ruff check`, `uv run ruff format --check`
  clean.

## Docs to update

- **New** `docs/src/dev/progress-reporting.md`: a dedicated dev page following the existing dev-doc
  style — a one-line "This page covers… It is for developers…" intro, then sections (e.g. `## Role`,
  the `events → state → reporter` seam, emit sites, mode selection, and `## Coverage` noting that
  only `progress/console.py` is omitted). Register it in `docs/src/dev/index.md` with a one-line
  entry alongside the other pages.
- `docs/src/dev/generation-pipeline.md`: a brief pointer to the progress event seam and where emit
  sites attach in the scheduler/runner (detail lives in the new page).
- `docs/src/product.md`: add `--quiet` to the CLI surface block.
- `README.md` / `docs/src/dev/guide.md`: note `--quiet` and the auto TTY/non-TTY behavior if dev
  commands or user-facing flags change.

## Verification gates

`uv run pytest` (or the coverage gate `uv run coverage run -m pytest && uv run coverage report`),
`uv run basedpyright`, `uv run ruff check`, `uv run ruff format --check`, 100% coverage maintained
(new `progress/` modules covered by event-submission tests; `progress/console.py` coverage-omitted
like `codex_runner.py`).
