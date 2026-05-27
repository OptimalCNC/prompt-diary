# Generation Audits

Generation audits are adversarial semantic checkpoints for evidence cards, project synthesis, and
the draft daily report artifacts. They are separate from deterministic validation. Validation
checks required shape and structural citation boundaries; audits check whether claims are actually
supported, fair, complete, and not overstated.

An audit checkpoint belongs to the phase that produced the artifact under review. It usually
annotates, challenges, or revises that artifact rather than turning it into a different artifact
type. Implementations may enable audit checkpoints individually; when a checkpoint is enabled, its
findings go back to the producing phase for revision. Audit uses the prepared workspace and
generated artifacts; it must not search outside the prepared workspace for new work claims.

## Inputs And Outputs

Inputs:

- `metadata.json`
- project session indexes
- copied sessions referenced by indexes
- per-session evidence cards
- evidence audit status and reasoning, when the evidence audit checkpoint is enabled
- project synthesis outputs, when the project or report audit checkpoint is enabled
- draft `daily-report.json` and `report.md`, when the report audit checkpoint is enabled

Durable audit outputs, when configured:

- `evidence_audit.md`
- `project_audit.md`
- `report_audit.md`
- `audit_findings.json`

These audit outputs are generation artifacts. They do not change the preparation layout. A disabled
checkpoint may produce no audit artifact; an enabled checkpoint may also store findings in another
orchestrator-managed form.

## Audit Modes

### Evidence Audit

When enabled, run after MCP tools create or update evidence cards. Findings go back to evidence
extraction, which repairs the card through MCP tools or reruns extraction until the card is
acceptable.

For each evidence chain, check:

- The trigger, agent reaction, observed outcome, observed checks, and terminal state are separated.
- Outcome citations support the outcome and cite agent reaction lines, not only user intent.
- Citations are inspectable and semantically support the claim. MCP validation owns structural
  enforcement for extracted chain citations.
- Agent reaction citations are not rejected merely because their timestamps cross midnight when
  they remain inside the indexed target span for an in-window human trigger.
- Outcome categories use the controlled values from the Evidence Contract.
- Terminal states such as `no_material`, `interrupted`, `failed`, `blocked`, and
  `clarification_only` match the observable evidence.
- Audit assigns or normalizes verification status and reasoning for material outcomes and relevant
  terminal states.
- Agent self-report is not treated as verification.
- Missing evidence is labeled as missing or unverified rather than converted into failure.
- Any extracted note about user behavior stays observable and does not infer personality,
  motivation, laziness, or hidden intent.

### Project Audit

When enabled, run after project synthesis writes work items. Findings go back to project synthesis
until grouping, evidence accounting, and verification handling are acceptable.

For each project synthesis output, check:

- Related chains are grouped into the same task thread, and unrelated chains are not over-merged.
- The earliest meaningful trigger, later corrections, approvals, and resume actions are preserved
  when they changed the result.
- Every indexed session, evidence card, and evidence chain has an evidence accounting disposition.
- Indexed sessions without evidence cards are represented as evidence gaps unless a preparation or
  extraction error explains them.
- No-material, interrupted, failed, blocked, and clarification-only chains are represented with
  dummy or non-material work items when they affect risk, engagement review, or team learning.
- Missed evidence cards, missed chains, and unexplained accounting holes are reported as synthesis
  bugs.
- Verification status and reasoning from evidence audit are preserved without upgrading
  unverified work into completed or validated work.
- Blockers, contradictions, missing checks, and useful failures are not hidden.
- Any claim intended for `report.md` can be expanded to valid work-claim citations.

### Report Audit

When enabled, run after a draft `daily-report.json` is composed, `report.md` is rendered, and
before deterministic validation is accepted. Findings go back to daily report synthesis. If the
report audit finds an upstream synthesis or evidence problem, the workflow returns to that
producing phase and repeats the affected enabled audits.

For each material report claim, check:

- The claim has citations that are inspectable through the project session indexes.
- The cited evidence semantically supports the claim.
- The claim does not overstate implementation, completion, validation, deployment, or acceptance.
- The report does not hide blockers, contradictions, or verification gaps.
- The report does not hide no-material, interrupted, failed, or clarification-only chains when they
  materially affect evidence trust, engagement review, or team learning.
- Engagement judgments are based on observable behavior.
- Missing evidence is not treated as negative evidence.
- Important evidence cards or indexed sessions without cards are not ignored when they change the
  report's conclusion.
