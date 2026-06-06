# Progress Reporting

This page covers the progress reporting seam (`prompt_diary/progress/`) that surfaces what
`prepare` and `generate` are doing in the terminal. It is for developers changing the CLI feedback
or adding progress to a new phase.

## Role

The pipeline emits structured **progress events** into a narrow `ProgressReporter`; the reporter
folds them through a pure reducer into a `ProgressState` and renders it. The pipeline depends only
on the reporter protocol, never on Rich.

## Seam: events -> state -> reporter

- `events.py` — frozen event types (`PhaseStarted`, `PhaseFinished`, `PrepareStarted`,
  `PrepareStep`, `PrepareFinished`, `RunStarted`, `TaskStarted`, `TurnAdvanced`, `TaskFinished`,
  `RunFinished`). Each carries only deterministic identifiers and counts; never transcript or agent
  text.
- `state.py` — `reduce(state, event) -> ProgressState`, a pure fold (per-kind counts, per-task
  rows, `turn x/y`, task elapsed, and accumulated phase elapsed). All the state that drives the
  display lives here and is unit-tested.
- `reporter.py` — the `ProgressReporter` protocol, `NullProgressReporter` (the default), and
  `select_reporter_mode(quiet, isatty)`.
- `log.py` — `LogReporter` for non-TTY/CI: one tested log line per event (`RunFinished` produces no line; the CLI prints the final summary separately).
- `console.py` — `LiveConsoleReporter` (Rich `Live` dashboard) and `build_reporter`.

## Emit sites

- `prepare/workspace.py` — prepare phase timing and prepare stage steps.
- `generate/pipeline.py` — aggregate evidence/project/daily/rendering phase timing,
  `TaskStarted`/`TaskFinished` (including `blocked`), threading the reporter to each phase runner's
  `run(..., reporter=...)`. The in-pipeline rendering phase timing comes from the pipeline like the
  other kinds; the rendering runner emits no phase events of its own.
- `generate/evidence_extraction/runner.py` — `TurnAdvanced` per committed turn.
- `generate/rendering/notion.py` — Notion publish timing for `generate` publishing and
  `generate render --notion`; its progress phase id is `publish`.
- `generate/workflow.py` — `RunStarted`/`RunFinished` and standalone phase timing.

A phase runner that wants per-item progress emits via the `reporter` argument it receives; runners
that do not still accept and ignore it. Every event carries a monotonic `at` timestamp supplied by
the emitter; the reducer derives elapsed/durations from it and never reads a clock. Renderers may
refresh active elapsed displays from the current monotonic clock, but that clock value stays at the
rendering edge rather than entering pipeline logic.

## Mode selection

`select_reporter_mode(quiet, isatty)` chooses `quiet` / `live` / `log`. The CLI builds the reporter
in `cmds/common.py::build_cli_reporter`; `--quiet` forces summary-only. The dashboard renders to
stderr so report paths on stdout stay pipeable.

## Coverage

Everything except `progress/console.py` is unit-tested — the reducer and the log path by submitting
the same events the pipeline emits, and the emit sites via a `RecordingReporter`. `progress/console.py`
(the Rich `Live` dashboard) is coverage-omitted in `pyproject.toml`, like
`integrations/codex_runner.py`, and is tuned during daily use.
