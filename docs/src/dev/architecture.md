# Architecture

## Tool Shape

Prompt Diary is a Python CLI tool structured in three layers:

- **CLI layer** (`cli.py`) — Typer commands that parse user options and delegate to the API layer.
- **API layer** (`api.py`) — public workflow functions that orchestrate preparation and generation
  by composing core modules.
- **Core modules** — each module owns one concern: date resolution, workspace construction,
  report writing and validation, or prompt template rendering.

## Codemap

The package lives at `src/prompt_diary/`.

| Module | Purpose |
|---|---|
| `cli.py` | Typer CLI entry point. Registers commands and subcommands, maps CLI options to API calls. |
| `api.py` | Public workflow functions (`prepare_prompt_diary`, `generate_prompt_diary`). Orchestrates the pipeline without owning any single concern. |
| `models.py` | Shared typed models used across modules: `ReportTarget`, `TimeWindow`, result types, and type aliases. |
| `errors.py` | Exception hierarchy. `PromptDiaryError` is the base for all user-facing failures. |
| `targets.py` | Resolves CLI date and timezone options into a `ReportTarget` with local and UTC time windows. |
| `workspace.py` | Parses source session files, applies time-window filtering, and writes the prepared workspace with metadata, copied sessions, and session indexes. |
| `report.py` | Constructs the generation prompt, executes external report writers, and validates `report.md` against the report contract. |
| `prompts/` | Prompt template subpackage. Loads `.md` template files from package data and renders them with Jinja2. See [Prompt System](./prompt-system.md). |

## Workflows

### `prepare`

Resolves a report target from CLI options, then builds a bounded workspace for that target day.
The workspace contains only copied session files and deterministic indexes — it defines the
evidence boundary that generation must not expand.

Design principle: **bounded evidence scope**. Preparation decides what is in scope before any
synthesis or generation agent runs. This prevents generation from discovering sessions outside
the target window or reinterpreting the report date.

Product contract: [Workspace Layout](../workspace-layout.md).

### `generate`

Resolves a report target, ensures a prepared workspace exists (preparing if needed), constructs a
generation prompt from the workspace inventory, invokes an external report writer, and validates
the resulting `report.md` against the report contract.

Design principle: **evidence-grounded reporting**. The generation prompt includes the workspace
inventory and the report contract rules. The validation step checks that every citation in the
report resolves to a real session and line span in the prepared workspace. Claims without valid
citations fail validation.

Product contracts: [Report Generation](../generate/index.md),
[Evidence Contract](../generate/evidence-contract.md),
[Project Synthesis](../generate/project-synthesis.md),
[Daily Report Synthesis](../generate/daily-synthesis.md).

### `prompts`

Loads prompt templates from package data and renders them with variable substitution. The three
generation prompts (evidence extractor, project synthesizer, daily synthesizer) are defined as
`.md` files inside the `prompts/` subpackage and are also included in the product docs via mdbook.

Design principle: **single source of truth**. Each prompt file is the authoritative source for
both the runtime agent prompt and the rendered documentation. See
[Prompt System](./prompt-system.md) for details.

## CLI Surface

The user-facing CLI commands and date targeting rules are defined in
[Prompt Diary Product](../product.md#cli-surface). `cli.py` implements those commands and adds the
`prompts` subcommand group for prompt template inspection (see
[Prompt System](./prompt-system.md)).

`report` and `prompt-diary` are both registered as console entry points and invoke the same CLI.
