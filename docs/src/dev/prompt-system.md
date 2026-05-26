# Prompt System

The prompt system manages the generation prompt templates that guide evidence extraction, project
synthesis, and daily report synthesis agents.

## Where Prompts Live

Prompt files are `.md` files inside the `src/prompt_diary/prompts/` subpackage. This location
serves two purposes:

- **Runtime**: the files are installed as package data with the wheel, so `importlib.resources`
  can load them after `pip install`.
- **Documentation**: the product docs include them via mdbook's `{{#include}}` directive, so
  the rendered docs always show the current prompt content.

## Python API

The `prompt_diary.prompts` module exposes one function per prompt:

- `evidence_extractor_prompt(*, working_dir: str, session_ref: str) -> str`
- `project_synthesizer_prompt() -> str`
- `daily_synthesizer_prompt() -> str`

Each function loads the template from package data and renders it with Jinja2. Variable
substitution uses `StrictUndefined`, so missing variables raise an error at render time. For
prompts without variables, the function takes no arguments.

The Jinja2 dependency and template file loading are implementation details hidden from callers.

## CLI

The `report prompts` subcommand group prints rendered prompts to stdout:

```bash
report prompts evidence-extractor [--working-dir DIR] [--session-ref REF]
report prompts project-synthesizer
report prompts daily-synthesizer
```

This is primarily a verification tool: after packaging and installing the wheel in a clean
environment, these commands confirm that the prompt files are accessible.

## How To Modify A Prompt

Edit the `.md` file in `src/prompt_diary/prompts/`. The change takes effect in both the runtime
API and the rendered product docs automatically.

If a prompt needs a new template variable, add it as a keyword argument to the corresponding
function in `src/prompt_diary/prompts/__init__.py` and pass it through the `_render` call.

## How To Add A New Prompt

1. Create the `.md` template file in `src/prompt_diary/prompts/`.
2. Add a public function in `src/prompt_diary/prompts/__init__.py` that calls `_render` with the
   filename and any required variables.
3. Export the function from `src/prompt_diary/__init__.py`.
4. Add a CLI command in `src/prompt_diary/cli.py` under the `_prompts_app` Typer group.
5. Add tests in `tests/test_prompts.py` — one for the API function, one for the CLI command.
6. Add an `{{#include}}` directive in the relevant product doc under `docs/src/generate/`. The
   include path from a doc file to the package is `../../../src/prompt_diary/prompts/<filename>`.

## How mdbook Includes Work

The product docs include prompts with a relative path that reaches back into the Python package:

```text
{{#include ../../../src/prompt_diary/prompts/evidence-extractor.md}}
```

mdbook resolves this path relative to the doc file's directory (`docs/src/generate/`). The prompt
content is rendered inline as formatted markdown. A `---` horizontal rule separates the source
note from the included content.
