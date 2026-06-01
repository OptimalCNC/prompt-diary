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

Daily report synthesis owns the daily report data model and the content of `daily-report.json`.
The reader-facing views rendered from that model are defined in [Rendering](#rendering).

### Daily Report Model

The JSON model is semantic, not a Markdown abstract syntax tree. It must encode the report's
claims, confidence, citations, evidence quality, engagement judgments, agent-driving lessons,
risks, blockers, follow-ups, and evidence gaps as typed fields that can be mapped to an
implementation data model.

The model must not place evidence-bearing claims only in Markdown strings. Prose fields are
allowed, but any claim that affects a report reading must carry local structure for its evidence,
confidence, and source relationship.

Required top-level shape:

```json
{
  "schema_version": 1,
  "report_date": "2026-05-12",
  "status": "final",
  "window": {
    "local_start": "2026-05-12T00:00:00+08:00",
    "local_end": "2026-05-13T00:00:00+08:00",
    "timezone": "Asia/Shanghai"
  },
  "overall_confidence": "medium",
  "executive_summary": {
    "top_outcomes": [],
    "main_risks": [],
    "confidence_limits": []
  },
  "outcome_overview": [],
  "projects": [],
  "verification_evidence_quality": {
    "verified_results": [],
    "partially_verified_results": [],
    "unverified_claims": [],
    "contradictions": [],
    "missing_checks": [],
    "confidence_limits": []
  },
  "engagement_assessment": {
    "overall_judgment": "Insufficient evidence to judge",
    "supporting_observations": [],
    "limits": []
  },
  "ai_agent_driving_quality": {
    "useful_patterns": [],
    "risks_or_antipatterns": [],
    "shareable_skills": []
  },
  "problems_risks_help_needed": [],
  "blockers_next_actions": [],
  "no_material_interrupted_examples": [],
  "follow_ups": [],
  "evidence_gaps": []
}
```

Common record shapes:

- `ReportCitation`: `project_key`, `session_ref`, and `lines`. `lines` uses the same
  `<start>-<end>` range as report Markdown citations.
- `WorkItemRef`: `project_key` and `work_item_ref`.
- `SessionRef`: `project_key` and `session_ref`.
- `EvidenceChainRef`: `project_key`, `session_ref`, and `turn_ref`. `turn_ref` is never used
  without `session_ref`.
- `GapRef`: `project_key`, `session_ref`, and `turn_ref` for an indexed turn that has no committed
  evidence chain.
- `SourceRef`: the most specific available synthesis or evidence handle, such as `WorkItemRef`,
  `SessionRef`, `EvidenceChainRef`, or `GapRef`. Turn-level source refs use `EvidenceChainRef` or
  `GapRef`; a bare `turn_ref` is invalid.
- `ReportClaim`: `summary`, `confidence`, `citations`, and `source_refs`. It may also include
  `verification_status`, `trigger`, `agent_reaction`, `result`, `risk`, or `recommended_next_action`
  when those fields serve the surrounding section.
- `ProjectReport`: `project_key`, `project_label`, `summary`, `work_items`, `blockers`, `risks`,
  `confidence`, and evidence-accounting notes needed to explain omitted, no-material, interrupted,
  or evidence-gap items.
- `EngagementObservation`: observable user direction, review, correction, resume action, or
  acceptance criteria, with `citations`, `confidence`, and `limits`.

Model rules:

- All required top-level fields are present. Empty arrays are valid when no supported content
  exists.
- Claim-bearing fields use `ReportClaim` or a narrower record that contains equivalent citation
  and confidence fields.
- Every concrete work claim cites lines inside exactly one indexed turn using the report citation
  rules from the [Evidence Contract](./evidence-contract.md#session-evidence-cards).
- Trigger, agent reaction, result, terminal state, and confidence remain separable for major
  outcomes.
- MVP scope (verification deferred): populate `verification_evidence_quality` only with observable
  signals — `contradictions` drawn from `failed` terminal states, `missing_checks` for material
  outcomes that carry no observed check, and `confidence_limits`. Leave `verified_results`,
  `partially_verified_results`, and `unverified_claims` empty; verified/unverified verdicts are not
  synthesized here and will later be derived from verification fields on evidence-chain outcomes.
- Evidence gaps may refer to `metadata.json` or session indexes, but session content claims still
  require normal session-line citations.
- No-material, interrupted, failed, paused, resumed, and clarification-only interactions stay
  represented when they bear on the report's evidence honesty, engagement review, or team learning.

### Field Provenance

Every model field is produced one of four ways. Only `synthesize` fields require the daily
synthesizer agent; `lift` / `derive` / `resolve` are deterministic and should be built by code, which
also guarantees they cannot drift from the evidence they came from.

- **lift** — copied verbatim from an upstream artifact (a work item, `source_user_messages`); no transformation.
- **derive** — computed deterministically from upstream fields.
- **resolve** — looked up deterministically, such as a turn reference to its line range via the evidence card.
- **synthesize** — newly written by the agent; the only AI-produced fields.

This table is filled in section by section as the abstract layout settles, and mirrors each block's
`needs`. (The [Daily Report Model](#daily-report-model) JSON above predates the layout redesign and
will be reconciled with the layout when the contract is settled — after the layout.)

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

Pending sections (Executive Summary, and the evidence-trust / engagement / team-learning lenses) get
their rows as each is designed.

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
the layout settles the model. The layout is refined section by section as each report lens is
designed; sections not yet designed appear as placeholders. It is the living structure this page
tracks. Each field's provenance — `lift` / `derive` / `resolve` / `synthesize` — is recorded in
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

Layout (the purpose-1 region — work communication — is detailed; later lenses are placeholders
filled as they are designed):

```text
Document  "Prompt Diary Report — {report_date}"
  properties: status{final|partial} · window{start–end, tz} · overall_confidence{high|medium|low}
  needs: report_date, status, window, overall_confidence

Section "Executive Summary" — the 30-second cross-project digest
  List(bullet)  top material outcomes (curated) — Prose · Citation
  List(bullet)  headline unfinished / blocked   — Prose · Citation
      needs: executive_summary → {top_outcomes[], unfinished[]} → {text, citations}

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

Section "Verification / Evidence Quality" — observed contradictions or failures, missing checks, and confidence limits; verified/unverified verdicts deferred (MVP)
    blocks: to design (Evidence-Trust lens)

Section "Engagement Assessment" — observable evidence of how the user directed, reviewed, corrected, or resumed agent work
    blocks: to design (Engagement lens)

Section "AI-Agent Driving Quality" — reusable working mechanisms, good practices, risks, anti-patterns, and skills worth sharing
    blocks: to design (Team-Learning lens)

Section "Problems / Risks / Help Needed" — unresolved risks, unsupported claims, missing verification, or areas needing human input
    blocks: to design

Section "Blockers and Next Actions" — blockers or open issues paired with supported next actions
    blocks: to design

Section "No-Material / Interrupted Examples" — low-value, interrupted, paused, resumed, failed, or clarification-only interactions that are useful workflow signals
    blocks: to design (Team-Learning lens)

Section "Follow-ups" — specific future work grounded in the day's evidence
    blocks: to design

Section "Evidence Gaps" — missing or weak evidence that affects confidence
    blocks: to design

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
  interprets it — and the same substrate feeds the engagement and evidence-trust readings.

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
  - Verification / Evidence Quality: `- No verification or evidence-quality issues found.`
  - Engagement Assessment: `- Insufficient supported engagement evidence for this report window.`
  - AI-Agent Driving Quality: `- No supported reusable agent-driving pattern found.`
  - Problems / Risks / Help Needed: `- No supported problems, risks, or help requests found in target spans.`
  - Blockers and Next Actions: `- No supported blockers or next actions found.`
  - No-Material / Interrupted Examples: `- No supported no-material or interrupted interactions found.`
  - Follow-ups: `- No supported follow-ups found.`
  - Evidence Gaps: `- No evidence gaps found.`

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
