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

The concrete `daily-report.json` schema — and the mechanism that produces and validates each field —
is settled together with the AI synthesis workflow (see
[Daily Synthesizer Prompt](#daily-synthesizer-prompt)), since it depends on how synthesis is driven;
it is intentionally not frozen here.

### Field Provenance

Every model field is produced one of four ways. Only `synthesize` fields require the daily
synthesizer agent; `lift` / `derive` / `resolve` are deterministic and should be built by code, which
also guarantees they cannot drift from the evidence they came from.

- **lift** — copied verbatim from an upstream artifact (a work item, `source_user_messages`); no transformation.
- **derive** — computed deterministically from upstream fields.
- **resolve** — looked up deterministically, such as a turn reference to its line range via the evidence card.
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
| `confidence` | `work_items[].confidence`, `outcomes[].confidence` | lift |
| `User messages` | `source_user_messages` (tool-populated) | lift |
| `disposition` | `terminal_states` + `outcomes` | derive |
| ordering · material/Minor split | `kind` + sort rule | derive |
| `Citation` | `evidence_refs` → lines via the evidence card | resolve |
| project `summary` | the project's work items | **synthesize** |

**Engagement Assessment**

| Field | Source | Provenance |
| --- | --- | --- |
| `Citation` | observation `citations` → lines via the evidence card | resolve |
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
| `Citation` | pattern `citations` → lines via the evidence card | resolve |
| pattern `kind` | classified by the agent (promote / avoid / reuse) | **synthesize** |
| pattern `statement` / `rationale` | the work item arc + `source_user_messages`, read in context | **synthesize** |
| `recurrence` | occurrences across work items (countable seed; the agent states it) | **synthesize** |
| `confidence` | the agent's per-pattern judgment | **synthesize** |
| `takeaways` | the patterns | **synthesize** |
| `limits` | named by the agent + standing single-day / proxy-metric limits | **synthesize** |

Another `synthesize`-heavy judgment lens, grounded by mandatory `Citation`s and seeded deterministically
by `process_outcome` (reuse) and repeated `failed` / `blocked` terminal states (avoid).

Rows for the remaining sections (Executive Summary, Open Items & Next Steps) are settled with the
workflow. Evidence-quality signals (confidence, limits, citations) are not a section of their own —
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
      (a work item with no material outcome shows its terminal disposition in place of the outcomes)
    Toggle "Minor activity" (folded)          — the project's no-material / trivial work items
    needs: projects[] → { project_label, summary, work_items[] → { title, kind, disposition,
           confidence, trigger.summary, agent_reaction.summary,
           outcomes[] → {what_changed, confidence, citations}, terminal_states[].summary } }
           + source_user_messages by covered_turn → verbatim {messages} per (session_ref, turn_ref)

Section "Open Items & Next Steps" — what's unfinished or blocked, each with a grounded next step
  List(bullet)  {the blocked / unfinished work}            · Tag(disposition) · Citation
                → next step: {grounded action}  (or "help needed: {what}")
  needs: next_steps[] → {summary, disposition, next_action, citations, confidence}
         from blocked / interrupted / failed work items + the agent's grounded next action

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
  needs: engagement_assessment → { overall_reading,
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
  needs: team_learning → { takeaways,
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

Notes on open items & next steps:

- Consolidates what used to be split across "Problems / Risks / Help Needed", "Blockers and Next
  Actions", and "Follow-ups": every unfinished or blocked work item with a grounded next step, or an
  explicit "help needed" where a human decision is required.
- Daily synthesis owns next actions (project synthesis must not prescribe them); each next step is
  grounded in the work item's evidence and cited — no speculative advice.
- The Executive Summary headlines the top open items; this section is the complete list — the
  "where to resume" view.

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
  - Open Items & Next Steps: `- No supported blockers or next actions found.`
  - Engagement Assessment: `- Insufficient supported engagement evidence for this report window.`
  - Team Learning: `- No supported reusable agent-driving pattern found.`

Every concrete work claim in a claim-bearing section cites lines inside exactly one indexed turn
using the report citation format from the
[Evidence Contract](./evidence-contract.md#session-evidence-cards). The renderer must not add
claim-bearing prose absent from `daily-report.json`.

### Notion Rendering

> Draft — a planned view, not part of the MVP output. It is specified here so the layout stays
> engine-independent and the renderer boundary stays clear; no Notion renderer ships yet.

Notion rendering serializes the same abstract layout into a Notion page. Like Markdown rendering it
is deterministic, read-only over the model, and adds no claim-bearing content.

Block → Notion:

- `Document` → a page titled `Prompt Diary Report — <report_date>`; `properties` become page
  properties (status, window, overall confidence) so reports are filterable and sortable across days.
- `Section` and `Group` → heading or toggle blocks.
- `Prose` → a text block; `Citation` → a link or mention.
- `List` → bulleted or numbered list blocks; a `List` of uniform tagged records (the per-project
  outcomes) can instead be a filterable, sortable database view — the cross-project slice the linear
  Markdown view does not provide.
- `Toggle` → a native toggle block.
- `Table` → a Notion database or linked view whose filters and sorts realize the layout's
  affordances directly; `Tag` columns become select or status properties.
- `Callout` → a callout block.
- `Empty` → the same fallback text as the Markdown view.

Open questions before a Notion renderer is built: how citations link out (workspace session
references have no Notion URL yet), whether a run updates a report in place or appends a new page,
and how `partial` versus `final` status is surfaced. These are deferred with the rest of view work.

## Daily Synthesizer Prompt

This contract is developer-facing: it documents the design for repository developers and
readers. The daily synthesizer agent never reads it. At runtime the agent sees only the rendered
prompt below and the workspace files it opens. Any decision in this contract that the agent must
act on has to be restated as explicit instructions in that prompt source; a cross-reference to
this contract does not reach the agent.

The prompt is view-agnostic: it instructs only the production of `daily-report.json` and must not
mention `report.md`, Markdown, Notion, or any rendering detail. Rendering consumes the model after
synthesis returns; see [Rendering](#rendering).

Prompt source: `src/prompt_diary/generate/prompts/daily-synthesizer.md` — loaded at runtime by the
orchestrator.

See [Daily Synthesizer Prompt](./daily-synthesizer-prompt.md).
