# Project Synthesis

Project synthesis groups one project's per-session evidence chains into a small set of project-level
**work items**. It is the noise-reduction layer between evidence extraction and daily report
synthesis. A single day can produce on the order of a hundred evidence chains across a project's
sessions; feeding them to daily synthesis raw would bury the signal. Project synthesis **groups**
related chains, **cites** them by reference, and **summarizes** them, so daily synthesis reads a
handful of work items instead of a hundred chains.

This step runs from the prepared report workspace root and operates on one prepared project scope at
a time, identified by `project_key`.

## Role: Group, Cite, Summarize

A work item is a thread-level summary node over a group of evidence chains. It never copies chain
content.

- **Group.** Collect the evidence chains that belong to the same work thread.
- **Cite, do not paste.** Reference grouped chains by `(session_ref, turn_ref)`. Never embed quoted
  messages, observed-check text, or line citations. Detail stays in the evidence cards and is reached
  by reference. The citation chain is `report.md -> work item -> evidence card -> turn_ref + lines`.
- **Summarize.** Describe the thread at a higher altitude than any single chain. A card summarizes
  one turn; a work item summarizes the whole thread.

The work item is therefore a compact index plus narrative. Daily synthesis works from these
summaries and opens evidence cards only to pull the exact lines for a claim it decides to promote.

## Inputs And Outputs

Inputs, under `projects/<project_key>/`:

- `project.json` — project identity for the work-item envelope.
- `evidence/<session_ref>.json` — the per-session evidence cards. The orchestrator trims these to
  summaries (no line citations or quoted text) and pastes them into the synthesizer prompt; the
  synthesis agent works only from that inline content and has no file access.
- `sessions.index.jsonl` — the coverage universe. The `write_work_item` tool reads it to report
  uncovered turns.

The pasted chains are grouped by session under a `#### Session <session_ref>` heading, and each chain
is labelled `<session_ref>/<turn_ref>` — `turn_ref` restarts per session and the work item references
turns as `{session_ref, turn_ref}`, so the session must be unambiguous for every chain. Each chain
keeps its trigger, reaction, outcome (with `category`), and terminal (with `type`) summaries plus
materiality; citations and quoted text are dropped.

Output:

- `projects/<project_key>/project-synthesis.json` — a work-item envelope

Project synthesis artifacts stay inside the prepared report workspace and must not change the
preparation layout or the meaning of `sessions.index.jsonl`.

## Boundary: What Project Synthesis Does Not Own

Project synthesis owns grouping and coverage only. It does not produce:

- executive or project progress summaries
- cross-project blocker prioritization
- reusable agent-driving patterns or antipatterns
- engagement verdicts
- day-level verification or evidence-quality conclusions

These belong to [Daily Report Synthesis](./daily-synthesis.md) because the signals only become
meaningful after comparing work items across every project. One weak prompt or one missing
verification in a single project may be noise, while the same pattern repeated across projects is a
real day-level lesson. Project synthesis preserves the local, evidence-backed material those
judgments need; it does not make the judgments itself.

## Grouping

Group by coherent work thread, not by session. Merge evidence chains into one work item when they
share:

- the same user goal
- the same artifact
- the same bug, blocker, or validation loop
- the same design decision
- a correction loop around the same output
- a test-fix-test sequence
- an interruption followed by a human continue or resume for the same goal

Keep chains in separate work items when they pursue unrelated goals, independent decisions, separate
blockers, different artifacts, or different project areas.

The session boundary is irrelevant in both directions:

- One thread may span several sessions, so `covered_turns` and `evidence_refs` may list turns from
  different `session_ref`s.
- One long session may contain several unrelated threads, which become several work items.

**Supporting turns fold in.** A low-value turn that fed a material thread — a clarification, an
approval, a resume — is covered inside the work item it supports, not split out.

**Trivial turns bucket.** Turns with no material outcome that support no thread — a connectivity
ping, a throwaway question — are grouped into a single `no_material_work_item` for the project rather
than producing many tiny items.

## Outcome Consolidation

A work item's `outcomes` are consolidated claims, not copies of card outcomes. Merge the card-level
outcomes that describe the same achievement into one work-item outcome, and cite the set of turns
that support it. The number of outcomes on a work item should be far smaller than the summed outcomes
of its covered chains.

Reuse the `category` already present on the evidence-card outcomes you consolidate, and the `type` on
their terminal states; do not invent new values. The controlled outcome categories and terminal-state
types are defined by the [Evidence Contract](./evidence-contract.md).

