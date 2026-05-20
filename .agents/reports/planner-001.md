# Planner Evidence Report

## Scope
- Assigned task: Produce a small, tractable implementation plan for the next slice of `prompt-diary generate`; do not edit production code or tests.
- Files or areas inspected: `AGENTS.md`, `README.md`, `docs/src/prompt-diary-tool-design.md`, `src/prompt_diary/cli.py`, `tests/test_cli.py`, `pyproject.toml`, and `.agents/subagent-evidence-report-template.md`.
- Files changed, if any: `.agents/reports/planner-001.md` only.

## Requirements Checked
| Requirement | Evidence | Result |
| --- | --- | --- |
| Read and comply with repository instructions before planning | `AGENTS.md:3-10` requires reading `README.md`, uv usage, Python 3.10+, basedpyright, ruff check, and ruff format checks. `README.md:25-28` says development uses uv. | Pass |
| Keep Python compatibility at 3.10+ | `README.md:5-6` says the tool targets Python 3.10 and newer. `pyproject.toml:6` sets `requires-python = ">=3.10"`. | Pass |
| Use uv for commands and workflow expectations | `README.md:27-28` says uv owns environment, dependency, build, and release workflows. `README.md:96-98`, `README.md:116-119`, and `README.md:124-132` list uv-based checks. | Pass |
| Read the prompt diary design | `docs/src/prompt-diary-tool-design.md:29-49` defines the CLI surface and `generate` behavior. | Pass |
| Follow evidence-report structure | `.agents/subagent-evidence-report-template.md:8-39` defines the required report sections. | Pass |
| Do not edit production code or tests | `src/prompt_diary/cli.py` and `tests/test_cli.py` were inspected but not modified; command `git status --short src tests` returned no output. | Pass |

## Findings
| Severity | Finding | Evidence | Suggested action |
| --- | --- | --- | --- |
| High | `prompt-diary generate` has no implementation behind the Typer command. | `src/prompt_diary/cli.py:51-59` defines `generate`, and `src/prompt_diary/cli.py:62-64` routes commands to `_fail_not_implemented` with exit code 2. | Developer should replace the placeholder with a small orchestration layer that can be unit-tested without a real model call. |
| High | `generate` must reuse or prepare a workspace before synthesis, then write and validate `report.md`. | `docs/src/prompt-diary-tool-design.md:46-49` states existing workspaces are reused, missing workspaces are prepared first, and `report.md` is validated before success. | Developer should make workspace existence and validation explicit state transitions in the generate path. |
| Medium | The report-writing prompt and output validator are core to correctness, not polish. | `docs/src/prompt-diary-tool-design.md:270-285` defines prompt instructions, and `docs/src/prompt-diary-tool-design.md:349-365` defines validation checks. | Developer should build prompt construction and validation before adding source-discovery breadth. |
| Medium | Current tests do not cover generation behavior. | `tests/test_cli.py:9-25` covers help and version only. | Developer should add focused unit tests for deterministic helpers; QA should add end-to-end CLI/library behavior tests after the slice lands. |
| Low | The current package has only Typer as a runtime dependency, so model execution is not represented in project metadata. | `pyproject.toml:7-9` lists only `typer>=0.25.1` under runtime dependencies. | Developer should use an injectable runner boundary in this slice, then choose the real model integration in a later slice if needed. |

## Next Slice Implementation Plan
Scope: implement the deterministic `generate` skeleton against the prepared-workspace contract, using an injectable report-writing runner. Leave broad session source discovery and adapter work out of this slice because preparation owns discovery, timestamp parsing, copying, and indexes (`docs/src/prompt-diary-tool-design.md:70-73`, `docs/src/prompt-diary-tool-design.md:193-240`).

