# Prompt Diary

Language: English | [简体中文](README.zh-CN.md)

[![CI](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml)
[![Publish](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml)
[![PyPI](https://img.shields.io/pypi/v/prompt-diary.svg)](https://pypi.org/project/prompt-diary/)
![Coverage budget](https://img.shields.io/badge/coverage%20budget-100%25-brightgreen.svg)

Prompt Diary prepares bounded workspaces from local assistant session history and generates evidenced prompt diary reports that help users review and improve how they collaborate with AI coding agents.

The tool targets Python 3.10 and newer. The package exposes `report` and `prompt-diary`
console commands after installation.

## Usage

Install Prompt Diary from PyPI as an isolated `uv` tool:

```bash
uv tool install prompt-diary
```

### Configuration

Configuration is optional for local-only reports. Run `prompt-diary config init` when you want
Notion publishing; it also lets you accept or override the data folder and records the required
reporter name:

```bash
prompt-diary config init
```

The setup prompts for the Notion integration token, optional data folder, target database id, and
required reporter name for the `汇报人` column. Credentials are validated before they are saved,
written to a single config file with `0600` permissions, and never passed on the command line. Use
these commands to inspect the stored configuration:

```bash
prompt-diary config show   # print the configuration (token masked) and the file location
prompt-diary config path   # print the config file location
```

Set the language for Codex-generated natural-language report content with:

```bash
prompt-diary config language zh-Hans
PROMPT_DIARY_CONTENT_LANGUAGE=zh-Hans report generate
```

The config file lives under the per-user config directory (`~/.config/prompt-diary/config.json` on
Linux; the platform equivalent on macOS and Windows), overridable with `PROMPT_DIARY_CONFIG`.
Environment variables still override stored credentials and settings when present, including
`NOTION_API_KEY`, `NOTION_PAGE_ID`, `PROMPT_DIARY_HOME`, and
`PROMPT_DIARY_CONTENT_LANGUAGE`. Content language supports `en`, `zh-Hans`, and `zh-Hant`. It
applies to generated natural-language content values; deterministic renderer-owned labels,
headings, fallbacks, and Notion metadata banners remain English in this release.

### Generate a report

Run the full report workflow directly:

```bash
report generate
```

By default, `report generate` first resolves the timezone, then targets the previous calendar day
in that timezone: the most recent completed day. It prepares the workspace if it is missing,
generates the report, and renders `report.md` and `report.notion.json`. The timezone is resolved
from `--timezone`, then `PROMPT_DIARY_TIMEZONE`, then `TZ`, then the system timezone, then UTC.
Notion publishing is enabled by default when both the Notion token and database id resolve from the
stored config or environment. Pass `--no-notion` to skip publishing, or pass `--notion` to require
publishing and fail if Notion is not configured.

Use explicit targeting when needed:

```bash
report generate --date 2026-05-12 --timezone Asia/Shanghai
report generate --today
report generate --date 2026-05-12 --timezone Asia/Shanghai --notion
```

The reports root is the base folder for prepared workspaces and generated report files. By default
it is the per-user data directory (`~/.local/share/prompt-diary/` on Linux; the platform equivalent
on macOS and Windows). Each run writes under `<reports-root>/work/<YYYY-MM-DD>/`. Override the
location with `--reports-root <path>`, `PROMPT_DIARY_HOME`, or the saved data folder. Precedence is
`--reports-root`, then `PROMPT_DIARY_HOME`, then the saved data folder, then the default per-user
data directory. Earlier versions wrote to `./.reports` in the current directory; pass
`--reports-root .reports` to keep using an existing local directory.

In an interactive terminal, `report generate` shows live progress. When output is redirected or
running in CI, it prints plain log lines. Pass `--quiet` to suppress the live output and print only
the final summary.

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
