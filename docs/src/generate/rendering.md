# Rendering

Rendering is the fourth generation phase. It takes the semantic daily report model,
`daily-report.json`, and projects it into two outputs: `report.md`, the reader-facing Markdown view,
and `report.notion.json`, the Notion page *payload* — an intermediate artifact the publish step
(see [Publishing](#publishing)) uploads to create the Notion page, which is the reader-facing Notion
view. It is deterministic and **agent-free** — no Codex, no MCP tools, no prompts — so every claim,
citation, confidence value, and evidence-quality signal in a rendered output comes from the model and
nothing is added. Because rendering is deterministic, the "no new claims" guarantee is structural,
not a rule the synthesizer must remember.

Rendering reads `daily-report.json` from the prepared workspace root and writes its outputs beside
it. It does not read sessions, evidence cards, or work items; an output that reads those, or
introduces content absent from the model, is a rendering bug.

Rendering turns `daily-report.json` into its outputs through an intermediate, engine-independent
**abstract layout**:

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
model, is a rendering bug.

Each block also declares the model data it consumes (`needs:`). Those needs are the layout's claim
on the contract — the union of every `needs` is what `daily-report.json` must carry — so settling
the layout settles the model, and it is the living structure this page tracks. Each field's
provenance — `lift` / `derive` / `resolve` / `synthesize` — is recorded in
[Field Provenance](./daily-synthesis.md#field-provenance); only `synthesize` fields need the agent.

## Abstract Layout

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
- `Toggle(label)` — a collapsible region for top-level records or renderer-specific folding;
  renderers may degrade nested labels to plain content when that better fits the target engine.
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
      Group    {work item title}              · Tag(disposition) · Tag(confidence)
        Prose label "Why"                     — trigger.summary (+ agent_reaction) · Citation
        Prose label "User messages"           — verbatim source_user_messages for the work item's turns · Citation
        List of outcomes — what changed · Tag(confidence) · Citation
        Callout(limit) (only if any) — what this work item did not verify or confirm · work_items[].limits
        (a work item with no material outcome shows its terminal disposition in place of the outcomes)
    Prose label "Minor activity"              — introduces the project's no-material / trivial work items
      List of minor work items                — same work-item Group shape
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

- Executive Summary and the per-project outcomes render the same evidence at two altitudes: the
  digest is a capped cross-project headline subset; Work by Project is the complete, grouped detail.
  They must stay consistent.
- `what changed` is lifted from a work item's consolidated `outcomes[].summary` — one list item per
  outcome — or, for a work item that ended without material output, its `terminal_states[].summary`.
  The work item `title` is the group label, and its text only as a fallback for a trivial work item
  with neither. Rendering selects and orders; it never re-writes a claim.
- `disposition` (completed / blocked / interrupted / failed / clarification) is derived from the work
  item's `terminal_states` and outcomes — the at-a-glance "finished or not" signal.
- Non-material and trivial work items are kept (the coverage invariant holds) but grouped under a
  per-project "Minor activity" label so they do not drown the material work.
- There is no standalone cross-project outcome table: the cross-project headline is the Executive
  Summary, and cross-project slicing is a Notion affordance over the flat outcome records.
- The "User messages" block reveals the verbatim `source_user_messages` (tool-populated raw user
  text per turn, already secret-redacted) for the work item's covered turns, so a reader can see
  exactly what was asked. It is untrusted display content — the renderer shows it quoted/escaped and
  never interprets it — and the same substrate feeds the engagement and team-learning readings.
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

## Markdown Rendering

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

## Notion Rendering

Notion rendering serializes the same abstract layout into a Notion page payload. Like Markdown
rendering it is deterministic, read-only over the model, and adds no claim-bearing content. It is
split in two: a pure renderer
(`rendering/render_notion.py`) that walks the layout into Notion block JSON and writes it to
`report.notion.json`, and a publisher (`rendering/notion_publish.py`, with the real SDK behind
`notion_client_adapter.py`) that pushes that payload. `report.notion.json` is a deterministic
artifact emitted on every run beside `report.md`; `generate render --notion` also regenerates it from
`daily-report.json` immediately before publishing.

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
- `Toggle` → a plain label paragraph followed by its children; only work-item `Group` list items
  become native Notion toggles. `Callout` tone `quote` (a verbatim user message) → a `quote` block,
  tone `limit` → a `callout` block with a warning icon; `Empty` → the Markdown view's fallback text.

Safety is structural: every model-derived string is placed only in a plain rich-text `text.content`
(never a `link` or other interpreted field), and Notion stores content literally, so no escaping is
needed and a session-derived string cannot forge structure. Notion's content limits are honored in
the payload (each `text.content` ≤ 2000 chars; each block's rich-text array ≤ 100 runs, truncating a
pathologically long single string with a fixed marker).

## Publishing

Publish (`generate render --notion`) is an outward-facing, gated step layered on top of the
deterministic render. The render command resolves an existing workspace, requires
`daily-report.json`, regenerates `report.notion.json`, then invokes the publisher. The publisher
reads the integration token and target database id from the stored config (`prompt-diary config
init`) or the `NOTION_API_KEY` / `NOTION_PAGE_ID` env vars (so credentials never pass on the command
line) and creates a **new row** per report — re-publishing never edits or deletes an existing row,
so the user prunes stale rows by hand. `report generate` runs rendering as an in-pipeline phase, and
when Notion publishing is active (`--notion`, or default configured publishing without `--no-notion`)
it then publishes through this same path. Property mapping is schema-driven: the database's single
title-typed property gets the page title, every date-typed property gets the report date, the
configured reporter name (from `config init` — the `汇报人` column by default, retargetable via
`notion_reporter_property`) is written into that one text property when it exists. Whenever the
reporter cannot be written — the column is missing, is present but not a text property, or no name is
configured — the publish still succeeds but prints a `Warning:` to stderr rather than silently
leaving the column empty (a database with no reporter column at all is not flagged). All other
property types are left untouched. A creation timestamp should use Notion's native **Created time** property
type (with *Include time* enabled), which Notion auto-fills with the upload instant; because the
publisher writes only `date`-typed columns, it never overwrites a `created_time` column. Metadata the
database has no column for (status, window, overall confidence) is surfaced in a status-colored
banner callout at the top of the page body (final → green, partial → yellow), followed by a table of
contents, so the report is self-describing and navigable against any schema. When the rendered body
fits Notion's create-page body limits (≤100 top-level children, ≤1000 block elements, and no
grandchildren), the publisher creates the page with its body in the same request. Larger or deeper
reports fall back to append batches that still respect ≤100 top-level children and ≤1000 block
elements per request, inlining leaf-only children and recursing only when returned block ids are
needed for deeper descendants.

The previously open questions are resolved: citations render as inline code (no link); a run always
appends a new page (never in place); `partial` versus `final` `status` shows in the color-coded
metadata banner (and in the `status` column if the database has one); and the `汇报人` reporter is a
configured free-form name (like `git config user.name`, not a Notion user) written into a text
column. Deferred: find-or-create of the target database, and database-schema introspection beyond
property-type matching.
