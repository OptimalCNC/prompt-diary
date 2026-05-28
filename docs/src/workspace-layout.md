# Workspace Layout

The workspace is the prepared evidence boundary for one target report date. It packages local
assistant history into a deterministic structure that report generation can read without scanning
the user's raw session stores.

```mermaid
flowchart LR
    raw["Raw assistant sessions<br/>Codex / Claude Code"]
    adapters["Source adapters<br/>timestamps, ids, cwd, line numbers"]
    window["Report window<br/>half-open interval"]
    workspace["Prepared report workspace<br/>metadata, projects, copied sessions, project session indexes"]
    report["Report generation<br/>prompt + indexed evidence"]

    raw --> adapters
    window --> adapters
    adapters --> workspace
    workspace --> report
```

Preparation owns data discovery, date-window handling, session copying, and indexing. The workspace
keeps report inputs stable and reviewable; the detailed contracts below define how sources are
selected, grouped, copied, and indexed.

For report date `2026-05-12`, the tool creates a prepared report workspace like this:

```text
.reports/
├── work/
│   └── 2026-05-12/
│       ├── metadata.json
│       └── projects/
│           └── ReportGenerator-e6ff7eeda632/
│               ├── project.json
│               ├── sessions.index.jsonl   # copied session inventory and target spans
│               ├── sessions/
│               │   ├── codex/
│               │   │   ├── 019e1bb6-620a-7462-9fb0-d28c3acef59d.jsonl
│               │   │   └── subagents/
│               │   │       └── 019e1bb6-620a-7462-9fb0-d28c3acef59d/
│               │   │           └── 019e1bb7-0c0f-74f2-a0c4-a8f5a0ef7f7d.jsonl
│               │   └── claude-code/
│               │       ├── 3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3.jsonl
│               │       └── subagents/
│               │           └── 3e1dcfb6-32e7-4059-9d1c-5fddc8b8d0c3/
│               │               └── agent-a9636c61b58788670.jsonl
```

Copied session files keep their source filenames. The examples above use UUID-based filenames
because both Codex and Claude Code identify local session transcript files by session id rather
than by report date. Source-native subagent transcripts are copied under
`sessions/<source>/subagents/<parent-session-id>/` when they are associated with a copied parent
session.

The workspace boundary is an intended-input boundary, not a security sandbox. This design does not
require filesystem or network isolation.

## Time Window Context (`metadata.json`)

The report window is an absolute half-open time interval derived from midnight at the start of the
target date to midnight at the start of the next date in the requested timezone.
`report_window_utc` is the canonical serialized representation used for deterministic trigger
inclusion checks after that local-day boundary has been resolved.

For example, `--date 2026-05-12 --timezone Asia/Shanghai` targets
`2026-05-12T00:00:00+08:00` through `2026-05-13T00:00:00+08:00`,
not `2026-05-12T00:00:00Z` through `2026-05-13T00:00:00Z`.

- Include work units whose human-authored trigger time is at or after
  `report_window_utc.start`.
- Exclude work units whose human-authored trigger time is at or after `report_window_utc.end`.
- Human triggers exactly at `report_window_utc.start` belong to this report.
- Human triggers exactly at `report_window_utc.end` belong to the next report.
- Session files may cross midnight. The target day includes a work unit by human trigger
  timestamp; indexed target spans locate that trigger and the resulting agent reactions inside
  copied sessions.

Example resolved window for `2026-05-12` in `Asia/Shanghai`:

```mermaid
flowchart LR
    localStart["Local start<br/>2026-05-12T00:00:00+08:00<br/>included"]
    utcStart["UTC start<br/>2026-05-11T16:00:00Z<br/>included"]
    utcEnd["UTC end<br/>2026-05-12T16:00:00Z<br/>excluded"]
    localEnd["Local end<br/>2026-05-13T00:00:00+08:00<br/>excluded"]

    localStart --> utcStart --> utcEnd --> localEnd
```

## Metadata Context (`metadata.json`)

`metadata.json` is required at the workspace root.

```json
{
  "schema_version": 2,
  "report_date": "2026-05-12",
  "timezone": "Asia/Shanghai",
  "status": "final",
  "prepared_at": "2026-05-13T08:58:00+08:00",
  "report_window_local": {
    "start": "2026-05-12T00:00:00+08:00",
    "end": "2026-05-13T00:00:00+08:00"
  },
  "report_window_utc": {
    "start": "2026-05-11T16:00:00Z",
    "end": "2026-05-12T16:00:00Z"
  }
}
```

Rules:

- `report_window_utc` is the canonical serialized trigger-inclusion boundary.
- `report_window_local` is the human-facing period shown in the report. Do not render a
  `00:00Z` to next-day `00:00Z` report window unless the requested timezone is UTC.
