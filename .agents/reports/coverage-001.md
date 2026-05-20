# Coverage Reviewer Evidence Report

## Scope
- Assigned task: Review current coverage for `prompt-diary generate` delivery and identify test gaps required for 100% package line coverage.
- Files or areas inspected: `README.md`, `docs/src/prompt-diary-tool-design.md`, `.agents/subagent-evidence-report-template.md`, `pyproject.toml`, `src/prompt_diary/{api.py,cli.py,report.py,targets.py,workspace.py}`, and current tests under `tests/`.
- Files changed, if any: Intentional edit is this report only. The required coverage run writes `.coverage`; `.coverage` was already untracked in the initial `git status --short` evidence.

## Requirements Checked
| Requirement | Evidence | Result |
| --- | --- | --- |
| Read `README.md` before changing repository | Inspected `README.md`; it states the project uses `uv`, Python `>=3.10`, and coverage commands `uv run coverage run -m pytest` plus `uv run coverage report`. | Pass |
| Read `docs/src/prompt-diary-tool-design.md` | Inspected the design doc. Key checked requirements include `prompt-diary generate` auto-preparing missing workspaces, reusing existing workspaces with a refresh message, writing `report.md`, and validating report structure, citations, fallback bullets, word count, and sensitive content. | Pass |
| Do not edit production code or tests | No production or test file edits were made by this reviewer. Initial status already showed dirty package/test files and untracked `.coverage`. | Pass |
| Use configured coverage commands if feasible | Initial sandboxed `uv run coverage run -m pytest` failed because uv could not write `/home/huwei/.cache/uv`; reran with approval so uv could use its cache. | Pass |
| Require 100% line coverage for package code | `pyproject.toml` has `[tool.coverage.report] fail_under = 100` and `show_missing = true`. `uv run coverage report` failed with `TOTAL 84%`. | Fail |

## Findings
| Severity | Finding | Evidence | Suggested action |
| --- | --- | --- | --- |
| High | Package coverage is below the required 100% gate. | `uv run coverage report` output: `TOTAL 1007 159 84%` and `Coverage failure: total of 84 is less than fail-under=100`. | Add focused tests for the uncovered behavior below, then rerun coverage until `TOTAL 100%`. |
| High | `report.py` has the largest uncovered surface: writer failure handling, fallback variants, prompt edge cases, validation failures, malformed workspace metadata/index data, citation errors, sensitive-content checks, and trimming/error-message helpers. | Missing lines: `src/prompt_diary/report.py:117`, `132-133`, `135`, `144-145`, `223`, `249`, `256`, `289`, `327`, `332`, `356`, `364`, `438`, `449`, `452`, `467`, `474`, `486`, `490`, `497`, `506-507`, `510-513`, `520-521`, `528-531`, `534-535`, `551`, `553`, `560`, `574`, `582`, `598`, `607`, `622`, `634-636`, `638`, `646`, `659`, `672`, `685`, `698-701`, `705`, `709`, `713`, `717`, `736`, `756`, `765`, `772`, `785`, `792-793`, `800`, `804-806`. | Prioritize tests for `CommandReportWriter.from_environment` with a non-empty string that splits empty, command `OSError`, command non-zero with long output, `EmptyFallbackReportWriter`, `write_deterministic_report`, missing/no-session projects in prompts, missing `report.md`, partial fallback note, all validator failure modes, citation unknown/unordered/not-at-end/no-valid-citation, sensitive content, malformed JSON/object/field/index errors, and line-span/session-path failures. |
| High | `workspace.py` lacks coverage for preparation edge cases that can affect deterministic workspace boundaries and audit evidence. | Missing lines: `src/prompt_diary/workspace.py:113`, `158`, `178`, `180`, `206`, `218`, `221-222`, `224`, `246`, `251`, `271-274`, `290`, `292`, `311-312`, `368`, `418`, `437-439`, `450-451`, `478`, `483`, `574`, `624`, `654`, `658`, `688-689`, `691`, `700-702`, `714-715`, `717`, `732`, `744`, `749`, `758`, `767`. | Add tests for `force=True` workspace/audit removal, `audit_path_for_target`, default source path handling for blank/env/default paths, existing workspace with missing/invalid metadata and no projects dir, source root as a single `.jsonl` file and missing source root, malformed/scalar JSONL lines, non-monotonic timestamps and warnings, Claude subagent directly under `subagents/`, missing/fallback project roots, nonexistent canonical roots, destination filename collision, source spec fallback root in audit, invalid/naive timestamps, object/string helper fallbacks, and naive `prepared_at`. |
| Medium | `targets.py` is missing timezone/date error and default-discovery coverage. | Missing lines: `src/prompt_diary/targets.py:64-67`, `73-74`, `78-95`, `99-103`, `110`, `121-122`, `134`, `138`. | Add tests for unknown timezone, invalid date format, aware `now` conversion from a different timezone, default timezone fallback order including blank/colon-prefixed env values, and system timezone discovery paths. System discovery may need monkeypatching or a small injection seam to avoid depending on host `/etc` files. |
| Medium | `api.py` misses generation error paths and naive timestamp handling. | Missing lines: `src/prompt_diary/api.py:92`, `97`, `114`, `123-124`, `128`. | Add tests for a writer returning any path other than `workspace/report.md`, a writer producing an invalid report so `ReportValidationError` includes validation details, and a naive `now` value being localized to the target timezone for `generated_at`. |
| Medium | `cli.py` only has help/version and happy-path CLI coverage; error exits and the console entrypoint are uncovered. | Missing lines: `src/prompt_diary/cli.py:57-58`, `73-74`, `80-81`, `85`. Existing tests in `tests/test_cli.py` cover help/version only; e2e CLI tests cover success paths. | Add CLI tests for `prepare` errors, `generate` errors, stderr text and exit code `2`, plus a direct `main()`/console-entry test. |
| Medium | Some uncovered report validation branches look like invariant or unreachable code through the public validator. | `src/prompt_diary/report.py:510-511` appears unreachable after `if not section_bullets: continue`, because a non-empty bullet list with no `non_fallback` necessarily contains the fallback. `src/prompt_diary/report.py:606-607` also appears unreachable through normal workspace loading because duplicate project keys and duplicate session refs are rejected earlier. | Confirm with the lead whether these should be covered by private-unit tests, removed/simplified, or excluded with explicit coverage pragmas. Tests alone may not be the right fix for these lines. |