## No Prescriptions

A work item describes blocked or unfinished state through a `blocker_outcome`; it does not recommend a
next action. This boundary is local to project synthesis so it stays focused on grouping; pairing
blockers with supported next actions is the job of [Daily Report Synthesis](./daily-synthesis.md).

## Coverage Invariant

Every indexed turn is accounted for:

> Every `(session_ref, turn_ref)` in the project's `sessions.index.jsonl` appears in exactly one work
> item's `covered_turns`.

This includes material, minor, interrupted, clarification-only, failed, blocked, and trivial turns,
as well as evidence gaps. A turn that has a committed evidence chain is grouped into a normal work
item by its content. A turn that is indexed but has no committed chain — its content is unknowable to
synthesis — is collected into an `evidence_gap_item` instead. Turns intentionally left unreported,
such as duplicate evidence already represented elsewhere, go into an `excluded_with_reason` item that
records the reason. Nothing is dropped silently.

## Work Item Kinds

`kind` is the work item's coverage disposition. It is one of:

- `material_work_item` — grouped work that produced material progress.
- `no_material_work_item` — reportable low-value or negative turns with no material output, including
  the trivial-turn bucket.
- `evidence_gap_item` — accounts for indexed turns that have no extractable evidence.
- `excluded_with_reason` — turns intentionally left out of reportable work items; requires `reason`.

`kind` is deliberately small and mutually exclusive. Finer signals that can co-occur are not kinds:
an interruption is a `terminal_states[].type`, and a blocker is an `outcomes[].category` of
`blocker_outcome`. A single thread can be material, interrupted, and contain a blocker at once; daily
synthesis routes its sections off these finer fields.

`kind` is maintained as controlled values in the prompt API (`PROJECT_WORK_ITEM_KINDS`) and rendered
into the [Project Synthesizer Prompt](./project-synthesizer-prompt.md), so it has one source of
truth.

## Schema

### Envelope

```json
{
  "schema_version": 1,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "project_label": "ReportGenerator",
  "work_items": [],
  "source_user_messages": []
}
```

References inside the file are `{"session_ref": "...", "turn_ref": "..."}`. `project_key` is implied
by the envelope and re-attached by daily synthesis when it loads the file, matching how a session
evidence card carries `session_ref` once on the envelope and a bare `turn_ref` on each chain.

`work_items` are agent-authored. `source_user_messages` is **tool-populated**: `write_work_item`
fills it once, on the first write, and the synthesizer agent neither reads nor writes it — so the
[Project Synthesizer Prompt](./project-synthesizer-prompt.md) needs no change. It carries the
original user-message content per indexed turn, copied verbatim from the `text` of each extracted
chain's `trigger.quoted_messages` in `evidence/<session_ref>.json`:

```json
"source_user_messages": [
  {
    "session_ref": "S0001",
    "turn_ref": "T0001",
    "messages": ["<redacted user-authored text>"]
  }
]
```

Each turn's `messages` is a plain list of the verbatim user-message strings. It is messages-only —
content, not structure: just the text, with no line citations, `trigger_type`, `terminal_state`, or
check information, because daily synthesis reopens the card (which keeps the full `quoted_messages`
with citations) for committed structure when it needs it. The text is already secret-redacted by the
extractor; the tool copies it verbatim and does not re-redact. There is one entry per indexed turn
whose chain has at least one user message; turns with no extractable user text are simply absent,
still accounted for through `covered_turns` and the coverage invariant. Entries are ordered by
`(session_ref, turn_ref)`. This block is the user-message content substrate for daily synthesis's
engagement and team-learning readings.

### Work Item

```json
{
  "work_item_ref": "W0001",
  "kind": "material_work_item",
  "title": "Finalize and freeze the evidence-extraction contract",
  "covered_turns": [
    {"session_ref": "S0001", "turn_ref": "T0001"}
  ],
  "trigger": {
    "summary": "User drove the evidence-extraction surface to top-level turn_ref, ordered a consistency review, and finalized the design choices.",
    "evidence_refs": [
      {"session_ref": "S0001", "turn_ref": "T0001"},
      {"session_ref": "S0001", "turn_ref": "T0006"}
    ]
  },
  "agent_reaction": {
    "summary": "Migrated the contract, MCP tools, and prompt to turn_ref identity, ran review subagents, implemented the finalized choices, and froze with a commit.",
    "main_actions": ["turn_ref migration", "consistency review", "implement finalized choices", "freeze commit"]
  },
  "outcomes": [
    {
      "category": "document_outcome",
      "summary": "Evidence contract and MCP tool docs moved to top-level turn_ref; chain_ref removed.",
      "evidence_refs": [{"session_ref": "S0001", "turn_ref": "T0001"}],
      "confidence": "high"
    },
    {
      "category": "process_outcome",
      "summary": "Froze the agreed contract as a checkpoint commit.",
      "evidence_refs": [{"session_ref": "S0001", "turn_ref": "T0010"}],
      "confidence": "high"
    }
  ],
  "terminal_states": [
    {
      "type": "interrupted",
      "summary": "Prompt-test verification of the placeholder edit was interrupted; test ownership left to concurrent agents.",
      "evidence_refs": [{"session_ref": "S0001", "turn_ref": "T0008"}]
    }
  ],
  "limits": ["Prompt-test suite not confirmed green within these turns."],
  "confidence": "high"
}
```