1. Developer: add typed target-date resolution helpers in `src/` and unit tests for default yesterday, `--today`, `--date`, mutual exclusion, future-date rejection, `final` versus `partial`, and local/UTC half-open windows. Evidence: `docs/src/prompt-diary-tool-design.md:38-45` and `docs/src/prompt-diary-tool-design.md:101-155`.
2. Developer: add workspace loader helpers for `.reports/work/<report_date>/metadata.json`, `projects/*/project.json`, and each `sessions.index.jsonl`, with path-containment checks for session paths. Evidence: `docs/src/prompt-diary-tool-design.md:74-93`, `docs/src/prompt-diary-tool-design.md:127-155`, and `docs/src/prompt-diary-tool-design.md:206-240`.
3. Developer: add a prompt builder plus injectable runner interface. Unit tests should assert that `generated_at`, metadata-first reading, project/index enumeration, untrusted-session handling, citation requirements, and `report.md` creation instructions are present. Evidence: `docs/src/prompt-diary-tool-design.md:270-285`.
4. Developer: add `report.md` validation and wire `generate` to call it after the runner. Unit tests should cover required section order, header values, word limit, fallback bullets, citation parsing, citation span containment, and actionable failures. Evidence: `docs/src/prompt-diary-tool-design.md:286-365`.
5. Developer: wire Typer `generate` to resolve the target, reuse an existing workspace with the specified informational message, call the prepare boundary when missing, run the injected writer, validate, and exit nonzero on actionable validation errors. Evidence: `docs/src/prompt-diary-tool-design.md:46-49`.
6. QA: own end-to-end behavior tests for both library and CLI after Developer's unit-tested slice exists: existing prepared workspace success with a fake runner, missing workspace behavior through the prepare boundary, and validation failure output. Evidence that coverage is currently missing: `tests/test_cli.py:9-25`.
7. Coverage and reviewers: inspect coverage reports and review findings only; do not patch code. The role-boundary report requirement is tracked by `.agents/subagent-evidence-report-template.md:31-35`.

## Verification
| Command or inspection | Evidence summary | Result |
| --- | --- | --- |
| `nl -ba README.md` | Confirmed uv development workflow, Python 3.10+ target, and required checks at `README.md:5-6`, `README.md:27-28`, `README.md:96-98`, `README.md:116-119`, and `README.md:124-132`. | Pass |
| `nl -ba docs/src/prompt-diary-tool-design.md` | Confirmed `generate` contract, workspace contract, prompt contract, report shape, citation rules, and validation rules at cited design lines above. | Pass |
| `nl -ba src/prompt_diary/cli.py` | Confirmed `generate` is still routed to `_fail_not_implemented` at `src/prompt_diary/cli.py:51-64`. | Pass |
| `nl -ba tests/test_cli.py` | Confirmed current tests only cover help and version at `tests/test_cli.py:9-25`. | Pass |
| `git status --short src tests` | Returned no output, confirming this planner did not edit `src/` or `tests/`. | Pass |

## Constraints Compliance
| Constraint | Evidence | Result |
| --- | --- | --- |
| Role boundary respected | Developer owns `src/` and unit tests only; QA owns end-to-end behavior tests for library and CLI; coverage and reviewers do not patch code. This report changed only `.agents/reports/planner-001.md`; role-boundary reporting is required by `.agents/subagent-evidence-report-template.md:31-35`. | Pass |
| Evidence-backed report | Requirements, findings, plan, and verification cite repository files with line numbers or command outputs. Template evidence requirement is at `.agents/subagent-evidence-report-template.md:3-6`. | Pass |

## Residual Risk
- The design specifies the prompt contract but not the concrete model provider or invocation mechanism; this slice should keep that boundary injectable until a provider decision is made. Evidence: `docs/src/prompt-diary-tool-design.md:270-285` specifies prompt contents, while `pyproject.toml:7-9` has no runtime model dependency.
- Full correctness of `generate` depends on prepared workspace quality, but preparation owns discovery, timestamp parsing, copying, and span indexes. Evidence: `docs/src/prompt-diary-tool-design.md:70-73` and `docs/src/prompt-diary-tool-design.md:193-240`.
