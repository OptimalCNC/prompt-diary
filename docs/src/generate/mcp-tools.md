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

### Required Tool

The v1 MCP server must expose this tool:

| Tool | Purpose |
| --- | --- |
| `write_evidence` | Check one draft evidence chain, create or update the canonical session evidence card, and assign `chain_ref`. |

### Common Rules

MCP tools run with their process current working directory set to the prepared report workspace
root. They must not infer the target report date from hidden global state; the prepared workspace
root is the only filesystem root used by these tools.

`project_key` identifies the project directory under `projects/<project_key>`. The server verifies
it against `projects/<project_key>/project.json` before writing.

`session_ref` is the associated indexed session. It is unique only within one project, so the server
resolves it through `projects/<project_key>/sessions.index.jsonl`. The server determines the target
evidence file as `projects/<project_key>/evidence/<session_ref>.json`.

There is at most one canonical evidence card file per indexed session. The MCP server may append
multiple chains to that card, but generation must not create a separate flat `evidence_cards.jsonl`
as the source of truth. If no chain is written for an indexed session, downstream synthesis treats
that missing card as an evidence gap for the indexed session.

Normal write results should return stable references rather than filesystem paths. If a tool
explicitly documents a returned file locator for debugging or inspection, that locator must be
relative to the prepared report workspace root.

Rejected writes should be structured and actionable:

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

### `write_evidence`

Check one draft evidence chain, write it to the canonical session evidence card, and assign the
committed `chain_ref`.

Input:

```json
{
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "evidence_chain": {
    "turn": {
      "turn_ref": "T0001",
      "turn_start_line": 120,
      "turn_end_line": 170
    },
    "trigger": {
      "type": "explicit_user_message",
      "summary": "User asked to design the evidence writing operation.",
      "quoted_messages": [
        {
          "text": "Please design the evidence writing operation.",
          "citations": [
            {"lines": "120-128"}
          ]
        }
      ],
      "citations": [
        {"lines": "120-128"}
      ]
    },
    "agent_reactions": [
      {
        "summary": "Agent proposed an MCP write operation that checks and appends evidence chains.",
        "citations": [
          {"lines": "129-170"}
        ]
      }
    ],
    "outcomes": [
      {
        "category": "decision_outcome",
        "summary": "The evidence writing surface was clarified around workspace-root execution, project key, session reference, and append behavior.",
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

Write behavior:

- If the evidence file does not exist, the tool creates a canonical session evidence card from
  `projects/<project_key>/project.json` and the matching row in
  `projects/<project_key>/sessions.index.jsonl`, then appends the chain.
- If the evidence file already exists, the tool validates the existing card, assigns the next
  `chain_ref`, and appends the chain.
- Agents must not provide `chain_ref`; the tool owns deterministic chain reference allocation.
- Agents must provide the assigned `turn_ref` inside `evidence_chain.turn`; the tool validates it
  against `projects/<project_key>/sessions.index.jsonl`.
- Chain references are assigned as `E0001`, `E0002`, and so on within the card.
- `turn_ref` and `chain_ref` are separate. `turn_ref` comes from preparation and identifies the
  covered turn; `chain_ref` is assigned at write time and identifies the committed evidence chain.
- A card must not contain duplicate evidence for one `turn_ref`.
- Writes should be serialized per `(project_key, session_ref)` and committed with atomic file
  replacement so parallel extraction agents cannot corrupt a card.

Successful result:

```json
{
  "status": "appended",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "chain_ref": "E0002"
}
```

### Structural Rules

`write_evidence` must apply these rules before committing a chain:

- The current working directory is the prepared report workspace root.
- `projects/<project_key>` contains `project.json` and `sessions.index.jsonl`.
- `project_key` matches the `project_key` in `projects/<project_key>/project.json`.
- `session_ref` resolves to exactly one row in `projects/<project_key>/sessions.index.jsonl`.
- Input is one evidence chain, not a full session evidence card.
- Input does not include `chain_ref`.
- `evidence_chain.turn.turn_ref` resolves to exactly one `turns[]` item in the session index row.
- `evidence_chain.turn.turn_start_line` and `turn_end_line` match that indexed turn.
- Existing card chains do not already contain evidence for that `turn_ref`.
- Required summaries are non-empty.
- `trigger.type` is one of `explicit_user_message`, `implicit_context`, `user_correction`,
  `user_approval`, or `resume_or_continue`.
- Citation line spans are numeric, ordered, and contained by the indexed turn identified by
  `turn_ref`.
- Material outcomes cite agent reaction evidence, not only trigger evidence.
- `outcomes[*].category` is one of the controlled outcome categories in the Evidence Contract and
  is not a completion, verification, or engagement label.
- `terminal_state` is required for every evidence chain.
- Input may omit material outcomes only when `terminal_state.type` explains the non-success ending.
- `terminal_state.type` is one of `material_result`, `no_material`, `blocked`, `interrupted`,
  `failed`, `clarification_only`, `evidence_gap`, or `other`.
- `terminal_state.summary` is non-empty and has at least one citation when the state is based on
  visible session evidence.
- `observed_checks` record visible checks only; they must not include verification status or
  extractor reasoning.
- `other` outcomes include `suggested_category` and `category_rationale`.
- `terminal_state.type=other` includes `state_rationale`.
- Existing evidence cards, when present, match `project.json` and the session index row.

### Optional Tools

These tools are not required for the extractor prompt, but they can be useful for orchestration,
inspection, and debugging:

| Tool | Purpose |
| --- | --- |
| `list_projects` | Return prepared project keys and labels from the current report workspace. |
| `list_sessions` | Given `project_key`, return indexed sessions and whether each has an evidence card. |
| `read_evidence` | Given `project_key` and `session_ref`, return the current canonical evidence card if present. |
| `delete_evidence_chain` | Given `project_key`, `session_ref`, and `chain_ref`, remove one chain; intended for human/debug workflows, not normal extraction. |

Destructive tools such as `delete_evidence_chain` should be disabled by default or require an
explicit approval mode. Normal extractor agents should only need `write_evidence`.
