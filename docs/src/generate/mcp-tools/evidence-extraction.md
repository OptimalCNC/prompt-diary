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
create a separate flat `evidence_cards.jsonl` as the source of truth. Missing or incomplete evidence
cards block project synthesis. Valid partial cards remain available so extraction can resume only
their missing turns.

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
  "mode": "compact",
  "cursor": null
}
```

`mode` is `"compact"` (default) or `"full"`. The `mode` parameter description in the tool schema
warns that `"full"` returns raw JSONL lines and can be very large; use it only for a narrow range
where exact raw content is necessary.

Each response contains at most 32 KiB of canonical UTF-8 JSON text, including metadata and JSON
escaping. Omit `cursor` (or pass null) for the first page. If `next_cursor` is non-null, pass that
`{"line": <int>, "offset": <int>}` object back with the same project, session, range, and mode.
Continue until `next_cursor` is null. Successful responses contain only `records` and
`next_cursor`; the request already identifies the session, range, and mode. There is no separate
limit on the number of requested lines. Known metadata can be omitted, so record counts and gaps
between returned line numbers do not indicate unread evidence. An all-metadata range returns
empty records and a null cursor.

### Compact return shape

Compact mode returns structured records with absolute physical line numbers. A page contains as
many whole records as fit; a record larger than a page is returned in lossless fragments:

```json
{
  "records": [
    {
      "line": 27,
      "kind": "tool_result",
      "tool_results": [
        {
          "kind": "command",
          "command": "pytest",
          "exit_code": 0,
          "preview": "12 passed"
        }
      ]
    }
  ],
  "next_cursor": {"line": 28, "offset": 0}
}
```

Compact record fields are sparse: absent values, empty collections, and default flags are omitted.

| Field | Type | Description |
| --- | --- | --- |
| `line` | int | Absolute 1-based physical line number. |
| `kind` | str | Evidence kind, such as `user`, `assistant`, `tool_call`, `tool_result`, `terminal`, or `unknown`. |
| `text` | str | Exact genuine message text, or an explicitly bounded fallback for other content. |
| `tool_uses` | list | Invocations with `name`, optional `input_summary`, and `truncated` when trimmed. |
| `tool_results` | list | Results with `kind` and available evidence: `status`, `command`, `exit_code`, `file_path`, `changes`, `name`, `preview`, `stderr`, `error`, and `truncated`. File changes carry `path`, `operation`, and available `move_path` and bounded diff/content `preview`. |
| `truncated` | bool | Present as `true` when record text was trimmed. |
| `duplicate_of` | int | Physical line of the canonical message for a confirmed Codex echo. |
| `source_type` | str | Original record type for a fallback whose shape is not normalized. |
| `unavailable` | list[str] | Unknown or unavailable content blocks requiring raw inspection if relevant. |

Command exit codes preserve zero. Completion status alone does not prove command success. Modern
Codex custom tool outputs and completed command, file-change, and MCP events are normalized before
metadata is removed. Physical line numbers locate the unchanged copied source; per-line hashes,
byte counts, correlation IDs, and normalization metadata are not retained in compact records.

Confirmed Codex message echoes return a physical line and `duplicate_of` instead of repeated text.
Matching requires adjacent records, identical role and complete text, the source's message/echo
ordering, and timestamps separated by at most 100 ms in logging order. This includes known modern
completed-message events. Repeated messages without this proof remain separate. The canonical
line is independent of the requested range. Full mode returns both original raw records.

### Compact trimming policy

Compaction first normalizes evidence, then removes known scaffolding and redundant metadata:

- **Tool results larger than 1 KiB** retain a head preview (about 320 bytes) and tail preview
  (about 160 bytes), joined by an elision marker and marked `truncated: true`. Tool inputs use
  bounded previews too.
- **Known metadata and source context** are omitted: token accounting, reasoning, settings,
  environment/bootstrap instructions, and recognized lifecycle metadata. These omissions do not
  create records or extra local audit files.
- **Failures, interruptions, and parent-visible child results** remain observable. Unknown,
  malformed, and unavailable content remains identifiable for a narrow full-mode read.

Genuine user and assistant text stays exact, including Claude string and text-block messages.
Large genuine messages are paginated rather than shortened. Small tool results remain intact.
Use `mode="full"` on specific lines when a trimmed result, attachment, or unknown shape leaves an
evidence gap. Full mode reads the unchanged source copy.

### Full return shape

Full mode returns verbatim raw JSONL lines within the same page budget. Large lines require
multiple pages.

```json
{
  "records": [
    {
      "line": 27,
      "raw_line": "{\"type\":\"event_msg\"}"
    }
  ],
  "next_cursor": null
}
```

Full records contain only `line` (int) and `raw_line` (str).

In either mode, an oversized record uses this fragment shape in `records`:

| Field | Type | Description |
| --- | --- | --- |
| `line` | int | Physical line of the original source record. |
| `record_format` | `compact_json` \| `raw_line` | The fragment reconstructs a serialized compact record or the original raw line. |
| `offset` | int | Zero-based Unicode-character offset in that content. |
| `total_chars` | int | Character count of the complete content. |
| `content` | str | This page's contiguous content fragment. |

Concatenate fragments of the same physical line in offset order. For `compact_json`, parse the
completed text as JSON to recover the usual compact record; for `raw_line`, the completed text is
the original line. A fragment is not a complete record. Pagination never changes citation line
numbers, and the final fragment may share a page with subsequent whole records.

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
or a cursor outside the range or record content. Error text is also bounded by the page budget.

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
