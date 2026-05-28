# Evidence Extraction Tools

Evidence extraction tools are the primary agent-facing write path for extracted session evidence.
Agents submit one draft evidence chain at a time. The MCP server validates the draft through the
generation API, creates or updates the canonical session evidence card, and commits the write.

Shared workspace, result, and error rules are defined in [MCP Tools](./index.md).
The evidence data model is defined by the [Evidence Contract](../evidence-contract.md).

## Required Tool

The Evidence Extraction phase requires this tool:

| Tool | Purpose |
| --- | --- |
| `write_evidence` | Check one draft evidence chain and create or update the canonical session evidence card. |

## Workspace Resolution

`project_key` identifies the project directory under `projects/<project_key>`. The tool verifies
it against `projects/<project_key>/project.json` before writing.

`session_ref` is the associated indexed session. It is unique only within one project, so the tool
resolves it through `projects/<project_key>/sessions.index.jsonl`. The tool determines the target
evidence file as `projects/<project_key>/evidence/<session_ref>.json`.

There is at most one canonical evidence card file per indexed session. The tool may append
multiple chains to that card, but generation must not create a separate flat `evidence_cards.jsonl`
as the source of truth. If no chain is written for an indexed session, downstream synthesis treats
that missing card as an evidence gap for the indexed session.

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
