# Daily Report Synthesis

Daily report synthesis is the final report-producing generation phase. It turns project work items
into a semantic daily report model and a Markdown report, where the four
[product purposes](../product.md#purposes) must converge from one evidence base: work
communication, evidence trust, engagement review, and team learning.

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

- `daily-report.json` in the prepared workspace root
- `report.md` in the prepared workspace root

`daily-report.json` is the authoritative report artifact. `report.md` is a deterministic
Markdown view rendered from that model. Daily report synthesis owns the internal checks needed
before returning both artifacts. A report that misses required model fields, uses invalid
citations, hides required evidence-quality limits, or includes forbidden high-risk content is a
daily report synthesis bug.

## Report Contract

Daily report synthesis owns the daily report data model, the required Markdown view, and the
content of both artifacts.

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

### Markdown Rendering

`report.md` is rendered from `daily-report.json`. Markdown is a presentation format, not the
source of truth for the report's evidence model.

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

Every concrete work claim in claim-bearing Markdown sections must cite lines inside exactly one
indexed turn using the report citation format from the
[Evidence Contract](./evidence-contract.md#session-evidence-cards). The renderer must not add
claim-bearing prose that is absent from `daily-report.json`.

## Daily Synthesizer Prompt

This contract is developer-facing: it documents the design for repository developers and
readers. The daily synthesizer agent never reads it. At runtime the agent sees only the rendered
prompt below and the workspace files it opens. Any decision in this contract that the agent must
act on has to be restated as explicit instructions in that prompt source; a cross-reference to
this contract does not reach the agent.

Prompt source: `src/prompt_diary/generate/prompts/daily-synthesizer.md` — loaded at runtime by the
orchestrator.

See [Daily Synthesizer Prompt](./daily-synthesizer-prompt.md).