## Prioritized Test-Gap List
| Priority | Gap | Likely missing behavioral tests |
| --- | --- | --- |
| 1 | Report validation contract matrix | Missing `report.md`; bad header/status/window/generated lines; partial report without note; missing/out-of-order sections; >600 words; empty required sections; fallback mixed with claims; claim bullet missing citation at end; invalid citation shape; unknown project/session; unordered citation lines; cited lines outside index; sensitive content. |
| 2 | Report workspace/index load robustness | Missing `metadata.json`; invalid JSON; JSON root not object; missing nested metadata objects; missing string/int fields; missing `projects`; non-directory children; missing `sessions.index.jsonl`; blank index lines; invalid session paths; missing copied session; target span beyond copied file. |
| 3 | Report writer boundary | Environment command parsing edge cases; external command start failure; external command non-zero with stderr/stdout and long output trimming; deterministic fallback writer entrypoints. |
| 4 | Workspace preparation edge cases | Existing workspace reuse/counting with no projects; force refresh removal; source discovery from one file or missing root; malformed/scalar/untimestamped records; non-monotonic timestamps; Codex payload timestamp fallback; Claude subagent id edge; unknown project identity and fallback root; filename collision. |
| 5 | Target and API/CLI error paths | Invalid date/timezone, future/current/default date variants around timezone conversion, API wrong writer path and validation failure, CLI `prepare`/`generate` error exits, console entrypoint line. |

## Verification
| Command or inspection | Evidence summary | Result |
| --- | --- | --- |
| `sed -n '1,240p' README.md` | Confirmed required `uv` workflows, Python `>=3.10`, and coverage commands. | Pass |
| `sed -n '1,620p' docs/src/prompt-diary-tool-design.md` | Confirmed `prepare`/`generate` workflow, workspace structure, prompt/report contracts, citation rules, and validation requirements. | Pass |
| `sed -n '1,220p' .agents/subagent-evidence-report-template.md` | Confirmed required report structure used here. | Pass |
| `git status --short` before coverage/report write | Existing dirty state included `M README.md`, `M pyproject.toml`, `M src/prompt_diary/__init__.py`, `M src/prompt_diary/cli.py`, `M uv.lock`, untracked `.agents/`, `.coverage`, new package modules, and tests. | Pass |
| `uv run coverage run -m pytest` | First sandboxed run failed: `Could not acquire lock ... Read-only file system ... /home/huwei/.cache/uv/...`. Escalated rerun passed: `collected 26 items` and `26 passed in 0.49s`. | Pass |
| `uv run coverage report` | Failed as expected under the 100% gate. File totals: `api.py 87%`, `cli.py 82%`, `report.py 82%`, `targets.py 63%`, `workspace.py 88%`, `TOTAL 84%`. | Fail |
| Source/test inspection | Inspected numbered source and current tests to map uncovered lines to behavioral gaps. Existing tests cover happy-path prepare/generate, report prompt basics, several citation/index validations, and target basics, but not the edge cases listed above. | Pass |

## Constraints Compliance
| Constraint | Evidence | Result |
| --- | --- | --- |
| Role boundary respected | No production code or tests were edited. Only `.agents/reports/coverage-001.md` was intentionally added. | Pass |
| Evidence-backed report | Includes command output summaries, exact uncovered file/line ranges from `coverage report`, and inspected source/test observations. | Pass |

## Residual Risk
- The coverage run refreshes `.coverage`; it was already untracked before this review, but its contents may now reflect the current rerun.
- A few uncovered lines appear to encode invariants that public behavior may not reach. Reaching 100% may require either private-unit coverage, code simplification, or explicit coverage exclusions approved by the lead.
