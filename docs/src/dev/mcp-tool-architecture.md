# MCP Tool Architecture

## Page Role

This page defines implementation constraints for Prompt Diary behavior exposed through MCP tools.
The generation docs define the agent-facing tool contracts. This page defines how those contracts
must be implemented so MCP remains an adapter over reusable, testable package APIs.

## Required Layers

Every MCP tool that implements Prompt Diary behavior must have two layers:

| Layer | Role | Owns |
| --- | --- | --- |
| API layer | Transport-independent package API that can be tested directly and reused by future adapters. | Data models, parsing untrusted inputs into typed request objects, workspace-relative resolution, validation, canonical read/write logic, result models, and structured domain errors. |
| MCP adapter layer | MCP SDK adapter that exposes the API layer through the MCP protocol. | SDK registration, transport schema mapping, workspace-root handoff, and conversion between API results or errors and the MCP response shape. |

The API layer must not depend on MCP SDK request or response types, stdio transport, server
lifecycle, or CLI option parsing. Adapter layers must not reimplement validation, canonical write
logic, or authoritative data models.

## Boundary Rules

- Parse incoming MCP payloads into API request models at the boundary.
- Pass the prepared workspace root explicitly into the API layer. If the MCP adapter uses its
  process current working directory as the prepared workspace root, capture `Path.cwd()` in the
  adapter and pass that path into the API call.
- Return structured API result models for successful operations and structured domain errors for
  rejected operations.
- Keep semantic tests on the API layer. MCP adapter tests should cover registration, schema mapping,
  and response adaptation only.
- Do not branch core behavior by adapter. An MCP call and a future CLI command that submit the same
  API request must receive the same validation and write behavior.

## Read-Only Tools

The two-layer pattern applies to read tools as well as write tools.

`read_session_lines` follows the same structure: the transport-independent API in
`src/prompt_diary/generate/evidence_extraction/session_reader.py` owns session resolution by
`(project_key, session_ref)` via `sessions.index.jsonl`, line-range validation, compaction logic,
and all result and error models. The thin MCP adapter in `src/prompt_diary/mcp/server.py`
resolves the workspace root, passes it into the API, and returns the result. The API layer accepts
no arbitrary filesystem paths.

Because `read_session_lines` performs no writes, no command execution, and no network access, and
because its default output is compact and bounded, it is safe under the server's
`default_tools_approval_mode="approve"`. `write_evidence` remains the only write tool for evidence
extraction.

## Relationship To Tool Contracts

[`MCP Tools`](../generate/mcp-tools/index.md) links to the phase-specific agent-facing schemas,
read/write behavior, and structural rules. The API layer is the implementation authority for those
rules. The MCP SDK handler is only the MCP adapter for that API.
