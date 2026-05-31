# Coaching And Skill Mining

Coaching and skill mining is an optional part of Daily Report Synthesis that extracts reusable
lessons about how the user drove AI coding agents. The required `AI-Agent Driving Quality` section
in `report.md` gives the concise daily view; optional coaching artifacts expand that view for team
learning. Neither should turn the report into personality judgment.

This optional part should use evidence cards, work items, the daily report, and available
validation results. It must not invent lessons from unsupported impressions.

When it runs before `report.md` is finalized, its findings may inform `AI-Agent Driving Quality`;
when it runs after `report.md`, it expands the same evidence-backed lessons without replacing the
report.

## Inputs And Outputs

Inputs:

- per-session evidence cards
- project work items
- draft or final `report.md`, when available
- validation results, when available

Optional outputs:

- `coaching_report.md`
- team standard candidates
- prompt patterns and anti-patterns

These outputs are optional generation artifacts. They do not replace `report.md`.

## Effective Patterns

Look for behaviors that improved the agent's output:

- concrete goals and constraints
- acceptance criteria before generation
- examples or counterexamples
- review and correction of weak output
- resuming or redirecting a paused agent with a clear `Continue` or follow-up
- explicit requests for tests or validation
- decomposing broad work into smaller deliverables
- asking for reusable templates, checklists, or standards

Each pattern should explain:

```text
pattern -> evidence -> why it worked -> how teammates can reuse it
```

## Anti-Patterns

Look for evidence-backed risks:

- broad prompts without acceptance criteria
- accepting agent claims without tests, artifacts, or review
- allowing document rewrites without preservation checks
- mixing unrelated goals in one prompt
- asking for a final report before evidence extraction is complete
- repeated loops with no artifact, decision, validation result, or clarified blocker
- repeated interruptions or `Continue` triggers caused by weak agent stopping behavior

Each anti-pattern should explain:

```text
anti-pattern -> evidence -> risk -> how to avoid it
```

Do not overgeneralize from one weak example. Use lower confidence when a pattern appears only once
or has ambiguous evidence.

## Pattern Contract

```json
{
  "pattern_id": "coach-P0001",
  "category": "effective_pattern | anti_pattern | skill | workflow_improvement",
  "pattern": "Require verification evidence before accepting completion claims.",
  "evidence_refs": [
    {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "turn_ref": "T0002"}
  ],
  "why_it_matters": "It prevents agent self-report from becoming a false completed outcome.",
  "reuse_guidance": "Ask the agent to show command output, test results, artifact paths, or explicit unverified status.",
  "risk_if_ignored": "The report may claim completion when only a proposal or untested patch exists.",
  "confidence": "high"
}
```

## Team Standard Candidates

Coaching and skill mining may propose standards such as:

- Every daily outcome must be traceable to evidence card chains and report citations.
- Every code completion claim must include a diff, test result, or explicit unverified status.
- Every document migration task should include preservation checks for original rules and
  weakened constraints.
- Every final report should pass deterministic validation or state unresolved uncertainty.
- Every blocker should include a recommended next action when evidence supports one.
- Complex agent prompts should state acceptance criteria before generation begins.

Standards should be proposed as candidates, not silently imposed as product requirements.

## Prompt Template

```text
You are an AI-agent usage coach for Prompt Diary.

You will receive evidence cards, project synthesis outputs, the daily report, and any available
validation results.

Your job is to identify reusable lessons about how the user drove AI coding agents.

Focus on:
1. Behaviors that produced good outcomes.
2. Behaviors that caused wasted loops, unsupported claims, or weak verification.
3. Prompt patterns worth sharing with teammates.
4. Anti-patterns worth avoiding.
5. Workflow gaps that should become tools, templates, tests, or standards.

Rules:
- Use only evidence-backed behavior.
- Do not judge personality, motivation, laziness, or hidden intent.
- Separate strong evidence from weak evidence.
- Prefer actionable recommendations.
- Do not overfit to one isolated example.

Output:
- effective patterns
- anti-patterns
- lessons from no-material, interrupted, failed, or clarification-only chains
- skills worth sharing
- workflow improvements needed
- suggested team-level standards
- confidence
```

## Quality Checklist

Before accepting coaching output:

- Each pattern is backed by evidence references.
- The report does not repeat project outcomes as if they were coaching lessons.
- Recommendations are concrete enough to reuse.
- Anti-patterns are not exaggerated from weak evidence.
- Workflow improvements are implementable as tools, templates, tests, or standards.
- Coaching output does not contradict available validation results.