### Fields

- `work_item_ref` — project-local handle, `W0001`, `W0002`, and so on, assigned in work-item order.
- `kind` — coverage disposition (see [Work Item Kinds](#work-item-kinds)).
- `title` — a one-line name for the thread. There is deliberately no fused work-item `summary`: the
  `trigger`, `agent_reaction`, `outcomes`, and `terminal_states` summaries are the work item's
  summary, kept separable so each stays independently citable and daily synthesis can recompose them.
- `covered_turns[]` — every turn this item accounts for, as `{session_ref, turn_ref}`. The union
  across all work items covers the session index exactly once.
- `trigger` — the earliest meaningful human trigger for the thread, as `{summary, evidence_refs}`.
  Later corrections, approvals, and resumes are summarized in `agent_reaction` and remain in
  `covered_turns`.
- `agent_reaction` — what the agent actually did across the thread, as `{summary, main_actions}`.
- `outcomes[]` — consolidated achievements, as `{category, summary, evidence_refs, confidence}`.
  `category` reuses the Evidence Contract outcome categories. A blocker is an outcome with category
  `blocker_outcome`.
- `terminal_states[]` — how the thread or its notable branches ended, as
  `{type, summary, evidence_refs}`. `type` reuses the Evidence Contract terminal-state types,
  including `interrupted`, `blocked`, and `failed`.
- `limits[]` — short honesty notes: what the thread did not verify or could not confirm.
- `reason` — required for `excluded_with_reason`; why the covered turns are not reportable, such as
  duplicate evidence already represented in another work item.
- `confidence` — `high`, `medium`, or `low` for the work item as synthesized evidence.

### Required Fields Per Kind

- All kinds: `work_item_ref`, `kind`, `title`, a non-empty `covered_turns`, and `confidence`.
- `material_work_item`: also `trigger`, `agent_reaction`, and at least one of `outcomes` or
  `terminal_states`.
- `no_material_work_item`: `trigger`, `agent_reaction`, and `outcomes` may be empty; `title` plus
  `covered_turns` carry it.
- `evidence_gap_item`: covers only turns that have no committed evidence chain; narrative fields are
  empty; `confidence` is usually `low`.
- `excluded_with_reason`: requires `reason`; narrative fields are empty.

## Project Synthesizer Prompt

This contract is developer-facing: it documents the design for repository developers and
readers. The project synthesizer agent never reads it. At runtime the agent sees only the rendered
prompt below and the workspace files it opens. Any decision in this contract that the agent must
act on has to be restated as explicit instructions in that prompt source; a cross-reference to
this contract does not reach the agent.

Prompt source: `src/prompt_diary/generate/prompts/project-synthesizer.md` — loaded at runtime by the
orchestrator.

The orchestrator runs the synthesizer in one main pass, then — if `write_work_item` still reports
uncovered turns — exactly one bounded continuation that names the remaining turns and asks the agent
to cover them (group a turn that has an evidence chain into a work item; cover one that does not with
an `evidence_gap_item`). Those continuation-only instructions live in
`src/prompt_diary/generate/prompts/project-synthesizer-next.md` (`project_synthesizer_next_prompt`);
the task fails only if turns remain uncovered after that single continuation. Because the
continuation names the turn references explicitly, it also recovers a project whose paste was empty —
every indexed turn an evidence gap.

See [Project Synthesizer Prompt](./project-synthesizer-prompt.md).

## Write Tool

Work items are committed through the `write_work_item` MCP tool, which also populates
`source_user_messages` on first write. Its input schema, validation rules, and result shape are
defined in [Project Synthesis Tools](./mcp-tools/project-synthesis.md).
