# Devcontainer

This devcontainer builds a local Ubuntu 24.04 image for Prompt Diary development.

## Included Tools

- `uv` for Python, dependency, build, and release workflows.
- Python development basics, build tools, Git, GitHub CLI, `jq`, `rg`, `fd`, `tree`, `tmux`, and shell utilities.
- Bun-backed JavaScript CLI support, including a `node` shim for tools with Node shebangs.
- Claude Code and OpenAI Codex for agent-assisted development.

## Container Layout

- The repository is mounted at `/ws/src/ReportGenerator`.
- `/ws` is a persistent workspace volume.
- `.venv` is a container-only named volume mounted over the repository path, so the container does
  not reuse or rewrite a host virtual environment.
- `/opt/cache` persists `uv` caches.
- `/opt/uv` persists `uv tool install` environments, bins, and uv-managed Python interpreters.
- GitHub CLI, Claude Code, and Codex settings live in persistent volumes under the `ubuntu` user's
  home directory.

The Dockerfile intentionally installs the current Claude Code, Codex CLI, and Bun releases at image
build time. Rebuild the container to pick up newer AI CLI tooling.

## First Run

Open the repository in a devcontainer-compatible editor and rebuild the container. The
`postCreateCommand` runs:

```bash
uv sync --locked --python 3.10
```

Run these commands inside the container to verify the development environment:

```bash
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest
```

Authenticate agent and GitHub tools inside the container when needed:

```bash
gh auth login
claude
codex
```
