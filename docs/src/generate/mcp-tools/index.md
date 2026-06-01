# MCP Tools

The Prompt Diary MCP server exposes agent-facing tools used during report generation. These tools
are internal to Prompt Diary's generation workflow: they serve extraction and synthesis agents
running inside a prepared workspace, not end-user CLI workflows.

Implementation must follow the two-layer structure defined in
[MCP Tool Architecture](../../dev/mcp-tool-architecture.md): a transport-independent API layer
owns data models, validation, and canonical read/write logic, while the MCP SDK handler is only
the current MCP adapter.

## Registered Tools

| Tool | Phase | Purpose |
| --- | --- | --- |
| `prompt_diary_ping` | — | Connectivity check; returns stable boilerplate. |
| `read_session_lines` | Evidence Extraction | Read a physical line range from one indexed session; compact by default, full raw on request. Read-only. |
| `write_evidence` | Evidence Extraction | Validate and append one evidence chain to the canonical session evidence card. |
| `write_work_item` | Project Synthesis | Validate and append one work item to the project synthesis output. |

## Phase Tool Contracts

- [Evidence Extraction Tools](./evidence-extraction.md)
- [Project Synthesis Tools](./project-synthesis.md)
- [Daily Report Synthesis Tools](./daily-synthesis.md)

## Common Rules

MCP tools run with their process current working directory set to the prepared report workspace
root. They must not infer the target report date from hidden global state; the prepared workspace
root is the only filesystem root used by these tools.

Normal tool results should return stable references rather than filesystem paths. If a tool
explicitly documents a returned file locator for debugging or inspection, that locator must be
relative to the prepared report workspace root.

Rejected tool calls should be structured and actionable:

```json
{
  "status": "invalid",
  "errors": [
    {
      "path": "evidence_chain.outcomes[0].citations[0].lines",
      "message": "line span 240-245 is outside turn T0001 span 42-239",
      "hint": "cite only lines inside the evidence chain's indexed turn"
    }
  ]
}
```

## Code Placement

MCP SDK registration and protocol adaptation belong under `src/prompt_diary/mcp/`.

Canonical parsing, validation, artifact reads and writes, and phase behavior belong under the
owning generation phase package:

- `src/prompt_diary/generate/evidence_extraction/`
- `src/prompt_diary/generate/project_synthesis/`
- `src/prompt_diary/generate/daily_synthesis/`

MCP modules should call those APIs instead of owning generation semantics.