- Audited project synthesis accounts for every indexed session and evidence chain, or records an
  excluded-with-reason disposition.

## Suspicious Claim Words

Audit these words carefully because they often hide unsupported completion claims:

```text
fixed
completed
verified
passed
implemented
fully migrated
all done
no issue found
deployed
accepted
```

Require direct evidence such as command output, test result, file diff, generated artifact,
rendered inspection, explicit user confirmation, or independent review. If that evidence is absent,
revise the claim to preserve uncertainty.

## Verdicts

Allowed verdicts:

- `Pass`: claims are supported; only trivial wording issues remain.
- `Pass with minor issues`: mostly supported, with small clarifications needed.
- `Needs revision`: unsupported claims, hidden blockers, incomplete evidence accounting, wrong
  grouping, or unclear verification require edits.
- `Reject`: evidence extraction, project synthesis, or report synthesis is materially unreliable,
  fabricated, or unfair.

## Finding Contract

```json
{
  "finding_id": "audit-F0001",
  "severity": "critical | major | minor",
  "category": "unsupported_claim | exaggerated_progress | unfair_judgment | missing_evidence | ignored_evidence | hidden_blocker | wrong_verification | wrong_grouping | missing_accounting | invalid_dummy_item | unclear_wording",
  "claim_or_section": "Report section, project work item, or evidence chain",
  "problem": "What is wrong and why it matters.",
  "evidence_refs": [
    {"project_key": "ReportGenerator-e6ff7eeda632", "session_ref": "S0001", "chain_ref": "E0001"}
  ],
  "verification": {
    "target": "outcomes[0]",
    "status": "partially_verified",
    "reasoning": "The outcome is visible, but the session lacks independent review or command output.",
    "citations": [
      {"project": "ReportGenerator-e6ff7eeda632", "session": "S0001", "lines": "129-170"}
    ]
  },
  "required_fix": "Concrete edit or extraction, synthesis, or report repair."
}
```

For report findings, include the report citation being challenged whenever possible. For project
audit findings, include the affected work item, evidence accounting row, missing card, or missing
chain whenever possible.

## Revision Loop

Recommended control flow when all audit checkpoints are enabled:

1. Extract evidence chains through MCP tools.
2. Run the evidence audit checkpoint.
3. If evidence audit fails, repair the card through MCP tools or rerun extraction, then re-audit.
4. Synthesize project work items with evidence accounting.
5. Run the project audit checkpoint.
6. If project audit fails, revise project synthesis, or return to evidence extraction when the
   findings expose missing or unreliable evidence cards, then re-audit.
7. Compose `daily-report.json` from project synthesis outputs, including project audit findings
   when that checkpoint is enabled, then render `report.md`.
8. Run the report audit checkpoint.
9. If report audit fails, revise the report, or return to project synthesis or evidence extraction
   when the finding belongs upstream, then re-audit the affected artifacts.
10. Run deterministic validation after enabled audit checkpoints pass or have only accepted minor
    issues.

When a checkpoint is disabled, the next artifact-producing phase may proceed from the latest
durable artifact. The report should preserve the resulting uncertainty instead of implying that
semantic audit evidence exists.

## Prompt Template

```text
You are the Prompt Diary generation auditor.

You will receive a prepared workspace and one or more generation artifacts:
- per-session evidence cards
- project synthesis outputs
- draft daily-report.json
- draft report.md

Your job is to check semantic support, verification status and reasoning, evidence accounting,
grouping, fairness, and overstatement for the enabled checkpoint's artifact.

Rules:
1. Check every material outcome claim in the artifact under audit.
2. For evidence audit, assign or normalize verification status and reasoning for extracted outcomes
   and relevant terminal states.
3. For project audit, check grouping correctness, evidence accounting completeness,
   dummy/non-material work items, and missed evidence cards or chains.
4. For report audit, check whether every material report claim is supported by cited evidence and
   project synthesis, including project audit findings when available.
5. Do not treat agent self-report as verification.
6. Do not treat missing evidence as evidence of failure.
7. Do not infer personality, motivation, laziness, or hidden intent.
8. Produce precise findings and required fixes for the producing phase. Do not rewrite the whole
   artifact unless necessary.

Output:
- audit mode
- verdict
- overall summary
- verification status and reasoning updates, when applicable
- findings grouped by category
- required revisions
- whether the next phase may consume the audited artifact after revision
```

## Quality Checklist

Before accepting an audit:

- The audit mode and artifact under review are explicit.
- The audit does not introduce unsupported claims.
- The verdict follows from the findings.
