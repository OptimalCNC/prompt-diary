# Daily Report Synthesis

Daily report synthesis is the final report-producing generation phase. It turns project summaries
and work items into `report.md`, where the four [product purposes](../product.md#purposes) must
converge from one evidence base: work communication, evidence trust, engagement review, and team
learning.

Daily report synthesis starts from the prepared workspace and generation artifacts. It must not
rediscover raw sessions outside the prepared workspace.

## Inputs And Outputs

Inputs:

- `metadata.json`
- `projects/*/project.json`
- `projects/*/sessions.index.jsonl`
- per-session evidence cards under `projects/*/evidence/`
- project synthesis outputs

Output:

- `report.md` in the prepared workspace root

Daily report synthesis owns the internal checks needed before returning `report.md`. A report that
misses required sections, uses invalid citations, hides required evidence-quality limits, or
includes forbidden high-risk content is a daily report synthesis bug.

## Report Contract

The report structure and section intent are defined in [Report Generation](./index.md). Daily
report synthesis owns the content of those sections.

Every concrete work claim in claim-bearing sections must cite lines inside the indexed target span
using the report citation format from the
[Evidence Contract](./evidence-contract.md#session-evidence-cards).

`Evidence Gaps` may also refer to `metadata.json` or session indexes in prose. It uses normal
session-line citations when it cites session content.

## Daily Synthesizer Prompt

Prompt source: `src/prompt_diary/prompts/daily-synthesizer.md` — loaded at runtime by the
orchestrator.

---

{{#include ../../../src/prompt_diary/prompts/daily-synthesizer.md}}
