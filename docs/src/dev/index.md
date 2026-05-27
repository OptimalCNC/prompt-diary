# Development

These pages document how the Prompt Diary codebase is organized, how the main APIs connect to the
product docs, and how to work on the project. They are written for developers modifying the code.

Product-level purposes, principles, and contracts live in the [product](../product.md) and
[generation](../generate/index.md) docs. These development pages explain how the code implements
them.

- [Architecture](./architecture.md) — tool shape, codemap, workflow design, CLI surface.
- [Codex Agent Runner](./codex-agent-runner.md) — initial needs and basic design for the async
  Codex SDK wrapper used by generation orchestration.
- [Development Guide](./guide.md) — environment setup, build, test, lint, release.
- [Prompt System](./prompt-system.md) — how prompt templates are stored, loaded, and modified.
