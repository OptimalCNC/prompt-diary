# Prompt Diary

[![CI](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml)
[![Publish](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml)
[![PyPI](https://img.shields.io/pypi/v/prompt-diary.svg)](https://pypi.org/project/prompt-diary/)
![Coverage budget](https://img.shields.io/badge/coverage%20budget-100%25-brightgreen.svg)

Prompt Diary prepares bounded workspaces from local assistant session history and generates evidenced prompt diary reports that help users review and improve how they collaborate with AI coding agents.

The tool targets Python 3.10 and newer. The package exposes `report` and `prompt-diary`
console commands after installation.

## Usage

Install Prompt Diary from PyPI as an isolated uv tool:

```bash
uv tool install prompt-diary
```

Then run:

```bash
report --help
prompt-diary --help
report prepare --date 2026-05-12 --timezone Asia/Shanghai
report generate --date 2026-05-12 --timezone Asia/Shanghai
```

Generation is an artifact-first pipeline with standalone phase commands:

```bash
report generate evidence --date 2026-05-12 --timezone Asia/Shanghai --project-key <project> --session-ref S0001
report generate project --date 2026-05-12 --timezone Asia/Shanghai --project-key <project>
report generate daily --date 2026-05-12 --timezone Asia/Shanghai
```

Pass `--notion` to `report generate` to also publish the finished report as a new row in a Notion
database (the deterministic `report.notion.json` payload is always written beside `report.md`
regardless). Set `NOTION_API_KEY` (a Notion internal-integration token) and `NOTION_PAGE_ID` (the
target database id, shared with that integration) in the environment — credentials never pass on the
command line. Each run appends a new dated row; re-publishing never edits or deletes existing rows.

Generation drives a three-phase, artifact-first pipeline — evidence extraction, then project
synthesis, then daily report synthesis — through the Codex CLI, producing `daily-report.json`, the
rendered `report.md`, and `report.notion.json`. It requires the `codex` CLI to be installed and
authenticated; the subcommands above run each phase standalone against an already-prepared workspace.

Prepared workspaces and generated reports are written under a per-user data directory by default
(`~/.local/share/prompt-diary/` on Linux; the platform equivalent on macOS and Windows), organized
by date as `<reports-root>/work/<YYYY-MM-DD>/`. Override the location with `--reports-root <path>`
on `prepare` and `generate`, or by setting `PROMPT_DIARY_HOME`; precedence is `--reports-root` over
`PROMPT_DIARY_HOME` over the default data directory. (Earlier versions wrote to `./.reports` in the
current directory — pass `--reports-root .reports` to keep using an existing local directory.)

Both `prepare` and `generate` show a live progress dashboard when running on a TTY and write
append-only log lines when output is piped, redirected, or running in CI. Pass `--quiet` to either
command to suppress the live output and print only the final summary.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) for Python version, environment,
dependency, build, and release workflows.

Read [`docs/src/product.md`](docs/src/product.md) before designing new features, changing report
content, or modifying the generation pipeline. It defines the tool's purposes and principles that
downstream design must satisfy.

For environment setup, build commands, type checking, testing, coverage, linting, and pre-submit
checks, including the optional Ubuntu 24.04 devcontainer, see the
[Development Guide](docs/src/dev/guide.md). For codebase architecture and API design, see
[Architecture](docs/src/dev/architecture.md).
