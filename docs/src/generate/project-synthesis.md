# Project Synthesis

Project synthesis groups per-session evidence chains into project-level work items. Its job is to
reduce session noise while preserving the chain that makes a claim trustworthy:

```text
trigger -> agent reaction -> observed outcome or terminal state
```

This step operates inside one prepared project workspace at a time. It uses `project.json`,
`sessions.index.jsonl`, and the canonical per-session evidence cards under `evidence/`.

## Inputs And Outputs

Inputs:

- `project.json`
- `sessions.index.jsonl`
- `evidence/<session_ref>.json` files created by the MCP evidence tools
- copied session files only when a card or citation needs inspection

Outputs:

- project work items
- evidence accounting dispositions
- a project summary used by daily report synthesis

Project synthesis artifacts should stay inside the prepared report workspace and must not change
the preparation layout or the meaning of `sessions.index.jsonl`.

## Work Item Contract

A project work item is a synthesis artifact. It may reference evidence chains by
`session_ref` and `chain_ref`, but any claim promoted to `report.md` must use the report citation
format from the [Evidence Contract](./evidence-contract.md).

```json
{
  "schema_version": 1,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "project_label": "ReportGenerator",
  "work_item_ref": "W0001",
  "title": "Clarified report generation evidence contract",
  "kind": "material_work_item",
  "trigger": {
    "summary": "User asked for evidence-backed report generation documentation.",
    "evidence_refs": [
      {"session_ref": "S0001", "chain_ref": "E0001"}
    ]
  },
  "agent_reaction": {
    "summary": "Agent compared existing docs, identified conflicts, and updated generation contracts.",
    "main_actions": [
      "compared source docs with existing generate docs",
      "updated evidence and report contracts",
      "added synthesis guidance"
    ]
  },
  "outcomes": [
    {
      "category": "document_outcome",
      "summary": "Generation documentation was expanded while preserving the prepared-workspace model.",
      "confidence": "high"
    }
  ],
  "terminal_states": [
    {
      "type": "material_result",
      "evidence_refs": [
        {"session_ref": "S0001", "chain_ref": "E0001"}
      ]
    }
  ],
  "risks": [
    "The implementation still needs to enforce the expanded report shape."
  ],
  "evidence_refs": [
    {"session_ref": "S0001", "chain_ref": "E0001"}
  ],
  "confidence": "high"
}
```

## Project Summary

Daily report synthesis needs a compact project summary:

```json
{
  "schema_version": 1,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "project_label": "ReportGenerator",
  "progress_summary": "Evidence-backed summary of what changed in this project.",
  "work_items": ["ProjectWorkItem"],
  "evidence_accounting": [
    {
      "session_ref": "S0001",
      "chain_ref": "E0001",
      "disposition": "material_work_item",
      "work_item_ref": "W0001",
      "reason": "Primary documentation outcome for the project."
    },
    {
      "session_ref": "S0002",
      "chain_ref": null,
      "disposition": "evidence_gap_item",
      "work_item_ref": "W0002",
      "reason": "The indexed session produced no evidence card."
    }
  ],
  "blockers": [
    {
      "summary": "What is blocked or unresolved.",
      "evidence_refs": [
        {"session_ref": "S0002", "chain_ref": "E0001"}
      ],
      "recommended_next_action": "Concrete next action if supported by evidence."
    }
  ],
  "useful_agent_driving_patterns": [
    {
      "pattern": "User supplied concrete acceptance criteria before generation.",
      "evidence_refs": [
        {"session_ref": "S0001", "chain_ref": "E0002"}
      ],
      "why_it_worked": "It gave the agent checkable constraints."
    }
  ],
  "risks_or_antipatterns": [
    {
      "risk": "Agent claimed success without visible verification.",
      "evidence_refs": [
        {"session_ref": "S0003", "chain_ref": "E0001"}
      ],
      "mitigation": "Mark the result unverified and request command output or review."
    }
  ],
  "confidence": "high"
}
```

## Project Synthesizer Prompt

Prompt source: `src/prompt_diary/prompts/project-synthesizer.md` — loaded at runtime by the
orchestrator.

---

{{#include ../../../src/prompt_diary/prompts/project-synthesizer.md}}

## Quality Checklist

Before accepting project synthesis output, check:

- Evidence references resolve to existing per-session evidence cards.
- Any claim intended for `report.md` can be expanded to valid work-claim citations.
