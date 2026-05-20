# Architecture Reviewer Evidence Report

## Scope
- Assigned task: Review current implementation architecture for delivering `prompt-diary generate` as designed; do not patch code or tests.
- Files or areas inspected: `README.md`, `docs/src/prompt-diary-tool-design.md`, `.agents/subagent-evidence-report-template.md`, `pyproject.toml`, `src/prompt_diary/`, `tests/`, and prior `.agents/reports/` context.
- Files changed, if any: `.agents/reports/architecture-review-001.md` only.

## Requirements Checked
| Requirement | Evidence | Result |
| --- | --- | --- |
| Read repository instructions and README before changing repository files | `AGENTS.md` was provided in the task and requires reading `README.md`; `README.md:25-28` says development uses `uv`; `README.md:85-131` lists type, lint, test, and build commands. | Pass |
| Read the prompt diary tool design | `docs/src/prompt-diary-tool-design.md:29-49` defines CLI workflow; `docs/src/prompt-diary-tool-design.md:70-73` defines preparation ownership; `docs/src/prompt-diary-tool-design.md:270-365` defines prompt, report, citation, and validation contracts. | Pass |
| Review architecture only; do not patch code or tests | `git status --short` before this report showed modified/untracked implementation files from existing work, but this review did not edit `src/` or `tests/`; only this report file was added. | Pass |
| CLI layer stays thin and maps to workflow | `src/prompt_diary/cli.py:41-60` delegates `prepare` to `prepare_prompt_diary`; `src/prompt_diary/cli.py:63-76` delegates `generate` to `generate_prompt_diary`; scripts expose `prompt-diary` and `report` at `pyproject.toml:11-13`. | Pass |
| Library layer owns workflow orchestration | `src/prompt_diary/api.py:21-39` resolves targets and prepares workspaces; `src/prompt_diary/api.py:42-84` resolves target, reuses or prepares workspace, writes report, validates report, and returns structured results. | Pass |
| Preparation owns discovery, timestamp parsing, copying, indexing, workspace layout, and audit | `src/prompt_diary/workspace.py:96-140` prepares/reuses workspace; `src/prompt_diary/workspace.py:207-248` scans JSONL and parses timestamped records; `src/prompt_diary/workspace.py:392-407` writes metadata, project workspaces, and audit; design responsibility is `docs/src/prompt-diary-tool-design.md:70-73`. | Pass |
| Generation aligns with prompt/model synthesis design | Design requires running a report-writing model in the workspace at `docs/src/prompt-diary-tool-design.md:48` and prompt instructions at `docs/src/prompt-diary-tool-design.md:270-285`; implementation calls `write_deterministic_report` at `src/prompt_diary/api.py:71-73` and renders fixed bullets at `src/prompt_diary/report.py:118-146`. | Fail |
| Validation structurally enforces the evidence contract | Design requires each citation project/session to resolve uniquely and lines to be contained by target span at `docs/src/prompt-diary-tool-design.md:263-266` and `docs/src/prompt-diary-tool-design.md:353-360`; implementation validates line containment at `src/prompt_diary/report.py:351-370`. | Partial |

