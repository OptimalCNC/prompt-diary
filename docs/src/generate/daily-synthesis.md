# Daily Report Synthesis

Daily report synthesis is the final report-producing generation phase. It turns project work items
into a semantic daily report model, `daily-report.json`, where the four
[product purposes](../product.md#purposes) must converge from one evidence base: work
communication, evidence trust, engagement review, and team learning. Reader-facing views —
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
  represented when they affect evidence trust, engagement review, or team learning.

## Rendering

Rendering projects `daily-report.json` into reader-facing views. It is deterministic and adds no
judgment: every claim, citation, confidence value, and evidence-quality signal in a view comes from
the model. A view never reads sessions, evidence cards, or work items, and never introduces content
absent from the model — doing so is a rendering bug. Because rendering is deterministic, the "no new
claims" guarantee is structural, not a rule the synthesizer must remember.

Each view is one renderer over the same model, so adding a view never changes the model or the
synthesizer. `report.md` is the required view; further views (for example, Notion) are optional and
may be added without touching synthesis.

### Markdown Rendering

`report.md` is the required Markdown view of `daily-report.json`. Markdown is a presentation format,
not the source of truth for the report's evidence model.

The rendered report must use this structure:

```markdown
# Prompt Diary Report - <report_date>

Status: <final|partial>
Window: <local start> to <local end> <timezone>
Overall Confidence: <high|medium|low>

## Executive Summary
## Outcome Overview
## Project Details
## Verification / Evidence Quality
## Engagement Assessment
## AI-Agent Driving Quality
## Problems / Risks / Help Needed
## Blockers and Next Actions
## No-Material / Interrupted Examples
## Follow-ups
## Evidence Gaps
```

Section intent:

- `Executive Summary`: highest-priority supported outcomes, risks, and confidence limits.
- `Outcome Overview`: cross-project scan of major outcomes with trigger, reaction, result,
  terminal state, confidence, and citations.
- `Project Details`: grouped project-level work items with enough context for teammates.
- `Verification / Evidence Quality`: observed contradictions or failures, missing checks, and
  confidence limits. Verified/unverified verdicts are deferred (MVP).
- `Engagement Assessment`: observable evidence of how the user directed, reviewed, corrected, or
  resumed agent work.
- `AI-Agent Driving Quality`: reusable working mechanisms, good practices, risks, anti-patterns,
  and skills worth sharing.
- `Problems / Risks / Help Needed`: unresolved risks, unsupported claims, missing verification, or
  areas needing human input.
- `Blockers and Next Actions`: blockers or open issues paired with supported next actions.
- `No-Material / Interrupted Examples`: low-value, interrupted, paused, resumed, failed, or
  clarification-only interactions that are useful workflow signals.
- `Follow-ups`: specific future work grounded in the day's evidence.
- `Evidence Gaps`: missing or weak evidence that affects confidence.

When a section's backing model field is empty, the renderer emits the section's fallback bullet so
every required section is still present:

- Executive Summary: `- No supported work claims found for this report window.`
- Outcome Overview: `- No supported outcomes found for this report window.`
- Project Details: `- No supported project-level work items found for this report window.`
- Verification / Evidence Quality: `- No verification or evidence-quality issues found.`
- Engagement Assessment: `- Insufficient supported engagement evidence for this report window.`
- AI-Agent Driving Quality: `- No supported reusable agent-driving pattern found.`
- Problems / Risks / Help Needed: `- No supported problems, risks, or help requests found in target spans.`
- Blockers and Next Actions: `- No supported blockers or next actions found.`
- No-Material / Interrupted Examples: `- No supported no-material or interrupted interactions found.`
- Follow-ups: `- No supported follow-ups found.`
- Evidence Gaps: `- No evidence gaps found.`

Every concrete work claim in claim-bearing Markdown sections must cite lines inside exactly one
indexed turn using the report citation format from the
[Evidence Contract](./evidence-contract.md#session-evidence-cards). The renderer must not add
claim-bearing prose that is absent from `daily-report.json`.

### Notion Rendering

> Draft — a planned view, not part of the MVP output. It is specified here so the model stays
> view-agnostic and the renderer boundary stays clear; no Notion renderer ships yet.

Notion rendering projects the same `daily-report.json` into a Notion page so a report can be read
and shared in a workspace. Like Markdown rendering, it is deterministic, read-only over the model,
and adds no claim-bearing content.

Planned shape:

- One Notion page per report date, titled `Prompt Diary Report - <report_date>`, with `status`,
  `window`, and `overall_confidence` as page properties so reports are filterable and sortable.
- The same section order as the Markdown view, one Notion heading block per section, so the two
  views stay legible against each other.
- Each `ReportClaim` becomes a block carrying its `summary`, with `confidence` and citations shown
  inline; a citation links back to its cited session and line range rather than restating evidence.
- Empty sections use the same fallback bullets as the Markdown view.

Open questions to settle before a Notion renderer is built: how citations link out (workspace
session references have no Notion URL yet), whether a run updates a report in place or appends a new
page, and how `partial` versus `final` status is surfaced. These are deferred with the rest of view
work.

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
