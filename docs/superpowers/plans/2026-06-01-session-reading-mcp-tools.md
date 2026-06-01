# Session Reading MCP Tools — Implementation Plan

Executes the design at
[`../specs/2026-06-01-session-reading-mcp-tools-design.md`](../specs/2026-06-01-session-reading-mcp-tools-design.md).

Methodology: subagent-driven development (fresh implementer per task, then spec-compliance review,
then code-quality review, then Codex cross-AI review) with strict TDD inside each task. Work happens
directly on `main` (feature branch opt-out granted by the user).

## Architecture

Mirror the existing `write_evidence` seam:

- **Transport-independent API** takes `workspace_path` + typed args and returns frozen result
  dataclasses. Lives under `src/prompt_diary/generate/evidence_extraction/`.
- **Thin MCP server wrapper** in `src/prompt_diary/mcp/server.py` resolves the workspace from
  `PROMPT_DIARY_WORKSPACE` (else cwd) and forwards to the API.

New modules:

- `session_compaction.py` — **pure** deterministic record compaction. No filesystem, no network.
  `compact_record(raw_line: str, *, source: str) -> CompactRecord`. Source-aware (codex/claude)
  with a graceful fallback for unrecognized/malformed records. This is the heavily unit-tested core.
- `session_reader.py` — the `read_session_lines` API. Resolves `(project_key, session_ref)` via
  `load_prepared_workspace`, validates the physical line range, reads the raw lines, and returns
  either compact records (default) or raw JSONL lines (`mode="full"`). Returns
  `ReadSessionLinesOkResult | ReadSessionLinesInvalidResult` with structured `SessionReadError`
  entries.

Server wrapper: add `read_session_lines(project_key, session_ref, start_line, end_line, mode)` to
`server.py` and register it in `build_mcp_server()`.

### Decisions (encoded, not asked)

- **Error envelope key is `field`** (per the design doc's error model), via a reader-specific
  `SessionReadError(field, message, hint)`. The write tools use `path` for nested chain paths;
  the reader validates flat inputs, so `field` is the accurate term. Noted as a deliberate, minor
  divergence for Codex review to weigh.
- **`mode` default is `compact`.** The MCP `mode` parameter description must warn that `full`
  returns raw JSONL lines that can be very large — assert the warning text appears in the tool's
  input schema.
- **New dedicated fixtures** with real codex + claude record shapes (including one oversized tool
  result and one reasoning/thinking record) drive compaction/reader tests. The existing
  `basic-two-turns` fixture is left untouched; the reader's graceful fallback handles its simplified
  shape so the real-agent evidence test keeps working.
- **Threshold:** 1 KiB per tool-result payload pass-through, as a named module constant.

## Compact record contract

For each physical line in compact mode:

- `line` (int, absolute 1-based), `record_type` (source-native discriminator string), `role` (when
  determinable), `content_kinds` (subset of `text`, `tool_use`, `tool_result`, `thinking`),
  `summary` (deterministic), and optional `text_preview`, `tool_uses`, `tool_results`.
- Always: `raw_bytes` (byte length of the original physical line), `raw_sha256`, `truncated`.

Trimming policy (deterministic, no LLM):

- Trim/omit large tool-result payloads (head + optional tail preview) and assistant
  reasoning/thinking text.
- Pass through tool-result payloads below the 1 KiB threshold unchanged.
- Never trim user-authored messages or assistant text messages.
- Set `truncated` and always report `raw_bytes`/`raw_sha256` when anything is omitted.

Full mode returns `{line, raw_line, raw_bytes, raw_sha256}` per line plus `mode: "full"`.

## Error cases (reader)

Unknown project key; unknown session ref; missing/invalid session index; missing session file;
non-integer line number; range outside the session; reversed range; range too broad for the mode;
malformed JSONL line inside the range (compact still identifies the physical line and raw byte
count).

## Tasks

### Task 1 — Compaction core (`session_compaction.py`), pure + TDD
RED→GREEN per behavior: codex record types (session_meta, event_msg/user_message &
agent_message, turn_context, response_item message/function_call/function_call_output/reasoning),
claude record types (user trigger, assistant text/tool_use/thinking, user tool_result, attachment,
summary/system), large-tool-result trimming, sub-threshold pass-through, untrimmed user/assistant
text, reasoning omission, malformed/unrecognized fallback, and raw_bytes/raw_sha256/truncated
accounting.

### Task 2 — `read_session_lines` API (`session_reader.py`), TDD
Session resolution, range validation, compact vs full output, physical line-number fidelity, every
error case above. Uses Task 1 for compaction.

### Task 3 — MCP server registration + opt-in real-tool contract test
Register `read_session_lines`; tests for registration, input-schema contract fields, the `mode`
warning text, compact success, full success, invalid-result parity with the API, and
`PROMPT_DIARY_WORKSPACE` resolution. Add a `codex_mcp`-marked test that a real Codex agent can call
the approved tool once.

### Task 4 — Prompt rewrite + plumbing
Rewrite `evidence-extractor.md` and `evidence-extractor-next-turn.md`: read only via
`read_session_lines`; loudly forbid shell/`cat`/`awk`/`sed`/`grep`/`jq`/Python/Codex built-in file
reads of session files (even a single line); `mode="full"` only for a narrow range with a stated
good reason; keep the no-evidence-file and untrusted-content rules; stop exposing `session_path`.
Drop `session_path` from `inputs.py`, `prompts/__init__.py`, `runner.py`, and `cmds/prompts.py`.
Keep the `- Project key:` / `- Session reference:` lines (the fake agent and any tooling parse
them). Update prompt tests.

### Task 5 — Mock-agent workflow test
A deterministic fake agent that, per turn, calls the real `read_session_lines` (compact) for the
assigned bounds and then `write_evidence`, proving runner + new prompt + new tool integrate without
a real model. Keep it in the default (100%-coverage) suite.

### Task 6 — Real-agent run, compliance monitoring, docs
Opt-in `codex_mcp` test driving a real Codex agent through the new prompt; assert via the agent
event stream that `read_session_lines` (compact) was called and the session file was not read via
shell. Orchestrator then runs it live and inspects the produced Codex session to confirm compliance,
recording findings. Update docs: `mcp-tools/evidence-extraction.md`, `mcp-tools/index.md`,
`evidence-extractor-prompt.md`, `mcp-tool-architecture.md`.

## Gates per task

1. Implementer (TDD; commit on green).
2. Spec-compliance review (independent code read vs the design's acceptance criteria).
3. Code-quality review (`superpowers:code-reviewer`).
4. Codex cross-AI review (`codex:codex-rescue`, read-only).
5. Fix loops until each gate is clean, then mark the task done.

Final: whole-implementation review + Codex cross-review, then run the full pre-submit suite
(`ruff check`, `ruff format --check`, `basedpyright`, `pytest`, `coverage run -m pytest` +
`coverage report` at 100%).
