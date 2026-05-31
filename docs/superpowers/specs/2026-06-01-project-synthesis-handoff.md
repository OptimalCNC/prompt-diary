# Handoff Plan — Project Synthesis docs

**For:** the agent working on the project-synthesis docs.
**Files in scope:** `docs/src/generate/project-synthesis.md`, `docs/src/generate/mcp-tools/project-synthesis.md`.
**Why now:** daily synthesis was just changed to consume a `source_user_messages` block from
`project-synthesis.json`, and the `write_work_item` tool must produce it. These two files own that
contract.

**Context — decisions this plan encodes:**

- Project synthesis stays a noise-reduction layer (group / cite / summarize). The original
  *user-message content* is brought back for the engagement and team-learning readings — but
  **deterministically, by the MCP tool**, not by the agent, and as **content only** (no structural
  classification carried up).
- Verification is **out of MVP** (trust extraction + project synthesis for now); this plan adds no
  verification signal to work items.
- "No Prescriptions" is **project-synthesis-local**; daily synthesis owns next actions.

Already-applied sibling edits (do not redo, must stay consistent): `product.md` principle 6,
`docs/src/generate/daily-synthesis.md`, and `src/prompt_diary/generate/prompts/daily-synthesizer.md`.

---

## 0. Cross-file invariant (must match the already-edited daily side)

`daily-synthesis.md` now documents `project-synthesis.json` as carrying agent-authored `work_items`
**plus** a tool-populated `source_user_messages` block: "original user-message text and citations,
per indexed turn." Your edits must match this exact shape:

```json
"source_user_messages": [
  {
    "session_ref": "S0001",
    "turn_ref": "T0001",
    "quoted_messages": [
      {"text": "<redacted user-authored text>", "citations": [{"lines": "45-46"}]}
    ]
  }
]
```

Properties to preserve:

- **Messages-only** — content, not structure. Do **not** add `trigger_type`, `terminal_state`, or
  check info here; that was deliberately decided against (daily synthesis reopens the card for
  committed structure when it needs it).
- **Tool-populated, never agent-authored.** The synthesizer agent does not write it.
- Copied **verbatim** from each extracted chain's `trigger.quoted_messages` (text + citations) in
  `evidence/<session_ref>.json`. Already secret-redacted by the extractor — the tool does not
  re-redact or recompute citations.
- One entry per indexed turn whose chain has ≥1 quoted message; turns with no extractable user text
  are simply absent (still accounted for via `covered_turns` / the coverage invariant). State this
  rule explicitly.
- Deterministic order by `(session_ref, turn_ref)`.

---

## 1. `project-synthesis.md`

**1a. Add `source_user_messages` to the envelope.** In the `### Envelope` section, add the block as
a sibling to `work_items`, and add a short paragraph stating it is tool-populated (by
`write_work_item` on first write), messages-only, copied verbatim from the cards, and is the
user-message content substrate for daily synthesis's engagement/team-learning reading. Make clear
the synthesizer agent neither reads nor writes it — so the existing `project-synthesizer.md` prompt
needs **no** change (call this out so it isn't edited).

**1b. Clarify "No Prescriptions" as project-local.** The current section reads product-wide
("predicting how an unfinished session should continue is out of scope — the user resumes their own
session"). This now conflicts with daily synthesis owning "Blockers and Next Actions." Reword so the
boundary is explicitly **local to project synthesis** (keeps it focused on grouping), and point to
`daily-synthesis.md` as the owner of supported next actions. Suggested closing sentence:

> This boundary is local to project synthesis so it stays focused on grouping; pairing blockers with
> supported next actions is the job of [Daily Report Synthesis](./daily-synthesis.md).

---

## 2. `mcp-tools/project-synthesis.md` — spec the `write_work_item` contract

Expand the current stub into a full contract, mirroring the structure/quality of
`evidence-extraction.md`.

**Input:** `{ "project_key": "<key>", "work_item": { …Work Item per project-synthesis.md schema… } }`

**Workspace resolution:** CWD = workspace root; verify `projects/<project_key>/project.json`
(matches `project_key`) and `sessions.index.jsonl`; output is
`projects/<project_key>/project-synthesis.json`.

**Write behavior:**

- **First call:** create the envelope from `project.json` (`schema_version`, `project_key`,
  `project_label`, empty `work_items`) **and** populate `source_user_messages` deterministically by
  reading every `evidence/<session_ref>.json` card and copying each chain's `trigger.quoted_messages`
  per §0. (Extraction is complete by this phase, so all cards exist — a single deterministic
  population.) Then append the submitted work item.
- **Subsequent calls:** validate the existing envelope; append the work item; do **not** re-populate
  `source_user_messages`.
- Serialize per `project_key`; atomic file replacement (parallel safety) — mirror `write_evidence`.

**Validation (reject, don't commit, with structured `{path, message, hint}` errors per
`mcp-tools/index.md`):**

- `kind` ∈ `PROJECT_WORK_ITEM_KINDS`; required-fields-per-kind hold (from project-synthesis.md).
- `work_item_ref` matches `W%04d`, unique in the file.
- Every `covered_turns[*]` resolves to a real indexed turn in `sessions.index.jsonl`;
  `evidence_status` ∈ `PROJECT_EVIDENCE_STATUS_VALUES` and is consistent with whether a committed
  chain exists (`extracted` ↔ chain present, `gap` ↔ absent).
- **Coverage exclusivity across calls:** a turn already covered by a committed work item cannot be
  covered again (every turn in exactly one work item).
- Each `evidence_refs` turn is an `extracted` turn present in this item's `covered_turns` (cannot
  cite a `gap` turn or a turn not covered here).
- `outcomes[*].category` ∈ `EVIDENCE_OUTCOME_CATEGORIES`; `terminal_states[*].type` ∈
  `EVIDENCE_TERMINAL_STATES` (reuse-only — no new values); `confidence` ∈ {high, medium, low}.
- `excluded_with_reason` requires `reason`; required summaries non-empty; no secrets/credentials/
  absolute paths.

**Success result:**

```json
{
  "status": "appended",
  "project_key": "...",
  "work_item_ref": "W0001",
  "uncovered_turns": [{"session_ref": "S0001", "turn_ref": "T0003"}]
}
```

`uncovered_turns` = indexed turns not yet in any committed work item; empty = coverage invariant
satisfied (agent stops). This is the loop signal the `project-synthesizer.md` prompt already relies
on.

**Placement** (per `mcp-tools/index.md`): API / validation / IO in
`src/prompt_diary/generate/project_synthesis/`; MCP adapter in `src/prompt_diary/mcp/`. Reuse the
enums from `prompts/__init__.py` (`PROJECT_WORK_ITEM_KINDS`, `PROJECT_EVIDENCE_STATUS_VALUES`,
`EVIDENCE_OUTCOME_CATEGORIES`, `EVIDENCE_TERMINAL_STATES`).

---

## 3. Consistency checks before done

- `source_user_messages` shape identical in `project-synthesis.md` and the daily-side wording (§0).
- `project-synthesizer.md` prompt left unchanged (the tool owns the block).
- Validation reuses existing enums; introduces no new controlled values.
