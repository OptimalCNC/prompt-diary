# Project Synthesis

Project synthesis groups per-session evidence chains into project-level work items. Its job is to
reduce session noise while preserving the chain that makes a claim trustworthy:

```text
trigger -> agent reaction -> observed outcome or terminal state
```

This step runs from the prepared report workspace root and operates on one prepared project scope
at a time, identified by `project_key`. It uses `projects/<project_key>/project.json`,
`projects/<project_key>/sessions.index.jsonl`, and the canonical per-session evidence cards under
`projects/<project_key>/evidence/`.

## Inputs And Outputs

Inputs:

- `projects/<project_key>/project.json`
- `projects/<project_key>/sessions.index.jsonl`
- `projects/<project_key>/evidence/<session_ref>.json` files created by the MCP evidence tools
- copied session files only when a card or citation needs inspection

Outputs:

- project work items
- evidence accounting dispositions
- a project summary used by daily report synthesis

Project synthesis artifacts should stay inside the prepared report workspace and must not change
the preparation layout or the meaning of `sessions.index.jsonl`.

## Work Item Contract

A project work item is a synthesis artifact. It references committed evidence chains with full
`EvidenceChainRef` objects: `project_key`, `session_ref`, and `turn_ref`. `evidence_refs` are only
for committed evidence chains; missing evidence uses `gap_refs`. Any claim promoted to `report.md`
must use the report citation format from the [Evidence Contract](./evidence-contract.md).

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
      {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001"}
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
        {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001"}
      ]
    }
  ],
  "risks": [
    "The implementation still needs to enforce the expanded report shape."
  ],
  "evidence_refs": [
    {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001"}
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
      "turn_ref": "T0001",
      "disposition": "material_work_item",
      "work_item_ref": "W0001",
      "reason": "Primary documentation outcome for the project."
    },
    {
      "session_ref": "S0002",
      "turn_ref": "T0001",
      "disposition": "evidence_gap_item",
      "work_item_ref": "W0002",
      "reason": "The indexed turn produced no evidence chain."
    }
  ],
  "blockers": [
    {
      "summary": "What is blocked or unresolved.",
      "gap_refs": [
        {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0002", "turn_ref": "T0001"}
      ],
      "recommended_next_action": "Concrete next action if supported by evidence."
    }
  ],
  "useful_agent_driving_patterns": [
    {
      "pattern": "User supplied concrete acceptance criteria before generation.",
      "evidence_refs": [
        {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0002"}
      ],
      "why_it_worked": "It gave the agent checkable constraints."
    }
  ],
  "risks_or_antipatterns": [
    {
      "risk": "Agent claimed success without visible verification.",
      "evidence_refs": [
        {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0003", "turn_ref": "T0001"}
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

See [Project Synthesizer Prompt](./project-synthesizer-prompt.md).

## Quality Checklist

Before accepting project synthesis output, check:

- Every `evidence_refs` item resolves to a committed evidence chain.
- Every `gap_refs` item resolves to an indexed turn that has no committed evidence chain.
- Every indexed turn has exactly one evidence-accounting disposition.
- Any claim intended for `report.md` can be expanded to valid work-claim citations.