- `status` is `final` for a completed day and `partial` for same-day reports.
- `prepared_at` is the workspace preparation time.

## Project Context (`project.json`)

Project folders are grouped by canonical project root.

Project root derivation:

1. Prefer an explicit `cwd` or project root from the session record.
2. For Codex sessions, use `session_meta.payload.cwd`, then `turn_context.payload.cwd`, then the configured source fallback.
3. For Claude Code sessions, use top-level `cwd`, then the configured source fallback.
4. Resolve symlinks and normalize path separators when the path exists.
5. If no reliable root exists, use `unknown-project/<source>/<source_session_id>`.

Project key generation:

- Shape: `<sanitized-display-name>-<hash12>`.
- `sanitized-display-name`: basename of canonical root, with characters outside `[A-Za-z0-9._-]` replaced by `-`, repeated `-` collapsed, trimmed to 48 characters, fallback `unknown-project`.
- `hash12`: first 12 lowercase hex characters of SHA-256 over the UTF-8 canonical root string. For unknown roots, hash the fallback identity string.

Example:

```text
ReportGenerator-e6ff7eeda632
```

Each project folder contains `project.json`.

```json
{
  "schema_version": 2,
  "project_key": "ReportGenerator-e6ff7eeda632",
  "project_label": "ReportGenerator"
}
```

`project_label` is a sanitized human-readable label for report display. Session counts and source
lists are derived from the session index. Absolute project roots are not report inputs and do not
belong in `project.json`.

## Session Context (`sessions/*.jsonl`)

Adapters parse source-specific JSONL records enough to identify human-authored triggers, copy
sessions, and create the session index. Session discovery targets only root/main assistant
sessions. Source-native subagent sessions are skipped during initial discovery and are not copied
merely because they contain target-window timestamps. A subagent session is copied only when an
indexed parent session references it through a spawn/result association inside that parent
session's target span.

A human-authored trigger is an externally authored user message, correction, approval, resume
action, or explicit human-supplied context that asks or directs the agent to act.
[Source Session Formats](./source-session-formats.md) documents the per-source record structures
and explains how adapters distinguish human triggers from source-generated records. A human
`Continue`, `resume`, or equivalent UI action is a trigger when it asks the agent to continue,
recover, or finish work; it may also reveal that the previous agent reaction paused or stopped.
Tool results, task notifications, system records, and source-generated records with `role: user`
are not human triggers unless they carry a new externally authored instruction.

| Source | Timestamp | Session id | Project root | Missing or malformed trigger timestamp |
| --- | --- | --- | --- | --- |
| Codex | top-level `timestamp`; fallback `payload.timestamp` only for session metadata | `session_meta.payload.id`; fallback filename stem | `session_meta.payload.cwd`, then `turn_context.payload.cwd` | cannot include a trigger-owned work unit; remains available only as copied context if another trigger includes the session |
| Claude Code | top-level `timestamp` | filename stem | top-level `cwd`; fallback configured source root | cannot include a trigger-owned work unit; remains available only as copied context if another trigger includes the session |

Malformed JSONL lines are never standalone evidence for a work claim. The adapter should treat
malformed and untimestamped records as preparation diagnostics, not report evidence.

Copied root session files keep original source filenames and original record order under
`sessions/<source>/`. Copied subagent files keep original source filenames under
`sessions/<source>/subagents/<parent-session-id>/`. Adapters must preserve line numbering because
the session index cites parent session line numbers.

## Session Index Context (`sessions.index.jsonl`)

Each project has one `sessions.index.jsonl` file. It has one JSON object per copied root session
file in that project and is both the copied-session inventory and the trigger-owned span index.
Subagent sessions do not get their own session index rows; they are optional context for the parent
agent reaction that spawned or received them.

`session_ref` is unique within the project session index and deterministic for the same project
inputs. It gives citations a short stable handle for a copied session.

Required fields:

```json
{
  "session_ref": "S0001",
  "source": "codex",
  "source_session_id": "019e1bb6-620a-7462-9fb0-d28c3acef59d",
  "session_path": "sessions/codex/019e1bb6-620a-7462-9fb0-d28c3acef59d.jsonl",
  "target_start_line": 21,
  "target_end_line": 98,
  "subagent_path": "sessions/codex/subagents/019e1bb6-620a-7462-9fb0-d28c3acef59d",
  "turns": [
    {
      "turn_ref": "T0001",
      "turn_start_line": 21,
      "turn_end_line": 55,
      "target_subagents": [
        {
          "session_file": "019e1bb7-0c0f-74f2-a0c4-a8f5a0ef7f7d.jsonl",
          "source_session_id": "019e1bb7-0c0f-74f2-a0c4-a8f5a0ef7f7d",
          "agent_role": "explorer",
          "parent_spawn_line": 43,
          "parent_result_line": 51,
          "association": "spawned_or_returned_in_target_span"
        }
      ]
    },
    {
      "turn_ref": "T0002",
      "turn_start_line": 60,
      "turn_end_line": 98,
      "target_subagents": []
    }
  ]
}
```

