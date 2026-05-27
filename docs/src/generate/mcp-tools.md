# MCP Tools

The Prompt Diary MCP server exposes agent-facing tools used during report generation. It is a thin
wrapper over local validation and canonical write logic. It operates on prepared workspaces and
must not rely on hidden global report-date state.

This page groups MCP tools by generation phase. The evidence data model is defined by the
[Evidence Contract](./evidence-contract.md).

Implementation status: the current package MCP server is a boilerplate stdio server and exposes
only `prompt_diary_ping` for connectivity checks. The evidence-writing tools below are the future
generation contract and are not implemented yet.

## Evidence Tools

Evidence tools are the primary agent-facing write surface for extracted session evidence.
Agents submit one draft evidence chain at a time. The MCP server owns validation, canonical
per-session evidence card creation, chain reference allocation, and atomic writes.

### Required Tools

The v1 MCP server must expose these tools:

| Tool | Purpose |
| --- | --- |
| `resolve_session` | Resolve a project `working_dir` and `session_ref` into the indexed session metadata an extractor needs. |
| `validate_evidence` | Validate one draft evidence chain without writing it. |
| `write_evidence` | Validate one draft evidence chain, create or update the canonical session evidence card, and assign `chain_ref`. |

### Common Rules

`working_dir` is the project workspace directory containing `project.json` and
`sessions.index.jsonl`. It is not the report workspace root, because `session_ref` is unique only
within one project. MCP tools must not infer the target report date; `working_dir` is the only
location input used to find project metadata and evidence outputs.

`session_ref` is the associated indexed session. The server determines the target evidence file as
`<working_dir>/evidence/<session_ref>.json`.

There is at most one canonical evidence card file per indexed session. The MCP server may append
multiple chains to that card, but generation must not create a separate flat `evidence_cards.jsonl`
as the source of truth. If no identifiable trigger-centered chain is found, no evidence card is
created; downstream synthesis treats that missing card as an evidence gap for the indexed session.

All paths returned by MCP tools should be relative to `working_dir` unless explicitly documented
otherwise.

Validation failures should be structured and actionable:

```json
{
  "status": "invalid",
  "errors": [
    {
      "path": "evidence_chain.outcomes[0].citations[0].lines",
      "message": "line span 240-245 is outside target span 42-239",
      "hint": "cite only lines inside the indexed session target span"
    }
  ]
}
```

### `resolve_session`

Resolve one indexed session from a project workspace.

Input:

```json
{
  "working_dir": ".reports/work/2026-05-19/projects/ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001"
}
```

Successful result:

```json
{
  "status": "resolved",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "project_label": "ReportGenerator",
  "session_ref": "S0001",
  "source": "codex",
  "source_session_id": "019e3aeb-f640-70c0-98f2-fd7e480a5a89",
  "session_path": "sessions/codex/rollout-2026-05-18T19-50-03-019e3aeb-f640-70c0-98f2-fd7e480a5a89.jsonl",
  "target_span": {
    "start_line": 42,
    "end_line": 239
  },
  "evidence_path": "evidence/S0001.json",
  "existing_chain_refs": ["E0001"]
}
```

### `validate_evidence`

Validate one draft evidence chain without writing it. This tool uses the same input shape and
validation rules as `write_evidence`, but it never writes files and never assigns a final
`chain_ref`.

Input:

```json
{
  "working_dir": ".reports/work/2026-05-19/projects/ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "evidence_chain": {
    "trigger": {
      "type": "explicit_user_message",
      "summary": "User asked to design the evidence writing operation.",
      "citations": [
        {"lines": "120-128"}
      ]
    },
    "agent_reactions": [
      {
        "summary": "Agent proposed an MCP write operation that validates and appends evidence chains.",
        "citations": [
          {"lines": "129-170"}
        ]
      }
    ],
    "outcomes": [
      {
        "category": "decision_outcome",
        "summary": "The evidence writing surface was clarified around project working directory, session reference, and append behavior.",
        "citations": [
          {"lines": "129-170"}
        ]
      }
    ],
    "observed_checks": [],
    "terminal_state": {
      "type": "material_result",
      "summary": "The chain produced a design decision but did not include implementation or independent review evidence.",
      "citations": [
        {"lines": "129-170"}
      ]
    },
    "materiality": "material",
    "uncertainties": []
  }
}
```

Successful result:

```json
{
  "status": "valid",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "next_chain_ref": "E0002"
}
```

`next_chain_ref` is advisory because another writer may append before `write_evidence` runs. The
`chain_ref` returned by `write_evidence` is the committed reference.

### `write_evidence`

Validate one draft evidence chain, write it to the canonical session evidence card, and assign the
committed `chain_ref`.

Input uses the same shape as `validate_evidence`.

Write behavior:

- If the evidence file does not exist, the tool creates a canonical session evidence card from
  `project.json` and the matching row in `sessions.index.jsonl`, then appends the chain.
- If the evidence file already exists, the tool validates the existing card, assigns the next
  `chain_ref`, and appends the chain.
- Agents must not provide `chain_ref`; the tool owns deterministic chain reference allocation.
- Chain references are assigned as `E0001`, `E0002`, and so on within the card.
- Writes should be serialized per `(working_dir, session_ref)` and committed with atomic file
  replacement so parallel extraction agents cannot corrupt a card.

Successful result:

```json
{
  "status": "appended",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "chain_ref": "E0002",
  "evidence_path": "evidence/S0001.json"
}
```

### Validation Rules

`validate_evidence` and `write_evidence` must apply the same validation rules:

- `working_dir` contains `project.json` and `sessions.index.jsonl`.
- `session_ref` resolves to exactly one row in `sessions.index.jsonl`.
- Input is one evidence chain, not a full session evidence card.
- Input does not include `chain_ref`.
- Required summaries are non-empty.
- `trigger.type` is one of `explicit_user_message`, `implicit_context`, `user_correction`,
  `user_approval`, or `resume_or_continue`.
- Citation line spans are numeric, ordered, and contained by the indexed target span.
- Material outcomes cite agent reaction evidence, not only trigger evidence.
- `outcomes[*].category` is one of the controlled outcome categories in the Evidence Contract and
  is not a completion, verification, or engagement label.
- `terminal_state` is required for every evidence chain.
- Input may omit material outcomes only when `terminal_state.type` explains the non-success ending.
- `terminal_state.type` is one of `material_result`, `no_material`, `blocked`, `interrupted`,
  `failed`, `clarification_only`, `evidence_gap`, or `other`.
- `terminal_state.summary` is non-empty and has at least one citation when the state is based on
  visible session evidence.
- `observed_checks` record visible checks only; they must not include verification status or audit
  reasoning.
- `other` outcomes include `suggested_category` and `category_rationale`.
- `terminal_state.type=other` includes `state_rationale`.
- Existing evidence cards, when present, match `project.json` and the session index row.

### Optional Tools

These tools are not required for the evidence contract, but they are useful for orchestration,
inspection, and debugging:

| Tool | Purpose |
| --- | --- |
| `list_projects` | Given a report workspace directory, return project workspace directories and labels. |
| `list_sessions` | Given a project `working_dir`, return indexed sessions and whether each has an evidence card. |
| `read_evidence` | Given `working_dir` and `session_ref`, return the current canonical evidence card if present. |
| `delete_evidence_chain` | Remove one chain by `chain_ref`; intended for human/debug workflows, not normal extraction. |

Destructive tools such as `delete_evidence_chain` should be disabled by default or require an
explicit approval mode. Normal extractor agents should only need `resolve_session`,
`validate_evidence`, and `write_evidence`.
