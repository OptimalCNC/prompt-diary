# Prompt System

The prompt system manages the generation prompt templates that guide evidence extraction, project
synthesis, and daily report synthesis agents.

## Where Prompts Live

Prompt files are `.md` files inside the `src/prompt_diary/generate/prompts/` subpackage. This location
serves two purposes:

- **Runtime**: the files are installed as package data with the wheel, so `importlib.resources`
  can load them after `pip install`.
- **Documentation**: dedicated prompt pages under `docs/src/generate/` contain only mdbook
  `{{#include}}` directives for the runtime prompt files, so the rendered prompt pages match the
  current prompt content. Parent generation contract and synthesis pages keep prompt source
  metadata and link to those dedicated prompt pages.

## Python API

The `prompt_diary.generate.prompts` module exposes one function per prompt:

- `evidence_extractor_prompt(*, project_key: str, project_json: str, session_ref: str,
  session_index_record: str, target_turn: str) -> str`
- `evidence_extractor_next_turn_prompt(*, write_evidence_result: str,
  target_turn: str) -> str`
- `project_synthesizer_prompt(*, project_key: str, project_json: str,
  evidence_chains: str) -> str`
- `project_synthesizer_next_prompt(*, project_key: str, uncovered_turns: str) -> str`
- `daily_synthesizer_prompt() -> str`

Each function loads the template from package data and renders it with Jinja2. Variable
substitution uses `StrictUndefined`, so missing variables raise an error at render time. For
prompts without variables, the function takes no arguments.
Evidence extractor controlled-value descriptions are maintained next to the prompt API and rendered
into the runtime prompt, so the enum values have one Python source of truth.

The Jinja2 dependency and template file loading are implementation details hidden from callers.

## Runtime Language Norm

Content-language instructions are injected outside the phase prompt templates. The generation
composition root wraps the Codex agent factory so evidence extraction, project synthesis, and daily
synthesis all receive the same rendered norm through `AgentConfig.developer_instructions`; the
wrapper also writes a generated `AGENTS.md` into the prepared workspace before the first agent
conversation is minted.

The norm applies to Codex-generated natural-language content values. It tells agents to preserve
JSON keys, MCP tool names, enum values, IDs, citations, paths, commands, code identifiers, and
verbatim source text. Deterministic renderer-owned labels, headings, fallbacks, and Notion metadata
banners are not localized by this mechanism.

## CLI

The `report prompts` subcommand group prints rendered prompts to stdout:

```bash
report prompts evidence-extractor \
  [--project-key KEY] [--project-json JSON] \
  [--session-ref REF] [--session-index-record JSON] \
  [--target-turn JSON]
report prompts evidence-extractor-next-turn \
  [--write-evidence-result JSON] [--target-turn JSON]
report prompts project-synthesizer
report prompts daily-synthesizer
```

This is primarily a verification tool: after packaging and installing the wheel in a clean
environment, these commands confirm that the prompt files are accessible.

## How To Modify A Prompt

Edit the `.md` file in `src/prompt_diary/generate/prompts/`. The change takes effect in both the runtime
API and the rendered product docs automatically.

If a prompt needs a new template variable, add it as a keyword argument to the corresponding
function in `src/prompt_diary/generate/prompts/__init__.py` and pass it through the `_render` call.

## How To Add A New Prompt

1. Create the `.md` template file in `src/prompt_diary/generate/prompts/`.
2. Add a public function in `src/prompt_diary/generate/prompts/__init__.py` that calls `_render` with the
   filename and any required variables.
3. Export the function from `src/prompt_diary/__init__.py`.
4. Add a CLI command in `src/prompt_diary/cli.py` under the `_prompts_app` Typer group.
5. Add tests in `tests/generate/test_prompts.py` — one for the API function, one for the CLI command.
6. Add a dedicated prompt doc page under `docs/src/generate/` that contains only an
   `{{#include}}` directive for the runtime prompt file. The include path from a prompt doc page
   to the package is `../../../src/prompt_diary/generate/prompts/<filename>`.
   Short follow-up prompts may instead be quoted from the parent contract page when they are only
   used as a continuation of a full prompt.
7. Add the prompt source note and a link to the prompt doc page on the relevant parent generation
   page.
8. Add the prompt doc page to `docs/src/SUMMARY.md` as a child of that parent page.

## How mdbook Includes Work

Dedicated prompt pages include prompts with a relative path that reaches back into the Python
package. For example, `docs/src/generate/evidence-extractor-prompt.md` includes the runtime
template with:

```text
{{#include ../../../src/prompt_diary/generate/prompts/evidence-extractor.md}}
```

mdbook resolves this path relative to the prompt page's directory (`docs/src/generate/`). The
prompt content is rendered inline as formatted markdown on the prompt page. Keep prompt source
metadata on the parent generation page, and link to the prompt page instead of including the
prompt template directly.
