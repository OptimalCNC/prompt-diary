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

To enable the optional Codex SDK runner in that tool environment, run:

```bash
prompt-diary codex bootstrap
```

Then run:

```bash
report --help
prompt-diary --help
report prepare --date 2026-05-12 --timezone Asia/Shanghai
```

Generation runs an external report-writing model command inside the prepared workspace. The
command must read the generated prompt from standard input and create `report.md` in its current
working directory. Configure it before running `generate`; for example, with Codex CLI:

```bash
export PROMPT_DIARY_REPORT_WRITER_COMMAND="codex exec -"
report generate --date 2026-05-12 --timezone Asia/Shanghai
```

Set `PROMPT_DIARY_REPORT_WRITER_TIMEOUT_SECONDS` to override the default 600-second writer
timeout.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) for Python version, environment,
dependency, build, and release workflows.

Read [`docs/src/product.md`](docs/src/product.md) before designing new features, changing report
content, or modifying the generation pipeline. It defines the tool's purposes and principles that
downstream design must satisfy.

For environment setup, build commands, type checking, testing, coverage, linting, and pre-submit
checks, see the [Development Guide](docs/src/dev/guide.md). For codebase architecture and API
design, see [Architecture](docs/src/dev/architecture.md).
