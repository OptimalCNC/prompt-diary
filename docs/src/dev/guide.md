# Development Guide

## Documentation

Before writing documentation, identify the targeted readers for each section, what that section
should provide to them, and the writing principles that follow from that purpose. For example,
`Usage` in the README is for end users installing and running the tool, so keep release
verification, debugging, and maintainer-only commands out of it.

## Environment

Set up the development environment:

```bash
uv sync
```

Run the CLI from the project environment:

```bash
uv run report --help
```

Install the local checkout as an isolated uv tool:

```bash
uv tool install .
```

## Dependencies

Add runtime dependencies with:

```bash
uv add <package>
```

Add development-only dependencies with:

```bash
uv add --dev <package>
```

## Build And Release

Build source and wheel distributions:

```bash
uv build
```

Publish release artifacts only after the package metadata and target registry are configured:

```bash
uv publish
```

## Type Checking

Type checking uses [basedpyright](https://docs.basedpyright.com/latest/configuration/command-line/).
The project config enables strict mode for `src` and `tests`. Add type annotations by best effort
for new and changed code. This is a hard rule: prefer explicit, checkable types whenever they
improve clarity or allow basedpyright to verify behavior.

Use accurate types when possible instead of relying on repeated validation. At module boundaries,
parse untrusted or loosely structured inputs into precise internal types, then pass those types
through the rest of the code. Do not validate a value and then continue passing the original
loose representation when a richer type, dataclass, `TypedDict`, `NewType`, enum, or other
structured representation can preserve the invariant for callers and the type checker.

```bash
uv run basedpyright
```

## Tests

Tests use [pytest](https://docs.pytest.org/). The pytest config lives in `pyproject.toml` and uses
strict config and marker validation.

```bash
uv run pytest
```

Codex/MCP integration contract tests are opt-in because they may spend model tokens and require
local SDK setup. Bootstrap the optional SDK into the current development environment before running
the live wrapper test:

```bash
uv run prompt-diary codex bootstrap
```

Then run the opt-in real Codex agent wrapper test:

```bash
uv run pytest -m codex_mcp --run-codex-mcp tests/integrations/test_codex_mcp_integration.py
```

## Coverage

Coverage uses [coverage.py](https://coverage.readthedocs.io/) and is configured to require 100%
line coverage for package code. Default coverage uses mocked Codex runner tests; the real Codex
agent wrapper test remains opt-in because it may spend model tokens.

```bash
uv run coverage run -m pytest
uv run coverage report
```

## Linting And Formatting

Linting and formatting use [ruff](https://docs.astral.sh/ruff/). Ruff is configured for
Python 3.10. The lint rule set is explicit and intentionally broader than Ruff's defaults,
covering imports, modernization, bug-prone patterns, datetime safety, security checks, pathlib
usage, pytest style, exception handling, and simplification rules.

```bash
uv run ruff check
uv run ruff format --check
uv run ruff format
```

## Pre-Submit Checks

Before submitting changes, run:

```bash
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest
uv run coverage run -m pytest
uv run coverage report
uv build
```
