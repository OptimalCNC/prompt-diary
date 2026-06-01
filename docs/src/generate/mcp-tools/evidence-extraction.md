# Evidence Extraction Tools

Evidence extraction tools are the agent-facing read and write path for extracted session evidence.
`read_session_lines` lets the extractor agent read physical line ranges from indexed sessions
through the MCP server rather than raw shell reads. `write_evidence` accepts one draft evidence
chain at a time, validates it through the generation API, and creates or updates the canonical
session evidence card.

Shared workspace, result, and error rules are defined in [MCP Tools](./index.md).
The evidence data model is defined by the [Evidence Contract](../evidence-contract.md).

## Required Tools

The Evidence Extraction phase requires these tools:

| Tool | Purpose |
| --- | --- |
| `read_session_lines` | Read a physical line range from one indexed session, compact by default or full raw. Read-only; safe by default. |
| `write_evidence` | Check one draft evidence chain and create or update the canonical session evidence card. |

## Workspace Resolution

Both tools resolve sessions by `(project_key, session_ref)` against the prepared workspace.
`project_key` identifies the project directory under `projects/<project_key>`. `session_ref` is
unique within one project and resolves through `projects/<project_key>/sessions.index.jsonl`.
Neither tool accepts an arbitrary filesystem path.

`write_evidence` additionally determines the target evidence file as
`projects/<project_key>/evidence/<session_ref>.json`. There is at most one canonical evidence card
file per indexed session. The tool may append multiple chains to that card, but generation must not
create a separate flat `evidence_cards.jsonl` as the source of truth. If no chain is written for an
indexed session, downstream synthesis treats that missing card as an evidence gap for the indexed
session.

## `read_session_lines`

Read a physical line range from one indexed session. The session is resolved by `project_key` and
`session_ref` against the prepared workspace's `sessions.index.jsonl`; the tool never accepts an
arbitrary path. Line numbers are 1-based and match the physical JSONL line numbers produced by
`prepare`, so compact records and citations stay stable.

This tool is read-only and safe under the server's `default_tools_approval_mode="approve"`.
`write_evidence` remains the only write tool for evidence extraction.

Input schema:

```json
{
  "project_key": "<project_key>",
  "session_ref": "<session_ref>",
  "start_line": 23,
  "end_line": 114,
  "mode": "compact"
}
```

`mode` is `"compact"` (default) or `"full"`. The `mode` parameter description in the tool schema
warns that `"full"` returns raw JSONL lines and can be very large; use it only for a narrow range
where exact raw content is necessary.

### Compact return shape

Compact mode returns bounded structured records. One record per physical line:

```json
{
  "status": "ok",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "line_range": {"start": 23, "end": 114},
  "mode": "compact",
  "records": [
    {
      "line": 27,
      "record_type": "user",
      "role": "user",
      "content_kinds": ["tool_result"],
      "summary": "Tool result.",
      "text_preview": null,
      "tool_uses": [],
      "tool_results": [
        {
          "kind": "file",
          "status": null,
          "file_path": "projects/.../evidence/S0001.json",
          "command": null,
          "preview": "{\"schema_version\":1,...",
          "raw_bytes": 98099,
          "truncated": true
        }
      ],
      "raw_bytes": 98099,
      "raw_sha256": "<sha256>",
      "truncated": true
    }
  ]
}
```

Compact record fields:

| Field | Type | Description |
| --- | --- | --- |
| `line` | int | Absolute 1-based physical line number. |
| `record_type` | str | Source record type (`user`, `assistant`, `system`, `system:init`, source-specific equivalents, or `unknown`). |
| `role` | str \| null | Message role when present. |
| `content_kinds` | list[str] | High-level content kinds present: `text`, `tool_use`, `tool_result`, `thinking`. |
| `summary` | str | Deterministic short description of the record. |
| `text_preview` | str \| null | Full text for user/assistant text messages; null when absent or suppressed. |
| `tool_uses` | list | Tool invocations, each with `name` (str) and `input_summary` (str). |
| `tool_results` | list | Tool results, each with `kind`, `status`, `file_path`, `command`, `preview`, `raw_bytes`, `truncated`. |
| `raw_bytes` | int | UTF-8 byte length of the original physical line. |
| `raw_sha256` | str | SHA-256 hex digest of the original physical line. |
| `truncated` | bool | Whether any data on this record was trimmed. |

### Compact trimming policy

Compact mode trims only:

- **Tool result payloads larger than 1 KiB** — trimmed to a head preview (~320 bytes) and tail
  preview (~160 bytes) joined by an elision marker. `raw_bytes` and `truncated: true` are always
  reported.
- **Assistant reasoning/thinking** — omitted entirely. The `summary` reads `"Assistant reasoning
  omitted."` and `truncated: true` is set.

Compact mode never trims:

- Normal user messages.
- Normal assistant text messages.
- Tool result payloads at or below 1 KiB.

### Full return shape

Full mode returns verbatim raw JSONL lines. Results can be very large.

```json
{
  "status": "ok",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "line_range": {"start": 27, "end": 27},
  "mode": "full",
  "records": [
    {
      "line": 27,
      "raw_line": "{...}",
      "raw_bytes": 98099,
      "raw_sha256": "<sha256>"
    }
  ]
}
```

