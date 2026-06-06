# Daily Report Synthesis

Daily report synthesis is the convergence *synthesis* phase. It turns project work items into a
semantic daily report model, `daily-report.json`, where the three
[product purposes](../product.md#purposes) must converge from one evidence base: work
communication, engagement review, and team learning — each honest about its evidence. The
[Rendering](./rendering.md) phase then projects that model into `report.md` (the Markdown view) and
`report.notion.json` (the Notion page payload the publish step uploads to create the Notion page),
plus any future engine; the synthesizer that builds the model is view-agnostic.

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

`daily-report.json` is the authoritative report artifact and this phase's only output. The Markdown
view `report.md` and the Notion page payload `report.notion.json` (which the publish step uploads to
create the Notion page) are deterministic projections of that model produced by the
[Rendering](./rendering.md) phase, not by this one: synthesis builds the model, rendering projects it
into those outputs. A model that misses required fields, uses invalid citations, hides required
evidence-quality limits, or includes forbidden high-risk content is a synthesis bug; a rendered
output that adds, drops, or alters a claim relative to the model is a rendering bug.

## Report Contract

Daily report synthesis owns the daily report data model — the content of `daily-report.json` — from
which the reader-facing views in [Rendering](./rendering.md) are produced. Its shape is set by the
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

The reader-facing outputs are produced by the [Rendering](./rendering.md) phase, which reads
`daily-report.json` and writes `report.md` (the Markdown view) and `report.notion.json` (the Notion
page payload the publish step uploads to create the Notion page). Rendering is deterministic and
agent-free, so those outputs add no claims: every claim, citation, confidence value, and
evidence-quality signal in them comes from this model. The abstract layout, the block vocabulary,
and the Block→Markdown / Block→Notion mappings live on that page.

## AI Synthesis Workflow

Daily synthesis produces `daily-report.json` by **building a deterministic skeleton in code, then
filling only the `synthesize` fields with focused, tool-validated agent passes**. This keeps the AI
surface small and makes faithfulness structural: the write tools reject any synthesized claim that
arrives uncited or with a required field missing, so "every claim is grounded" is enforced rather than
left to prompt discipline.

This page is developer-facing — no agent reads it. Each pass sees only its own rendered prompt and the
workspace files it opens, so any rule a pass must follow has to be restated in that prompt's source.
Every pass is view-agnostic: it writes model fields only and never mentions `report.md`, Markdown, or
Notion (rendering consumes the model afterwards — see [Rendering](./rendering.md)).

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
