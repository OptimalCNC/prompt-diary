# Daily Report Synthesis Tools

Daily Report Synthesis tools are the agent-facing write path for the daily report. Each tool patches
one synthesize slot in the workspace-root `daily-report.json` — the per-project summary, the
whole-report title, the whole-report engagement assessment, or the whole-report team-learning
analysis. The MCP server validates the submission through the generation API, resolves every
citation to its indexed-turn line range, and atomic-writes the patched report.

Shared workspace, result, and error rules are defined in [MCP Tools](./index.md). The Daily Report
Synthesis phase contract — the report sections, controlled values, and citation model — is defined in
[Daily Report Synthesis](../daily-synthesis.md).

## Registered Tools

The Daily Report Synthesis phase registers these tools:

| Tool | Purpose |
| --- | --- |
| `write_project_summary` | Check one project's `summary` and patch `projects[p].summary`. |
| `write_report_title` | Check the whole-report title and patch `report_title`. |
| `write_engagement` | Check the engagement reading and patch `engagement_assessment`. |
| `write_team_learning` | Check the team-learning analysis and patch `team_learning`. |

## Workspace Resolution

The current working directory is the prepared report workspace root. The tools read the per-project
session index (`projects/<project_key>/sessions.index.jsonl`) to resolve citations and patch the
single canonical `daily-report.json` at the workspace root. A deterministic Build step seeds that
file with the synthesize slots set to `null` before any synthesis pass runs; the write tools
require the skeleton to already exist and only ever replace their own slot.

## `write_project_summary`

Check one project's qualitative summary and patch its slot. The summary's confidence is implicit in
the project's work items, so the section carries no confidence value.

Input schema:

```json
{
  "project_key": "<project_key>",
  "summary": {
    "text": "<non-empty string>",
    "citations": [
      {"session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}
    ]
  }
}
```

`summary.citations[*]` are per-project: the project is the tool's `project_key`, so `project_key` is
omitted. A citation that names a `project_key` disagreeing with the tool argument is rejected rather
than silently rebound.

Successful result:

```json
{"status": "written", "project_key": "ReportGenerator-e6ff7eeda632"}
```

The patched `projects[p].summary` is a single object — `{"text": ..., "citations": [...]}` — with
each citation resolved to `{"project_key", "session_ref", "turn_ref", "lines"}`.

Invalid result:

```json
{
  "status": "invalid",
  "errors": [
    {
      "path": "summary.citations[0].project_key",
      "message": "citation names a different project 'Other-aaaaaaaaaaaa', not 'ReportGenerator-e6ff7eeda632'",
      "hint": "omit project_key on a per-project pass or name this tool's project"
    }
  ]
}
```

## `write_report_title`

Check the whole-report title and patch `report_title`. The title is generated content, but the date
is renderer-owned metadata: `title.text` must not include `report_date`.

Input schema:

```json
{
  "title": {
    "text": "<one-line non-generic title without date>",
    "citations": [
      {"project_key": "<project_key>", "session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}
    ]
  }
}
```

This is a cross-project pass, so every citation names its `project_key` explicitly. The parser
rejects blank, multiline, date-bearing, generic report-label titles such as `Prompt Diary Report`,
and titles with no citations.

Successful result:

```json
{"status": "written"}
```

The patched `report_title` is a single object — `{"text": ..., "citations": [...]}` — with each
citation resolved to `{"project_key", "session_ref", "turn_ref", "lines"}`.

Invalid result:

```json
{
  "status": "invalid",
  "errors": [
    {
      "path": "title.text",
      "message": "title.text must not include the report date",
      "hint": "write a concise, specific title without date, Markdown, or generic report wording"
    }
  ]
}
```

## `write_engagement`

Check the whole-report engagement reading and patch `engagement_assessment`. `observations[*]` read
a single controlled `dimension` each, and every cited claim is hedged by a controlled `confidence`.

Input schema:

```json
{
  "overall_reading": {
    "text": "<non-empty string>",
    "citations": [
      {"project_key": "<project_key>", "session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}
    ],
    "confidence": "high|medium|low"
  },
  "observations": [
    {
      "dimension": "<controlled engagement dimension>",
      "statement": "<non-empty string>",
      "citations": [
        {"project_key": "<project_key>", "session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}
      ],
      "confidence": "high|medium|low"
    }
  ],
  "limits": ["<non-empty string>"]
}
```

