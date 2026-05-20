# Subagent Evidence Report Template

Each subagent must write its report as a Markdown file using this structure.
Assertions without evidence are not accepted. Evidence may be file paths with line
references, command outputs, test results, or explicit observations from inspected
fixtures. Do not paste entire session transcripts.

```markdown
# <Role> Evidence Report

## Scope
- Assigned task:
- Files or areas inspected:
- Files changed, if any:

## Requirements Checked
| Requirement | Evidence | Result |
| --- | --- | --- |
| <specific requirement> | <file:line, command, or observation> | Pass/Fail/Blocked |

## Findings
| Severity | Finding | Evidence | Suggested action |
| --- | --- | --- | --- |
| <High/Medium/Low> | <specific issue or "None"> | <supporting evidence> | <next action> |

## Verification
| Command or inspection | Evidence summary | Result |
| --- | --- | --- |
| `<command>` | <key output or reason not run> | Pass/Fail/Blocked |

## Constraints Compliance
| Constraint | Evidence | Result |
| --- | --- | --- |
| Role boundary respected | <what was/was not edited> | Pass/Fail |
| Evidence-backed report | <where evidence is shown> | Pass/Fail |

## Residual Risk
- <risk, gap, or "None identified">
```
