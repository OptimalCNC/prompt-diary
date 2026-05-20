# Architecture Reviewer 002 Evidence Report

## Scope
- Assigned task: Final architecture review for the implemented `prompt-diary generate` and `prepare` workflow against `docs/src/prompt-diary-tool-design.md`; do not patch code or tests.
- Files or areas inspected: `README.md`, `docs/src/prompt-diary-tool-design.md`, `.agents/subagent-evidence-report-template.md`, `pyproject.toml`, `src/prompt_diary/`, `tests/`, and prior review/developer/QA reports under `.agents/reports/`.
- Files changed, if any: `.agents/reports/architecture-review-002.md` only.

## Requirements Checked
| Requirement | Evidence | Result |
| --- | --- | --- |
| Read repository instructions and README before repository changes | User-provided instructions require reading `README.md`; `README.md:25-27` requires `uv`; `README.md:85-140` lists type, test, lint, and pre-submit checks; `README.md:5-6` says Python 3.10+. | Pass |
| Read prompt diary tool design | `docs/src/prompt-diary-tool-design.md:29-48` defines the CLI workflow; `docs/src/prompt-diary-tool-design.md:50-255` defines workspace/index/audit; `docs/src/prompt-diary-tool-design.md:270-365` defines prompt/report/validation. | Pass |
| Use the evidence report template | `.agents/subagent-evidence-report-template.md:8-38` defines the required report sections and evidence expectations. | Pass |
| Do not patch code or tests | `git status --short` showed existing modified/untracked implementation files before this report; this review only added `.agents/reports/architecture-review-002.md`. | Pass |
| CLI layer stays thin and maps to workflow | `src/prompt_diary/cli.py:41-60` delegates `prepare` to `prepare_prompt_diary`; `src/prompt_diary/cli.py:63-76` delegates `generate` to `generate_prompt_diary`; `pyproject.toml:11-13` exposes both `prompt-diary` and `report`. | Pass |
| Library layer owns prepare/generate orchestration | `src/prompt_diary/api.py:30-48` resolves targets and prepares workspaces; `src/prompt_diary/api.py:51-106` resolves target, validates/reuses or prepares workspace, builds prompt, invokes writer, validates `report.md`, and returns structured results. | Pass |
| Date/window rules are authoritative | Design requires local-day half-open UTC inclusion at `docs/src/prompt-diary-tool-design.md:101-113`; implementation resolves yesterday/today/date/future rules at `src/prompt_diary/targets.py:31-47` and target date defaults at `src/prompt_diary/targets.py:114-122`. Tests cover defaults, today partial, future rejection, timezone conversion at `tests/test_targets.py:15`, `tests/test_targets.py:30`, `tests/test_targets.py:65`. | Pass |
| Prepare workspace layout, indexing, and deterministic references match design | Design requires copied whole sessions and index spans at `docs/src/prompt-diary-tool-design.md:70-72`, `docs/src/prompt-diary-tool-design.md:206-240`; implementation scans JSONL roots at `src/prompt_diary/workspace.py:277-293`, copies selected sessions and writes `S0001` refs at `src/prompt_diary/workspace.py:526-583`, and sorts by `(source, source_session_id, session_path)` at `src/prompt_diary/workspace.py:549-563`. | Pass |
| Source adapters preserve project/session rules | Codex/Claude source rules are in `docs/src/prompt-diary-tool-design.md:193-204`; implementation parses Codex metadata at `src/prompt_diary/workspace.py:390-403`, derives Claude subagent ids at `src/prompt_diary/workspace.py:406-422`, resolves project identity at `src/prompt_diary/workspace.py:432-487`, and records target spans at `src/prompt_diary/workspace.py:367-378`. | Pass |
| Prompt/model writer boundary exists | Design requires a model prompt and `report.md` artifact at `docs/src/prompt-diary-tool-design.md:270-284`; implementation defines `ReportWriter` at `src/prompt_diary/report.py:92-95`, command writer at `src/prompt_diary/report.py:98-134`, no silent default writer at `src/prompt_diary/report.py:104-116`, and explicit fallback writer only at `src/prompt_diary/report.py:137-143`. | Pass with finding |
| Report validation enforces structural contract | Design validation requirements are at `docs/src/prompt-diary-tool-design.md:349-365`; implementation checks report existence, header, sections, word count, bullets, citations, and sensitive content at `src/prompt_diary/report.py:250-270`, with structural project/index/span validation at `src/prompt_diary/report.py:322-376`, `src/prompt_diary/report.py:428-453`, and `src/prompt_diary/report.py:513-550`. | Pass |