`session_path` is relative to the project folder and must resolve under that project's `sessions/`
directory. `subagent_path` is relative to the project folder and names the folder containing copied
subagent files for this parent session. If the parent has no associated copied subagents,
`subagent_path` is `""`.
Downstream evidence artifacts should reference copied sessions by `session_ref`; `session_path`
stays in the session index as the canonical copied-session locator.

`target_start_line` and `target_end_line` are the overall target span — the first turn's start line
and the last turn's end line. They are derived from `turns` for convenience; consumers that need
per-trigger boundaries should use the `turns` list.

Each `turns` item records one trigger-owned work unit inside the target span:

- `turn_ref` is a row-local prepared-turn reference such as `T0001`. It resets for each
  `sessions.index.jsonl` row and identifies a turn as `(project_key, session_ref, turn_ref)`.
- `turn_start_line` is the line of the human-authored trigger that starts this work unit. It is
  1-based and inclusive.
- `turn_end_line` is the last line of agent reactions owned by this trigger. It is 1-based and
  inclusive. For the last trigger in a session, this extends to the end of the file. For earlier
  triggers, it ends before the pre-trigger scaffolding of the next turn (see
  [Source Session Formats](./source-session-formats.md) for scaffolding rules per source).
- `target_subagents` lists subagent transcripts associated with this turn. Each item has the fields
  described below. If no subagents are associated with this turn, `target_subagents` is `[]`.

Each `target_subagents` item records one copied subagent transcript associated with its parent turn:

- `session_file` is the copied source-native filename under `subagent_path`.
- `source_session_id` is the source-native subagent session id when available; otherwise use the
  filename stem.
- `agent_role` is the source-normalized role when available, such as `explorer` or `reviewer`;
  otherwise it is `null`.
- `parent_spawn_line` is the parent session line that launches the subagent and contains the
  delegation reason or prompt. It is `null` when the spawn line is unavailable.
- `parent_result_line` is the parent session line that receives the subagent output, completion
  notice, or summarized result. It is `null` when the result line is unavailable.
- `association` is `spawned_or_returned_in_target_span` when either the spawn line or result line
  falls inside the parent turn's line range.

Other parent references to the same subagent are not indexed by default. Subagent files are copied
as richer context for parent agent reactions, not as independent report targets. Diagnostic data
such as checksums, total line counts, event bounds, event counts, and parse warnings is not report
input.

Reference generation:

1. Within each project, sort copied root sessions by `(source, source_session_id, session_path)`.
2. Assign `session_ref` values as `S0001`, `S0002`, and so on within that project.
3. If a session lacks a source session id, use the source filename stem in the sort key and in `source_session_id`.
4. Within each session index row, assign `turn_ref` values as `T0001`, `T0002`, and so on after
   target turn construction, in the order of that row's `turns[]`.

Target span and turn construction:

- All line numbers are 1-based and inclusive.
- Each copied root session has exactly one target span for the report window. The target span is
  the union of the session's included turns.
- `target_start_line` is the first included turn's `turn_start_line`.
- `target_end_line` is the last included turn's `turn_end_line`.
- A human-authored trigger belongs to the target report date when its timestamp falls inside
  `report_window_utc`. Each in-window trigger produces one entry in `turns`.
- A trigger's turn starts at the trigger line (`turn_start_line`) and ends after the agent reactions
  and outcomes caused by that trigger (`turn_end_line`), even when those reaction lines have
  timestamps outside the report window.
- A later human-authored trigger outside the report window starts a different work unit and must not
  be absorbed into this report's target span. The previous turn ends before the next trigger's
  pre-trigger scaffolding (see [Source Session Formats](./source-session-formats.md)).
- For the last trigger in the session (no successor trigger), the turn extends to the last line of
  the file.
- `turns` is ordered by `turn_start_line`. When the target span contains multiple turns, they are
  not necessarily contiguous — pre-trigger scaffolding between turns is excluded.
- If malformed, untimestamped, or non-monotonic records make a turn broader than the true
  trigger-owned work unit, preparation still records the inclusive turn it can determine
  and treats the anomaly as a preparation diagnostic.
- No separate context index is generated. The reporter can inspect surrounding lines directly in the
  copied root session file, and can inspect listed subagent files when richer context is useful.
