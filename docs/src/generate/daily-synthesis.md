# Daily Report Synthesis

Daily report synthesis is the final report-producing generation phase. It turns project work items
into a semantic daily report model, `daily-report.json`, where the three
[product purposes](../product.md#purposes) must converge from one evidence base: work
communication, engagement review, and team learning — each honest about its evidence. Reader-facing views —
`report.md` and any future view — are rendered from that model by a deterministic step; the
synthesizer that builds the model is view-agnostic.

Daily report synthesis starts from the prepared workspace and generation artifacts. It must not
rediscover raw sessions outside the prepared workspace.

## Inputs And Outputs

Inputs:

- `metadata.json`
- `projects/*/project.json`
- `projects/*/sessions.index.jsonl`
- per-session evidence cards under `projects/*/evidence/`
- project synthesis outputs in `projects/*/project-synthesis.json`: the agent-authored work items
  and the tool-populated `source_user_messages` block (verbatim user-message text per indexed turn;
  reopen the evidence card for line citations)

Outputs:

- `daily-report.json` in the prepared workspace root — built by the synthesizer agent
- `report.md` in the prepared workspace root — rendered from `daily-report.json`

`daily-report.json` is the authoritative report artifact and the synthesizer agent's only output.
`report.md` is a deterministic view rendered from that model (see [Rendering](#rendering)). The
phase returns both, but the responsibilities are separate: synthesis builds the model, rendering
projects it into views. A model that misses required fields, uses invalid citations, hides required
evidence-quality limits, or includes forbidden high-risk content is a synthesis bug; a view that
adds, drops, or alters a claim relative to the model is a rendering bug.

## Report Contract

Daily report synthesis owns the daily report data model — the content of `daily-report.json` — from
which the reader-facing views in [Rendering](#rendering) are produced. Its shape is set by the
abstract layout: the union of every block's `needs` is what `daily-report.json` must carry, and the
[Field Provenance](#field-provenance) tables below record which of those fields are AI-`synthesize`d
versus deterministically built.

The concrete `daily-report.json` schema is **frozen** below — it is the union of the abstract
layout's `needs`. `synthesize` fields (see [Field Provenance](#field-provenance)) are written by the
agent passes; every other field is built deterministically by code. The phase writes one
`daily-report.json`: code lays down the deterministic skeleton with the three `synthesize` slots set
to `null`, each pass patches its own slot through its validating tool, and a finalize step fills
`overall_confidence` and validates the whole document (see [AI Synthesis Workflow](#ai-synthesis-workflow)).

Citations are stored **resolved** as `{project_key, session_ref, turn_ref, lines}`, where `lines` is
the cited indexed turn's line range (for example `"2-8"`); the report citation format `S0001:2-8` is
`session_ref:lines`, scoped to its project. Session refs are assigned per project, so every stored
citation carries `project_key` to stay unambiguous across projects. The per-project summary pass
submits `{session_ref, turn_ref}` (its project is the tool argument); the cross-project engagement
and team-learning passes submit `{project_key, session_ref, turn_ref}`. The tools resolve every
citation to its line range via the session index and reject any turn that is not a committed
(evidence-bearing) turn of its project — a turn covered only by an evidence-gap item carries no
evidence and cannot ground a claim.

```json
{
  "schema_version": 1,
  "report_date": "2026-05-28",
  "status": "final",
  "window": {"start": "2026-05-28T00:00:00+08:00", "end": "2026-05-29T00:00:00+08:00", "timezone": "Asia/Shanghai"},
  "overall_confidence": "high",
  "executive_summary": {
    "top_outcomes": [{"text": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}]}],
    "open_items": [{"text": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0002", "lines": "9-12"}]}]
  },
  "projects": [{
    "project_key": "ReportGenerator-e6ff7eeda632",
    "project_label": "ReportGenerator",
    "summary": {"text": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}]},
    "work_items": [{
      "work_item_ref": "W0001",
      "title": "…",
      "kind": "material_work_item",
      "disposition": "completed",
      "confidence": "high",
      "covered_turns": [{"session_ref": "S0001", "turn_ref": "T0001"}],
      "trigger_summary": "…",
      "agent_reaction_summary": "…",
      "outcomes": [{"what_changed": "…", "confidence": "high", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}]}],
      "terminal_states": [{"summary": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}]}],
      "limits": ["…"]
    }],
    "source_user_messages": [{"session_ref": "S0001", "turn_ref": "T0001", "messages": ["…"]}]
  }],
  "engagement_assessment": {
    "overall_reading": {"text": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}], "confidence": "medium"},
    "observations": [{"dimension": "direction", "statement": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0001", "lines": "2-8"}], "confidence": "medium"}],
    "limits": ["…"]
  },
  "team_learning": {
    "takeaways": {"text": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0002", "turn_ref": "T0001", "lines": "2-6"}], "confidence": "low"},
    "patterns": [{"kind": "reuse", "statement": "…", "rationale": "…", "recurrence": "…", "citations": [{"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0002", "turn_ref": "T0001", "lines": "2-6"}], "confidence": "low"}],
    "limits": ["…"]
  }
}
```

Field shapes follow the [Field Provenance](#field-provenance) tables. Notes on the schema:

- `summary` (per project), `engagement_assessment`, and `team_learning` are `null` in the skeleton
  and filled by their passes. Finalize requires `summary` non-null for any project with work items,
  and requires `engagement_assessment` / `team_learning` non-null when the report has any work item;
  an empty report (no work items) leaves the judgment sections `null`, and they render as
  `Empty(fallback)`.
- `disposition` is set only for `material_work_item`s (one of `completed` / `blocked` / `interrupted`
  / `failed` / `clarification`); minor kinds (`no_material_work_item`, `evidence_gap_item`,
  `excluded_with_reason`) carry `null` and fold into "Minor activity".
- `terminal_states[]` carries `{summary, citations}` (citations resolved from the work item's
  `terminal_states[].evidence_refs`, like `outcomes[]`). A material work item with no `outcomes`
  shows its terminal disposition as the visible claim in place of the outcomes, so each such terminal
  state must be cited; finalize rejects a no-outcome material item whose rendered terminal claim is
  uncited.
- `covered_turns` is lifted onto each work item so rendering can join the project-level
  `source_user_messages` to the work item's "User messages" toggle.
- The per-project `summary` carries `text` + `citations` only — its confidence is implicit in the
  work items it rolls up, each of which shows its own `confidence`. `overall_reading` and `takeaways`
  carry their own `confidence` because they are standalone judgments.
- `overall_confidence` is `high` / `medium` / `low` for a report with work items; for an empty report
  (no work items, judgment sections `null`) it is `null` — there are no per-claim confidences to roll
  up — and the header renders it as not applicable.
- The passes are idempotent on a single `daily-report.json`: each tool does an atomic
  read-modify-write that replaces its own slot (re-running a pass overwrites, never duplicates), and
  finalize recomputes `overall_confidence` from the current slots on every run, so a re-run never
  leaves a stale roll-up.

### Field Provenance

Every model field is produced one of four ways. Only `synthesize` fields require the daily
synthesizer agent; `lift` / `derive` / `resolve` are deterministic and should be built by code, which
also guarantees they cannot drift from the evidence they came from.

- **lift** — copied verbatim from an upstream artifact (a work item, `source_user_messages`); no transformation.
- **derive** — computed deterministically from upstream fields.
- **resolve** — looked up deterministically, such as a turn reference to its line range via the session index.
- **synthesize** — newly written by the agent; the only AI-produced fields.

These tables capture, per lens, which fields are AI-`synthesize`d versus deterministically built, and
mirror each block's `needs`. The mechanism that produces and enforces this split is settled with the
AI synthesis workflow.

**Work by Project**

| Field | Source | Provenance |
| --- | --- | --- |
| `project_label` | `project.json` | lift |
| work item `title` | `work_items[].title` | lift |
| `Why` (trigger / agent reaction) | `trigger.summary`, `agent_reaction.summary` | lift |
| outcome `what changed` | `outcomes[].summary` | lift |
| terminal `summary` (no-outcome fallback claim) | `terminal_states[].summary` | lift |
| `confidence` | `work_items[].confidence`, `outcomes[].confidence` | lift |
| `User messages` | `source_user_messages` (tool-populated) | lift |
| `disposition` | `terminal_states` + `outcomes` | derive |
| ordering · material/Minor split | `kind` + sort rule | derive |
| `Citation` | `outcomes[]` / `terminal_states[]` `evidence_refs` → lines via the session index | resolve |
| project `summary` | the project's work items | **synthesize** |

**Engagement Assessment**

| Field | Source | Provenance |
| --- | --- | --- |
| `Citation` | observation `citations` → lines via the session index | resolve |
| observation `dimension` | classified by the agent (direction / review / correction / recovery) | **synthesize** |
| observation `statement` | the work item's messages + reaction / outcome context | **synthesize** |
| `confidence` | the agent's per-observation judgment | **synthesize** |
| `overall_reading` | the engagement observations | **synthesize** |
| `limits` | named by the agent + standing offline / work-item-grain limits | **synthesize** |

This is the judgment lens: its output fields are `synthesize`, grounded by mandatory `Citation`s. The
substrate it reads — the work item's `trigger` / `agent_reaction` / `outcomes` / `terminal_states` and
its `source_user_messages` — is lifted/resolved input, not output fields.

**Team Learning**

| Field | Source | Provenance |
| --- | --- | --- |
| `Citation` | pattern `citations` → lines via the session index | resolve |
| pattern `kind` | classified by the agent (promote / avoid / reuse) | **synthesize** |
| pattern `statement` / `rationale` | the work item arc + `source_user_messages`, read in context | **synthesize** |
| `recurrence` | occurrences across work items (countable seed; the agent states it) | **synthesize** |
| `confidence` | the agent's per-pattern judgment | **synthesize** |
| `takeaways` | the patterns | **synthesize** |
| `limits` | named by the agent + standing single-day / proxy-metric limits | **synthesize** |

Another `synthesize`-heavy judgment lens, grounded by mandatory `Citation`s and seeded deterministically
by `process_outcome` (reuse) and repeated `failed` / `blocked` terminal states (avoid).

The Executive Summary carries no synthesized fields — it is projected deterministically (select by
significance, lift text, resolve citations; see [AI Synthesis Workflow](#ai-synthesis-workflow)). Evidence-quality signals (confidence, limits, citations) are not a section of their own —
they render inline on each claim, so their provenance lives with whichever section carries them.

## Rendering

Rendering turns `daily-report.json` into reader-facing views through an intermediate,
engine-independent **abstract layout**:

```text
daily-report.json   →   abstract layout   →   { report.md, Notion, … }
 (semantic model)        (presentation tree)     (engine adapters)
```

The abstract layout is the single source of truth for the report's *structure* — its sections,
their order, and the blocks inside them — written without any engine's syntax. Each engine renderer
walks the layout and serializes its blocks into that engine's constructs, degrading gracefully where
an engine lacks one. Rendering stays deterministic and adds no judgment: every claim, citation,
confidence value, and evidence-quality signal in a view comes from the model through the layout. A
view that reads sessions, evidence cards, or work items, or introduces content absent from the
model, is a rendering bug. Because rendering is deterministic, the "no new claims" guarantee is
structural, not a rule the synthesizer must remember.

Each block also declares the model data it consumes (`needs:`). Those needs are the layout's claim
on the contract — the union of every `needs` is what `daily-report.json` must carry — so settling
the layout settles the model, and it is the living structure this page tracks. Each field's
provenance — `lift` / `derive` / `resolve` / `synthesize` — is recorded in
[Field Provenance](#field-provenance); only `synthesize` fields need the agent.

### Abstract Layout

Blocks (engine-independent presentation primitives):

- `Document(title, properties)` — the report root; `properties` are key/value metadata.
- `Section(title)` — a titled, ordered region with a stated purpose; may nest.
- `Group(label)` — a labeled cluster of blocks repeated over a collection, such as one per project.
- `Prose(text, citation?)` — a run of rich text, optionally carrying an inline citation.
- `List(bullet|number)` — a sequence of items, each prose or nested blocks.
- `Table(columns, rows, affordances)` — tabular data; `affordances` declare the default sort,
  group-by, and filter-by keys. Rows bind to a model collection.
- `Tag(value, scale)` — one controlled value from a named scale (materiality, disposition,
  confidence, type); the key that filtering and sorting use.
- `Citation(refs)` — one or more evidence references resolving to `{session, lines}`.
- `Callout(tone)` — set-apart emphasis for limits, warnings, or gaps.
- `Toggle(label)` — a collapsible region, collapsed by default; reveals its children on demand.
- `Empty(fallback)` — explicit empty-state when a section's data is absent.

Layout (all sections below are designed):

```text
Document  "Prompt Diary Report — {report_date}"
  properties: status{final|partial} · window{start–end, tz} · overall_confidence{high|medium|low}
  needs: report_date, status, window, overall_confidence

Section "Executive Summary" — the 30-second digest: what got done and what's open
  List(bullet)  top outcomes (curated across projects)     — Prose · Citation
  List(bullet)  headline open items (unfinished / blocked) — Prose · Citation
      needs: executive_summary → { top_outcomes[] → {text, citations},
                                   open_items[]   → {text, citations} }

Section "Work by Project" — the day's outcomes, grouped by project then work item
  Group per project (ordered by significance)
    Prose   project summary — produced / finished / in-progress (qualitative) · Citation(work items)
    List of work items (material first):
      Prose    {work item title}              · Tag(disposition) · Tag(confidence)
      Toggle "Why" (folded)                   — trigger.summary (+ agent_reaction) · Citation
      Toggle "User messages" (folded)         — verbatim source_user_messages for the work item's turns · Citation
      List of outcomes — what changed · Tag(confidence) · Citation
      Callout(limit) (only if any) — what this work item did not verify or confirm · work_items[].limits
      (a work item with no material outcome shows its terminal disposition in place of the outcomes)
    Toggle "Minor activity" (folded)          — the project's no-material / trivial work items
    needs: projects[] → { project_label, summary → {text, citations}, work_items[] → { title, kind,
           disposition, confidence, trigger.summary, agent_reaction.summary,
           outcomes[] → {what_changed, confidence, citations},
           terminal_states[] → {summary, citations}, limits[] } }
           + source_user_messages by covered_turn → verbatim {messages} per (session_ref, turn_ref)

Section "Engagement Assessment" — a per-person, cited reading of how the user directed, reviewed, corrected, and resumed the work; judged from their messages, not volume, and never a score
  Prose   overall reading — a short qualitative judgment of how substantively the user's messages
          steered the day's work, grounded in the observations below and explicit about limits · synthesize · Citation
  Group "Direction"  (only if any)  — framing, goals, supplied context, acceptance criteria
    List(bullet)  {observation}                              · Tag(confidence) · Citation
  Group "Review"     (only if any)  — checking a result before moving on (approval, feedback)
    List(bullet)  {observation}                              · Tag(confidence) · Citation
  Group "Correction" (only if any)  — redirecting the agent after a wrong or failed attempt
    List(bullet)  {observation}                              · Tag(confidence) · Citation
  Group "Recovery"   (only if any)  — resuming stalled, interrupted, or blocked work
    List(bullet)  {observation}                              · Tag(confidence) · Citation
  Callout(limit)  what could not be observed — offline thinking and review are not visible, and
                  interaction precision is limited to the work-item grain
  needs: engagement_assessment → { overall_reading → {text, citations, confidence},
           observations[] → {dimension, statement, citations, confidence}, limits[] }
         evaluated per work item from { trigger.summary, agent_reaction.summary, outcomes[],
           terminal_states[] } + the work item's source_user_messages (verbatim, by covered_turn)

Section "Team Learning" — reusable, promotable, and avoidable patterns in how the work was done,
                          judged by productivity (good outcomes per unit of human attention), not by
                          prompt polish; abstracted for the team, within-day (trends deferred)
  Prose   key takeaways — the few patterns most worth the team's attention, or a note that the day
          shows nothing strong enough to generalize · synthesize · Citation
  Group "Promote" (only if any)  — practices that reached good outcomes efficiently
                                   (incl. a suitable start + well-placed corrections)
    List(bullet)  {pattern} — what worked and why it was productive       · Tag(confidence) · Citation
  Group "Avoid"   (only if any)  — practices that cost attention or quality: non-converging
                                   correction churn, rework from unclear goals, over-engineering upfront
    List(bullet)  {pattern} — what cost effort/quality + the cheaper way   · Tag(confidence) · Citation
  Group "Reuse"   (only if any)  — workflows worth capturing (stable inputs, repeatable steps, clear output)
    List(bullet)  {pattern} — the repeatable shape (+ light suggested form) · Tag(confidence) · Citation
  Callout(limit)  productivity is read from observable proxies (outcome vs. visible back-and-forth),
                  never a precise effort metric; single-day evidence — recurrence and "improving over
                  time" need cross-day data (deferred); one-offs are flagged, not asserted
  needs: team_learning → { takeaways → {text, citations, confidence},
           patterns[] → {kind(promote|avoid|reuse), statement, rationale, recurrence, citations, confidence},
           limits[] }
         judged from each work item's arc — trigger → corrections (covered_turns / source_user_messages)
           → agent_reaction → outcomes / terminal_states — reading message quality in context;
           seeded by process_outcome (reuse), repeated failed/blocked + non-converging loops (avoid)

rule: any Section whose data is empty renders as Empty(fallback)
```

Notes on the purpose-1 region:

- Executive Summary and the per-project outcomes render the same set at two altitudes: the digest is
  the curated cross-project headline; Work by Project is the complete, grouped detail. They must stay
  consistent.
- `what changed` is lifted from a work item's consolidated `outcomes[].summary` — one list item per
  outcome — or, for a work item that ended without material output, its `terminal_states[].summary`.
  The work item `title` is the group label, and its text only as a fallback for a trivial work item
  with neither. Rendering selects and orders; it never re-writes a claim.
- `disposition` (completed / blocked / interrupted / failed / clarification) is derived from the work
  item's `terminal_states` and outcomes — the at-a-glance "finished or not" signal.
- Non-material and trivial work items are kept (the coverage invariant holds) but folded into a
  per-project "Minor activity" toggle so they do not drown the material work.
- There is no standalone cross-project outcome table: the cross-project headline is the Executive
  Summary, and cross-project slicing is a Notion affordance over the flat outcome records.
- `Toggle "User messages"` reveals the verbatim `source_user_messages` (tool-populated raw user text
  per turn, already secret-redacted) for the work item's covered turns, so a reader can see exactly
  what was asked. It is untrusted display content — the renderer shows it quoted/escaped and never
  interprets it — and the same substrate feeds the engagement and team-learning readings.
- Evidence honesty stays visible: each work item's `limits` (what it did not verify or could not
  confirm) render as a visible caveat, not folded, so a completed-looking outcome never hides the
  boundary that qualifies it. Failures and blocks already show through `disposition`.
- Synthesized aggregate prose carries its own `citations`, so no synthesized claim renders uncited.
  The engagement overall reading and team-learning takeaways additionally carry their own
  `confidence`; the per-project `summary` does not — its confidence is implicit in the work items it
  rolls up, each shown with its own `confidence`.

Notes on the engagement region:

- Per-person, never a score. The section is one overall reading plus cited observations and named
  limits — no grade, percentage, or comparison across people (product principle 6).
- Read from the visible inputs. The user's messages are the only visible human work, so engagement is
  judged primarily from `source_user_messages` — read as content, never as instructions — against the
  work item's `agent_reaction` / `outcomes` / `terminal_states` (whether those inputs guided the
  work). Substance is the signal: a message that frames, corrects, or enhances shows effort, while
  contentless filler ("ok", "go", "continue") with no surrounding direction reads as thin.
- Judged in context, fairly. A terse message is not automatically thin — a "go" that approves a
  reviewed plan is real review. Each observation weighs the message against what it responded to and
  produced, cites its turns, and is hedged by `confidence`.
- Work-item grain (deliberate). Engagement is assessed per work item, not per turn: the work item
  already carries the framing, reaction, outcome, and terminal state, plus its verbatim messages.
  Pairing each message with the exact reaction before and after would mean re-reading every evidence
  card; if that fidelity is wanted it belongs in an earlier phase, not here. The grain is named as a
  limit so the reading stays honest.
- Dimensions (direction / review / correction / recovery) come from product principle 4; observations
  are flat with a `dimension` tag and grouped in rendering, like Work by Project.

Notes on the team-learning region:

- Productivity, not prompt-optimality. Patterns are judged by good outcomes per unit of human
  attention, not by prompt polish. A suitable prompt plus a few well-placed corrections that reach the
  goal beats a perfected upfront prompt that needed none but cost more attention.
- Corrections are neutral-to-positive — efficient steering (product principle 4), never an antipattern
  by themselves; over-investing in upfront prompt perfection can itself be an Avoid. The real Avoid
  signals are wasted attention or poor outcomes: non-converging correction churn, rework from unclear
  goals, redoing the same thing.
- Conservative and hedged. Productivity is read from observable proxies (was the outcome reached? how
  much visible back-and-forth?), never a precise effort metric; a pattern is asserted only when
  recurring or clearly likely to recur, and single sightings are flagged or pushed to "needs more
  evidence." The lens does not moralize.
- Context over frequency. With one day there is little repetition, so the reading leans on each
  pattern's arc in context — prompt → corrections → outcome — rather than counting occurrences;
  cross-day trends ("improving over time") are deferred.
- Patterns, not a verdict on the person, and aligned with engagement: neither rewards volume, both
  treat well-placed corrections as good. Team learning abstracts the shareable pattern; engagement
  attributes the behavior. Coverage of no-material / interrupted items stays in Work by Project's
  "Minor activity"; this section surfaces only the recurring pattern they may reveal.
- Recommended form (Reuse only): a light, generic suggestion — a reusable prompt, checklist, or
  playbook — never a tool-specific build on one day's evidence.

### Markdown Rendering

Markdown rendering serializes the abstract layout to `report.md`. Markdown is a presentation format,
not the source of truth for the report's structure or evidence model.

Block → Markdown:

- `Document` → `# {title}` followed by a status / window / overall-confidence line.
- `Section` → a `##` heading; nested sections deepen to `###`.
- `Group` → a `###` subheading carrying the label.
- `Prose` → a paragraph; an inline `Citation` is appended.
- `List` → `-` or `1.` items.
- `Table` → a GitHub pipe table. Interactive affordances are approximated: rows are pre-sorted by
  the layout's default sort (material first), group-by renders as a leading column or repeated
  sub-tables, and filtering is left to the reader's text search.
- `Tag` → plain text, optionally a marker such as ● material / ○ non-material.
- `Citation` → `S0001:45-52`, the project-scoped session ref and line range.
- `Callout` → a blockquote.
- `Toggle` → a `<details><summary>` block (HTML-in-Markdown), collapsed by default.
- `Empty` → the section's fallback bullet:
  - Executive Summary: `- No supported work claims found for this report window.`
  - Work by Project: `- No supported project-level work items found for this report window.`
  - Engagement Assessment: `- Insufficient supported engagement evidence for this report window.`
  - Team Learning: `- No supported reusable agent-driving pattern found.`

Every concrete work claim in a claim-bearing section cites lines inside exactly one indexed turn
using the report citation format from the
[Evidence Contract](./evidence-contract.md#session-evidence-cards). The renderer must not add
claim-bearing prose absent from `daily-report.json`.

### Notion Rendering

Notion rendering serializes the same abstract layout into a Notion page payload and publishes it as
a row in a Notion database. Like Markdown rendering it is deterministic, read-only over the model,
and adds no claim-bearing content. It is split in two: a pure renderer
(`daily_synthesis/render_notion.py`) that walks the layout into Notion block JSON and writes it to
`report.notion.json`, and a publisher (`daily_synthesis/notion_publish.py`, with the real SDK behind
`notion_client_adapter.py`) that pushes that payload. `report.notion.json` is a deterministic
artifact emitted on every run beside `report.md`; publishing is opt-in (see below).

Block → Notion (the idiomatic mapping, not 1:1 with Markdown):

- `Document` → the page: its title, plus a `properties` map (report_date, status, window, overall
  confidence) the publisher maps to database columns.
- `Section` → a `heading_2`; a `Group` that is a direct section child (a project, an
  engagement/team-learning dimension) → a `heading_3`.
- `Group` that is a list item (a work item) → a native **`toggle`** whose label carries the
  disposition and confidence and whose blocks nest inside — a collapsible record, the idiomatic
  Notion form for a titled cluster in a list.
- `Prose` → a `paragraph`, or a `bulleted_list_item` / `numbered_list_item` inside a list; its
  confidence tags and `Citation` ride in the same rich text.
- `Citation` → an inline-`code` run (e.g. `ReportGenerator · S0001:2-8`), never a link — workspace
  session references have no Notion URL.
- `Toggle` → a native `toggle`; `Callout` tone `quote` (a verbatim user message) → a `quote` block,
  tone `limit` → a `callout` block with a warning icon; `Empty` → the Markdown view's fallback text.

Safety is structural: every model-derived string is placed only in a plain rich-text `text.content`
(never a `link` or other interpreted field), and Notion stores content literally, so no escaping is
needed and a session-derived string cannot forge structure. Notion's content limits are honored in
the payload (each `text.content` ≤ 2000 chars; each block's rich-text array ≤ 100 runs, truncating a
pathologically long single string with a fixed marker).

Publishing (`report generate --notion`): the publisher reads the integration token and target
database id from `NOTION_API_KEY` and `NOTION_PAGE_ID` (so credentials never pass on the command
line) and creates a **new row** per report — re-publishing never edits or deletes an existing row, so
the user prunes stale rows by hand. Property mapping is schema-driven: the database's single
title-typed property gets the page title, every date-typed property gets the report date, and other
types are left for the user. Metadata the database has no column for (status, window, overall
confidence) is surfaced in a banner callout at the top of the page body, so the report is
self-describing against any schema. The page is created empty and its block tree appended one nesting
level at a time, keeping each request within Notion's per-request and create-nesting limits.

The previously open questions are resolved: citations render as inline code (no link); a run always
appends a new page (never in place); and `partial` versus `final` `status` shows in the metadata
banner (and in the `status` column if the database has one). Deferred: setting `汇报人`-style people
columns, find-or-create of the target database, and database-schema introspection beyond
property-type matching.

## AI Synthesis Workflow

Daily synthesis produces `daily-report.json` by **building a deterministic skeleton in code, then
filling only the `synthesize` fields with focused, tool-validated agent passes**. This keeps the AI
surface small and makes faithfulness structural: the write tools reject any synthesized claim that
arrives uncited or with a required field missing, so "every claim is grounded" is enforced rather than
left to prompt discipline.

This page is developer-facing — no agent reads it. Each pass sees only its own rendered prompt and the
workspace files it opens, so any rule a pass must follow has to be restated in that prompt's source.
Every pass is view-agnostic: it writes model fields only and never mentions `report.md`, Markdown, or
Notion (rendering consumes the model afterwards — see [Rendering](#rendering)).

### Steps

1. **Build (code).** Assemble every deterministic field from `project-synthesis.json` and the evidence
   cards, with no AI: the header (`report_date` / `status` / `window`), all of **Work by Project**
   except the project `summary`, and the entire **Executive Summary** (select top outcomes and open
   items by the significance sort, lift their text, resolve citations).
2. **Synthesize (agent passes).** Fill the remaining `synthesize` fields through the validating tools
   below.
3. **Finalize (code).** Derive `overall_confidence` as a roll-up over the per-claim confidences
   (including the synthesized ones), assemble the full `daily-report.json`, and validate it — all
   required fields present, every claim-bearing field carrying a resolvable citation. As
   defense-in-depth against a pass that edits `daily-report.json` directly instead of through a
   validating write tool, Finalize re-resolves *every* stored citation against the prepared
   workspace: a citation is rejected unless it carries its four keys, names a committed turn of its
   own project, and carries the exact line span the session index resolves that turn to.

Two deterministic-rule choices are fixed for the MVP, both tunable later:

- **Executive Summary is uncapped.** Build emits every material outcome and every open item,
  ordered by significance — completeness over truncation. Curating or capping the headline lists is
  deferred.
- **`overall_confidence` is the mean of the per-claim confidence bands.** Finalize averages the
  bands of the material work items and their outcomes, plus the engagement and team-learning
  judgments, and bands the mean at 2.5 (`high`) / 1.5 (`medium`). It is a simple roll-up, not a
  weighted or evidence-quality-aware score.

### Passes

Each pass reads only its substrate and writes only its fields:

| Pass | × | Reads | Writes (through its tool) |
| --- | --- | --- | --- |
| **Per-project summary** | N_projects | one project's work items | `projects[p].summary {text, citations}` |
| **Engagement** | 1 | all work items + their `source_user_messages` | `overall_reading`, `observations[]`, `limits[]` |
| **Team Learning** | 1 | all work-item arcs + `source_user_messages` | `takeaways`, `patterns[]`, `limits[]` |

Per-project is the project-synthesis pattern one level up — an aggregate within a project, blind to
other projects. Engagement and Team Learning are whole-report aggregates because their judgments span
work items (engagement is per-person; team-learning recurrence is cross-item).

### Tool contracts

Each tool follows the `write_evidence` / `write_work_item` pattern: the agent submits a structured
object; the tool validates it — returning `status: invalid` with structured errors so the agent
corrects and retries — then commits. Citations are submitted as turn refs `{session_ref, turn_ref}`
and resolved to line ranges via the session index, so a citation that does not resolve is rejected.

- **`write_project_summary(project_key, summary)`** — `summary: {text, citations}`. Rejects an empty
  `text`, empty `citations`, a citation that names a turn with no committed evidence in this project,
  or a citation whose submitted `project_key` names a different project.
- **`write_engagement(overall_reading, observations, limits)`** — `overall_reading: {text, citations,
  confidence}`, `observations: [{dimension, statement, citations, confidence} …]`, `limits: [str …]`.
  Rejects an empty `overall_reading.text`, any uncited `overall_reading` or observation, or a
  `dimension` / `confidence` outside its controlled values.
- **`write_team_learning(takeaways, patterns, limits)`** — `takeaways: {text, citations, confidence}`,
  `patterns: [{kind, statement, rationale, recurrence, citations, confidence} …]`, `limits: [str …]`.
  Rejects an empty `takeaways.text`, any uncited `takeaways` or pattern, or a `kind` / `confidence`
  outside its controlled values.

  Each agent submits exactly the fields shown in its prompt's JSON block; the tools resolve the
  submitted `{session_ref, turn_ref}` citations to stored `{session_ref, turn_ref, lines}`.

Each is a single call (the sections are curated, not coverage-bound). These extend the package MCP
server, which today exposes `prompt_diary_ping`, `read_session_lines`, `write_evidence`, and
`write_work_item`.

### Prompts

Each pass has its own focused, view-agnostic prompt under `src/prompt_diary/generate/prompts/`, loaded
at runtime by the orchestrator: [Project Summary Prompt](./project-summary-prompt.md),
[Engagement Prompt](./engagement-prompt.md), and [Team Learning Prompt](./team-learning-prompt.md).
These replace the single pre-redesign `daily-synthesizer` prompt.