## Findings
| Severity | Finding | Evidence | Suggested action |
| --- | --- | --- | --- |
| High | `prompt-diary generate` does not run the designed report-writing model or construct the required prompt; it writes a deterministic placeholder report from indexes only. | Design says `generate` "runs the report-writing model in that workspace" at `docs/src/prompt-diary-tool-design.md:48`; prompt requirements are listed at `docs/src/prompt-diary-tool-design.md:270-285`. Current orchestration calls `write_deterministic_report` then `validate_report` at `src/prompt_diary/api.py:71-73`. `write_deterministic_report` writes `_render_report` output at `src/prompt_diary/report.py:83-91`; `_render_report` emits summary/index bullets plus fallback sections at `src/prompt_diary/report.py:135-145`; `_summary_bullets` only claims indexed evidence availability at `src/prompt_diary/report.py:153-164`. | Introduce an explicit generation boundary in the library layer: build the prompt with `generated_at`, metadata/index reading instructions, untrusted-session warnings, citation rules, and report shape; execute an injectable model/runner in the workspace; then validate `report.md`. Keep the CLI unchanged except for delegating to this library API. |
| Medium | Existing workspace reuse does not verify that the workspace metadata matches the requested target timezone/window/status before generation. | A target includes `timezone`, `status`, local window, and UTC window at `src/prompt_diary/models.py:27-35`; the workspace path uses only `report_date` at `src/prompt_diary/models.py:36-39` and `src/prompt_diary/workspace.py:143-149`. `generate_prompt_diary` reuses any existing date workspace at `src/prompt_diary/api.py:52-60`; `_existing_prepare_result` counts files and returns without reading `metadata.json` at `src/prompt_diary/workspace.py:169-184`. The design makes time windows authoritative at `docs/src/prompt-diary-tool-design.md:101-113`. | On reuse, load `metadata.json` and compare `report_date`, `timezone`, `status`, `report_window_local`, and `report_window_utc` to the resolved target. If they differ, return an actionable error requiring `prepare --force` or include timezone/window in workspace identity. |
| Medium | Citation validation can accept ambiguous project/session references because duplicate `project_key` or duplicate `session_ref` rows are silently collapsed. | Evidence contract requires the cited project to resolve to one project workspace and session to one row at `docs/src/prompt-diary-tool-design.md:263-266`. `_load_projects` trusts each `project.json` key without checking it matches the directory or is unique at `src/prompt_diary/report.py:186-205`. `_session_index` builds a dict keyed by `(project.key, row.session_ref)` and overwrites duplicates at `src/prompt_diary/report.py:411-416`. | Make the workspace/report loader reject duplicate project keys, project key/directory mismatches, duplicate session refs within a project, and duplicate `(project, session)` citation targets before validating report citations. |
| Medium | Claude Code subagent session identity from `subagents/` paths is not modeled, so session references can diverge from the source adapter contract. | Design states Claude Code session id is the filename stem with subagent id from path when under `subagents/` at `docs/src/prompt-diary-tool-design.md:197-200`. Current parsing falls back to `source_path.stem` at `src/prompt_diary/workspace.py:253`; non-Codex metadata handling records only `cwd` at `src/prompt_diary/workspace.py:309-315`; no path-based subagent identity is derived before index rows are written at `src/prompt_diary/workspace.py:492-500`. | Add a source-adapter helper for Claude Code that derives a deterministic `source_session_id` including subagent path context when applicable, and cover it with a focused workspace test. |
| Low | The current report tests encode the placeholder generator as expected behavior, which may hide the design gap during future review. | `tests/test_report.py:12-27` asserts `write_deterministic_report` validates and includes "Indexed target-window work evidence was found"; design instead requires synthesis over copied session evidence and a prompt/model contract at `docs/src/prompt-diary-tool-design.md:270-285`. | Reframe deterministic report tests around validation and empty-evidence fallback behavior; add prompt-builder/runner tests that fail unless generation actually uses the prepared workspace contract. |

## Verification
| Command or inspection | Evidence summary | Result |
| --- | --- | --- |
| `nl -ba README.md` | Confirmed `uv` workflow and required development checks at `README.md:25-28`, `README.md:85-131`; Python 3.10+ target at `README.md:5-6`. | Pass |
| `nl -ba docs/src/prompt-diary-tool-design.md` | Confirmed workflow, workspace ownership, prompt contract, citation contract, report shape, and validation requirements at cited lines. | Pass |
| `nl -ba src/prompt_diary/cli.py src/prompt_diary/api.py src/prompt_diary/workspace.py src/prompt_diary/report.py` | Confirmed thin CLI, preparation responsibilities, deterministic placeholder generation, and validation implementation at cited lines. | Pass |
| `uv run pytest` | First sandboxed run failed with `Could not acquire lock ... Read-only file system ... /home/huwei/.cache/uv/...`; rerun with escalation passed: `10 passed in 0.07s`. | Pass |
| `uv run ruff check` | Output: `All checks passed!` | Pass |
| `uv run ruff format --check` | Output: `13 files already formatted` | Pass |
| `uv run basedpyright` | Output: `0 errors, 0 warnings, 0 notes` | Pass |

## Constraints Compliance
| Constraint | Evidence | Result |
| --- | --- | --- |
| Role boundary respected | This architecture review inspected implementation and tests but did not modify code or tests; only `.agents/reports/architecture-review-001.md` was added. | Pass |
| Evidence-backed report | Requirements and findings cite file paths with line numbers and command outputs. | Pass |
| Report template followed | This file uses the required sections from `.agents/subagent-evidence-report-template.md`: Scope, Requirements Checked, Findings, Verification, Constraints Compliance, and Residual Risk. | Pass |

## Residual Risk
- The design does not specify a concrete model provider or invocation mechanism; the safest next architecture step is an injectable runner plus prompt builder rather than hard-coding a provider.
- Validation checks structural citation boundaries but still cannot prove evidential support; that remains a report-generation quality boundary as stated in `docs/src/prompt-diary-tool-design.md:363`.
