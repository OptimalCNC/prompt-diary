# Project Synthesis Tools

Project Synthesis tools are the agent-facing write path for project-level work items. The synthesis
agent submits one work item at a time. The MCP server validates it through the generation API,
appends it to the canonical `project-synthesis.json`, and returns the indexed turns still uncovered
so the agent knows when the coverage invariant is satisfied.

Shared workspace, result, and error rules are defined in [MCP Tools](./index.md). The Project
Synthesis phase contract — the work-item schema, kinds, and coverage invariant — is defined in
[Project Synthesis](../project-synthesis.md).

## Required Tool

The Project Synthesis phase requires this tool:

| Tool | Purpose |
| --- | --- |
| `write_work_item` | Check one work item, append it to `project-synthesis.json`, and report the turns still uncovered. |

## Workspace Resolution

The current working directory is the prepared report workspace root. `project_key` identifies the
project directory under `projects/<project_key>`; the tool verifies it against
`projects/<project_key>/project.json` and reads `projects/<project_key>/sessions.index.jsonl` for the
indexed-turn universe. The output is the single canonical
`projects/<project_key>/project-synthesis.json` envelope.

## `write_work_item`

Check one work item and append it to the project synthesis envelope. The work-item shape, kinds, and
required-fields-per-kind are defined in [Project Synthesis](../project-synthesis.md). The controlled
values in this schema duplicate the enum definitions in
`src/prompt_diary/generate/prompts/__init__.py` so this tool contract remains self-contained.

Input schema:

```json
{
  "project_key": "<project_key>",
  "work_item": {
    "work_item_ref": "W0001",
    "kind": "material_work_item|no_material_work_item|evidence_gap_item|excluded_with_reason",
    "title": "<non-empty string>",
    "covered_turns": [
      {"session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}
    ],
    "trigger": {
      "summary": "<string>",
      "evidence_refs": [{"session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}]
    },
    "agent_reaction": {"summary": "<string>", "main_actions": ["<string>"]},
    "outcomes": [
      {
        "category": "code_outcome|document_outcome|decision_outcome|validation_outcome|process_outcome|research_outcome|blocker_outcome|other",
        "summary": "<non-empty string>",
        "evidence_refs": [{"session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}],
        "confidence": "high|medium|low"
      }
    ],
    "terminal_states": [
      {
        "type": "material_result|no_material|blocked|interrupted|failed|clarification_only|evidence_gap|other",
        "summary": "<non-empty string>",
        "evidence_refs": [{"session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}]
      }
    ],
    "limits": ["<string>"],
    "reason": "<required only for excluded_with_reason>",
    "confidence": "high|medium|low"
  }
}
```

Write behavior:

- **First write.** If `project-synthesis.json` does not exist, the tool creates the envelope from
  `projects/<project_key>/project.json` (`schema_version`, `project_key`, `project_label`, empty
  `work_items`) and populates `source_user_messages` once: it reads every
  `projects/<project_key>/evidence/<session_ref>.json` card and copies the `text` of each chain's
  `trigger.quoted_messages` verbatim into a `messages` string list, one entry per indexed turn that
  has at least one user message, ordered by `(session_ref, turn_ref)`. Extraction is complete by this
  phase, so all cards exist and this is a single deterministic population. The tool then appends the
  submitted work item.
- **Subsequent writes.** The tool validates the existing envelope and appends the work item; it does
  not re-populate `source_user_messages`.
- `source_user_messages` is messages-only (verbatim user-message text, no line citations) — the tool
  does not re-redact (the extractor already redacted secrets). Its shape and rules are in
  [Project Synthesis](../project-synthesis.md#envelope).
- Writes are serialized per `project_key` and committed with atomic file replacement so parallel
  calls cannot corrupt the envelope.
- Rejected writes are not committed. The synthesizer corrects the work item from the returned errors
  and retries.

Successful result:

```json
{
  "status": "appended",
  "project_key": "ReportGenerator-e6ff7eeda632",
  "work_item_ref": "W0001",
  "uncovered_turns": [{"session_ref": "S0001", "turn_ref": "T0003"}]
}
```

`uncovered_turns` lists indexed turns not yet covered by any committed work item. An empty list means
the [coverage invariant](../project-synthesis.md#coverage-invariant) is satisfied and the agent
stops. This is the loop signal the [Project Synthesizer Prompt](../project-synthesizer-prompt.md)
relies on.

## Structural Rules

`write_work_item` applies these rules before committing a work item. A rejected write returns
structured, actionable `{path, message, hint}` errors per [MCP Tools](./index.md) and is not
committed.

- The current working directory is the prepared report workspace root, and
  `projects/<project_key>` contains `project.json` (whose `project_key` matches) and
  `sessions.index.jsonl`.
- `kind` is one of the controlled work-item kinds, and the
  [required fields per kind](../project-synthesis.md#required-fields-per-kind) hold. An
  `evidence_gap_item` or `excluded_with_reason` carries no narrative — `trigger`, `agent_reaction`,
  `outcomes`, and `terminal_states` must be empty or absent.
- `work_item_ref` matches `W%04d` and is unique within the envelope.
- Every `covered_turns[*]` resolves to a real indexed turn in `sessions.index.jsonl`. An
  `evidence_gap_item` covers only turns that have no committed evidence chain; every other kind
  covers only turns that have a committed chain.
- **Coverage exclusivity.** A turn already covered by a committed work item cannot be covered again,
  so every indexed turn ends in exactly one work item across all calls.
- Each `evidence_refs` turn is one of this item's `covered_turns` and has a committed evidence chain;
  a turn with no chain cannot be cited.
- `outcomes[*].category` is one of the controlled outcome categories and `terminal_states[*].type`
  is one of the controlled terminal-state types — reuse only, no new values. `confidence` is one of
  `high`, `medium`, or `low`.
- `excluded_with_reason` requires a non-empty `reason`. Required summaries are non-empty, and the
  work item contains no secrets, credentials, or unnecessary absolute paths.

## Code Placement

Per [MCP Tools](./index.md): the transport-independent API — validation, envelope IO, and
`source_user_messages` population — lives in `src/prompt_diary/generate/project_synthesis/`; the MCP
adapter lives in `src/prompt_diary/mcp/`. Validation reuses the enums in
`src/prompt_diary/generate/prompts/__init__.py` (`PROJECT_WORK_ITEM_KINDS`,
`EVIDENCE_OUTCOME_CATEGORIES`, `EVIDENCE_TERMINAL_STATES`).
