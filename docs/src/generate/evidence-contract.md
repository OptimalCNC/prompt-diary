# Evidence Contract

The evidence contract defines the evidence data model and the grounding rules for evidence
extraction. It specifies what evidence cards and chains look like, what makes a citation valid,
and what extractors must follow when producing evidence from indexed sessions.

The prepared workspace layout is defined by the [Workspace Layout](../workspace-layout.md).
This contract operates inside that workspace. Evidence files are generation artifacts written
after preparation; they do not change the preparation layout or the meaning of
`sessions.index.jsonl`.

## Session Evidence Cards

Report generation decomposes copied sessions into structured session evidence cards before
project-level or day-level synthesis.

An existing session evidence card maps one-to-one to one row in one project's
`sessions.index.jsonl`. It does not need a separate `card_id`; its stable identity is
`(project_key, session_ref)`.

`session_ref` is the report-facing handle used by citations. `source_session_id` remains source
provenance and should not replace `session_ref` in generated report citations.

The canonical storage model is multiple per-session card files, not one flat
`evidence_cards.jsonl` file. Agents write evidence through the tools on the
[MCP Tools](./mcp-tools.md) page; the MCP server validates draft evidence chains, creates or
updates canonical session evidence cards, and assigns `chain_ref` values.

Each session evidence card contains one evidence chain for each `turns[]` item in the associated
`sessions.index.jsonl` row. Each chain has a stable `chain_ref` only within that card and records
the indexed turn boundary it covers, so a chain can be identified as
`(project_key, session_ref, chain_ref)`.
`chain_ref` values should be assigned in indexed turn order: the first `turns[]` item becomes
`E0001`, the second becomes `E0002`, and so on.

Session evidence cards are stored under the project workspace:

```text
projects/<project_key>/
├── project.json
├── sessions.index.jsonl
├── sessions/
└── evidence/
    └── S0001.json
```

Example canonical card:

```json
{
  "schema_version": 1,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "session_ref": "S0001",
  "source": "codex",
  "source_session_id": "019e3aeb-f640-70c0-98f2-fd7e480a5a89",
  "session_path": "sessions/codex/rollout-2026-05-18T19-50-03-019e3aeb-f640-70c0-98f2-fd7e480a5a89.jsonl",
  "evidence_chains": [
    {
      "chain_ref": "E0001",
      "turn": {
        "turn_start_line": 45,
        "turn_end_line": 120
      },
      "trigger": {
        "type": "explicit_user_message",
        "summary": "User asked the agent to study Claude session filename conventions.",
        "quoted_messages": [
          {
            "text": "Please study how Claude session filenames are formed and compare them with our design wording.",
            "citations": [
              {"lines": "45-46"}
            ]
          }
        ],
        "citations": [
          {"lines": "45-46"}
        ]
      },
      "agent_reactions": [
        {
          "summary": "Agent inspected local Claude session paths and compared them with the current design wording.",
          "citations": [
            {"lines": "51-58"}
          ]
        }
      ],
      "outcomes": [
        {
          "category": "research_outcome",
          "summary": "Claude session naming conventions were investigated and summarized.",
          "citations": [
            {"lines": "80-120"}
          ]
        }
      ],
      "observed_checks": [],
      "terminal_state": {
        "type": "material_result",
        "summary": "The agent produced an investigation summary and did not show independent review in the extracted evidence.",
        "citations": [
          {"lines": "80-120"}
        ]
      },
      "materiality": "material",
      "uncertainties": []
    }
  ]
}
```

## Evidence Chains

An evidence chain represents one indexed turn and the agent reaction owned by that turn:

```text
turn -> trigger -> agent_reactions -> outcomes and/or terminal_state
```

Field definitions, controlled values, and extraction rules are in the evidence extractor prompt.

## Evidence Extractor Prompt

Prompt source: `src/prompt_diary/prompts/evidence-extractor.md` — loaded at runtime by the
orchestrator.

---

{{#include ../../../src/prompt_diary/prompts/evidence-extractor.md}}