Full record fields: `line` (int), `raw_line` (str), `raw_bytes` (int), `raw_sha256` (str).

The maximum range for compact mode is 2000 lines; for full mode, 100 lines.

### Error model

Invalid inputs return a structured result:

```json
{
  "status": "invalid",
  "errors": [
    {
      "field": "session_ref",
      "message": "unknown session_ref 'S9999' for project 'ReportGenerator-e6ff7eeda632'",
      "hint": "use a session_ref listed in sessions.index.jsonl"
    }
  ]
}
```

Error cases: unknown `project_key`, unknown `session_ref`, missing session file, `start_line < 1`,
reversed range (`end_line < start_line`), `start_line` or `end_line` past the session's last line,
range too broad for the requested mode.

## `write_evidence`

Check one draft evidence chain and write it to the canonical session evidence card. Examples of
canonical evidence chains are in the [Evidence Contract](../evidence-contract.md).
The controlled values in this schema duplicate the enum definitions in
`src/prompt_diary/generate/prompts/__init__.py` so this tool contract remains self-contained.

Input schema:

```json
{
  "project_key": "<project_key>",
  "session_ref": "<session_ref>",
  "evidence_chain": {
    "turn_ref": "<turn_ref>",
    "trigger": {
      "type": "explicit_user_message|implicit_context|user_correction|user_approval|resume_or_continue",
      "summary": "<non-empty string>",
      "quoted_messages": [
        {
          "text": "<redacted user-authored text>",
          "citations": [
            {"lines": "<start>-<end>"}
          ]
        }
      ],
      "citations": [
        {"lines": "<start>-<end>"}
      ]
    },
    "agent_reactions": [
      {
        "summary": "<non-empty string>",
        "citations": [
          {"lines": "<start>-<end>"}
        ]
      }
    ],
    "outcomes": [
      {
        "category": "code_outcome|document_outcome|decision_outcome|validation_outcome|process_outcome|research_outcome|blocker_outcome|other",
        "summary": "<non-empty string>",
        "citations": [
          {"lines": "<start>-<end>"}
        ]
      }
    ],
    "observed_checks": [
      {
        "type": "command_output|test_output|artifact_inspection|user_feedback|other",
        "summary": "<non-empty string>",
        "citations": [
          {"lines": "<start>-<end>"}
        ]
      }
    ],
    "terminal_state": {
      "type": "material_result|no_material|blocked|interrupted|failed|clarification_only|evidence_gap|other",
      "summary": "<non-empty string>",
      "citations": [
        {"lines": "<start>-<end>"}
      ]
    },
    "materiality": "material|minor|none"
  }
}
```

Write behavior:

- If the evidence file does not exist, the tool creates a canonical session evidence card from
  `projects/<project_key>/project.json` and the matching row in
  `projects/<project_key>/sessions.index.jsonl`, then appends the chain.
- If the evidence file already exists, the tool validates the existing card and appends the chain.
- Agents provide the assigned `turn_ref` directly as `evidence_chain.turn_ref`; the tool validates
  it against `projects/<project_key>/sessions.index.jsonl`.
- A card must not contain duplicate evidence for one `turn_ref`.
- Writes should be serialized per `(project_key, session_ref)` and committed with atomic file
  replacement so parallel extraction agents cannot corrupt a card.
- If a write is rejected, the tool must return structured, actionable errors that name the invalid
  field, explain the problem, and include a correction hint when possible.
- Rejected writes are not committed. The extractor may correct the draft from the returned errors
  and retry until one chain for the assigned `turn_ref` is committed.

Successful result:

```json
{
  "status": "appended",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "turn_ref": "T0001"
}
```

## Structural Rules

`write_evidence` must apply these rules before committing a chain:

- The current working directory is the prepared report workspace root.
- `projects/<project_key>` contains `project.json` and `sessions.index.jsonl`.
- `project_key` matches the `project_key` in `projects/<project_key>/project.json`.
- `session_ref` resolves to exactly one row in `projects/<project_key>/sessions.index.jsonl`.
- Input is one evidence chain, not a full session evidence card.
- `evidence_chain.turn_ref` resolves to exactly one `turns[]` item in the session index row.
- Existing card chains do not already contain evidence for that `turn_ref`.
- Required summaries are non-empty.
- `trigger.type` is one of `explicit_user_message`, `implicit_context`, `user_correction`,
  `user_approval`, or `resume_or_continue`.
- Citation line spans are numeric, ordered, and contained by the indexed turn identified by
  `turn_ref`.
- The MCP server enforces citation structure and boundaries. The extractor remains responsible for
  ensuring cited lines semantically support the evidence-chain claim.
- Material outcomes cite agent reaction evidence, not only trigger evidence.
- `outcomes[*].category` is one of the controlled outcome categories and is not a completion,
  verification, or engagement label.
- `terminal_state` is required for every evidence chain.
- Input may omit material outcomes only when `terminal_state.type` explains the non-success ending.
- `terminal_state.type` is one of `material_result`, `no_material`, `blocked`, `interrupted`,
  `failed`, `clarification_only`, `evidence_gap`, or `other`.
- `terminal_state.summary` is non-empty and has at least one citation when the state is based on
  visible session evidence.
- `observed_checks` record visible checks only; they must not include verification status or
  extractor reasoning.
- Existing evidence cards, when present, match `project.json` and the session index row.