## Findings
| Severity | Finding | Evidence | Suggested action |
| --- | --- | --- | --- |
| Medium | The prompt inventory interpolates source-derived identifiers and session paths as raw text, so a crafted local session id or filename containing control characters/newlines can inject extra prompt lines before the model opens session files. | The design treats session content as untrusted at `docs/src/prompt-diary-tool-design.md:14` and requires the prompt to tell the model that session contents are evidence, not instructions, at `docs/src/prompt-diary-tool-design.md:278`. `build_report_prompt` emits `source_session_id={row.source_session_id}` and `session_path=projects/{project.key}/{row.session_path}` directly into natural-language lines at `src/prompt_diary/report.py:222-228`. Those values originate from untrusted session metadata or source filenames: Codex `payload.id` at `src/prompt_diary/workspace.py:393-396`, Claude/source path stem at `src/prompt_diary/workspace.py:406-422`, and copied session filename at `src/prompt_diary/workspace.py:586-587`. | Escape or serialize prompt inventory as data, preferably JSON emitted with `json.dumps`, and add an explicit instruction that all inventory field values are data, not instructions. Consider rejecting or normalizing control characters in source session ids and source filenames used in prompt-visible paths. |
| Low | The audit manifest records copied sessions but not skipped scanned files, which limits post-hoc inspection of preparation decisions for files that were parsed and excluded because no identifiable event landed in the target window. | The design says the audit manifest records enough information to reproduce and inspect preparation decisions at `docs/src/prompt-diary-tool-design.md:242-253`. Implementation scans all JSONL files at `src/prompt_diary/workspace.py:277-293`, but `_parse_session_file` returns `None` before audit data is produced when no target span exists at `src/prompt_diary/workspace.py:320-321`. `_audit_manifest` receives only selected `ParsedSession` entries and writes only those under `sessions` at `src/prompt_diary/workspace.py:601-617`. | Add an audit-only scanned-file inventory with source path, checksum, event bounds, parse counts/warnings, and an `included`/`excluded_reason` field. Keep this out of report-generation input. |

## Verification
| Command or inspection | Evidence summary | Result |
| --- | --- | --- |
| `sed -n '1,240p' README.md` and `nl -ba README.md` | Confirmed `uv` workflow, Python 3.10+ support, and documented checks at `README.md:5-6`, `README.md:25-27`, `README.md:85-140`. | Pass |
| `sed -n '1,620p' docs/src/prompt-diary-tool-design.md` and `nl -ba docs/src/prompt-diary-tool-design.md` | Confirmed workflow, workspace, indexing, audit, prompt, report, citation, and validation contracts at cited lines. | Pass |
| `rg --files src/prompt_diary tests .agents \| sort` | Inspected current `src/prompt_diary/` modules and relevant tests: `tests/test_api.py`, `tests/test_cli.py`, `tests/test_prompt_diary_e2e_qa.py`, `tests/test_report.py`, `tests/test_targets.py`, and `tests/test_workspace.py`. | Pass |
| `uv run ruff check` | First sandboxed attempt failed because `uv` could not write `/home/huwei/.cache/uv`; escalated rerun output: `All checks passed!`. | Pass |
| `uv run ruff format --check` | First sandboxed attempt failed because `uv` could not write `/home/huwei/.cache/uv`; escalated rerun output: `15 files already formatted`. | Pass |
| `uv run basedpyright` | First sandboxed attempt failed because `uv` could not write `/home/huwei/.cache/uv`; escalated rerun output: `0 errors, 0 warnings, 0 notes`. | Pass |
| `uv run pytest -o cache_dir=/tmp/prompt-diary-pytest-cache-arch002` | First sandboxed attempt failed because `uv` could not write `/home/huwei/.cache/uv`; escalated rerun collected 67 tests and passed: `67 passed in 0.24s`. Pytest cache was redirected outside the repo. | Pass |

## Constraints Compliance
| Constraint | Evidence | Result |
| --- | --- | --- |
| Role boundary respected | No files under `src/` or `tests/` were modified. The only file added by this review is `.agents/reports/architecture-review-002.md`. | Pass |
| Evidence-backed report | Requirements, findings, and verification rows cite design/source/test line references and command outputs. | Pass |
| No code/test patching | Editing was limited to this Markdown report; all implementation and test observations were read-only. | Pass |

## Residual Risk
- The report-writing model itself is external by design, configured through `PROMPT_DIARY_REPORT_WRITER_COMMAND`; this review validates the boundary and prompt contract, not any real provider behavior.
- Validation is intentionally structural and trusts indexes for target-window boundaries, matching `docs/src/prompt-diary-tool-design.md:363`; evidential correctness still depends on the report writer following the prompt.
- The repository had pre-existing dirty and untracked files before this review, so this report evaluates the current working tree state rather than a clean checkout.