This is a cross-project pass, so every citation names its `project_key` explicitly — session refs
repeat across projects, so the project key is part of the citation identity. The controlled
`dimension` values duplicate `ENGAGEMENT_DIMENSIONS` in
`src/prompt_diary/generate/prompts/__init__.py` so this tool contract remains self-contained.

Successful result:

```json
{"status": "written"}
```

The patched `engagement_assessment` is a single object with `overall_reading`, `observations`, and
`limits`; each citation is resolved to `{"project_key", "session_ref", "turn_ref", "lines"}`.

Invalid result:

```json
{
  "status": "invalid",
  "errors": [
    {
      "path": "overall_reading.citations[0]",
      "message": "S0001/T9999 has no committed evidence in project 'ReportGenerator-e6ff7eeda632'",
      "hint": "cite only turns with committed evidence in the named project"
    }
  ]
}
```

## `write_team_learning`

Check the whole-report team-learning analysis and patch `team_learning`. `patterns[*]` carry a
controlled `kind` (promote, avoid, or reuse) plus `rationale` and `recurrence`.

Input schema:

```json
{
  "takeaways": {
    "text": "<non-empty string>",
    "citations": [
      {"project_key": "<project_key>", "session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}
    ],
    "confidence": "high|medium|low"
  },
  "patterns": [
    {
      "kind": "<controlled team-learning pattern kind>",
      "statement": "<non-empty string>",
      "rationale": "<non-empty string>",
      "recurrence": "<non-empty string>",
      "citations": [
        {"project_key": "<project_key>", "session_ref": "<session_ref>", "turn_ref": "<turn_ref>"}
      ],
      "confidence": "high|medium|low"
    }
  ],
  "limits": ["<non-empty string>"]
}
```

This is a cross-project pass, so every citation names its `project_key` explicitly. The controlled
`kind` values duplicate `TEAM_LEARNING_PATTERN_KINDS` in
`src/prompt_diary/generate/prompts/__init__.py` so this tool contract remains self-contained.

Successful result:

```json
{"status": "written"}
```

The patched `team_learning` is a single object with `takeaways`, `patterns`, and `limits`; each
citation is resolved to `{"project_key", "session_ref", "turn_ref", "lines"}`.

Invalid result:

```json
{
  "status": "invalid",
  "errors": [
    {
      "path": "patterns[0].kind",
      "message": "patterns[0].kind must be a controlled team-learning pattern kind value",
      "hint": "use a controlled value such as avoid, promote, reuse"
    }
  ]
}
```

## Structural Rules

Each tool applies these rules before committing. A rejected write returns structured, actionable
`{path, message, hint}` errors per [MCP Tools](./index.md) and leaves `daily-report.json`
byte-for-byte unchanged.

- **Skeleton required.** `daily-report.json` must already exist at the workspace root, seeded by the
  Build step. If it is missing (or is not a JSON object), the write is rejected at path
  `daily_report` and no file is created.
- **Chain-only parse.** The submission's structure is validated first: non-empty strings, controlled
  `confidence`/`dimension`/`kind` values, and at least one citation per cited claim. Cross-project
  citations (`write_report_title`, `write_engagement`, `write_team_learning`) require `project_key`;
  per-project citations (`write_project_summary`) omit it.
- **Citation resolution and scope.** Every citation must resolve to an indexed turn in
  `sessions.index.jsonl`; the session index is the covered-turn universe, so a citation is in scope
  iff it resolves. An unresolvable citation is rejected at the citation's own path. Resolution stamps
  each stored citation with its 1-based inclusive `lines` range.
- **Project scope for `write_project_summary`.** `project_key` must be a real workspace project and
  must be present in the skeleton's `projects` list. A `summary.citations[*]` that names a different
  `project_key` is rejected at `summary.citations[<i>].project_key`; the rest resolve against the
  tool's `project_key`.
- **Idempotent slot replace.** Patching replaces the slot with a single object, so re-running a pass
  overwrites the prior write rather than accumulating. Writes are committed with atomic file
  replacement.

## Code Placement

Per [MCP Tools](./index.md): the transport-independent API — parsing, citation resolution, and report
IO — lives in `src/prompt_diary/generate/daily_synthesis/`; the MCP adapter lives in
`src/prompt_diary/mcp/`. Validation reuses the enums in
`src/prompt_diary/generate/prompts/__init__.py` (`ENGAGEMENT_DIMENSIONS`,
`TEAM_LEARNING_PATTERN_KINDS`).
