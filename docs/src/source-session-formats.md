# Source Session Formats

This document records the structure of source session JSONL files and the decisions behind trigger
detection. It supports [Workspace Layout](./workspace-layout.md) by explaining how adapters
distinguish human-authored triggers from source-generated records.

The evidence comes from analysis of ~200 real Codex sessions and all ~50 real Claude Code sessions
as of 2026-05-25.

## Codex Session Structure

A Codex session JSONL file contains one JSON object per line. Records are ordered chronologically
within each turn. A session is a sequence of turns, and each turn follows this structure:

```text
session_meta                          scaffolding — session-level metadata, once at file start
event_msg/task_started                scaffolding — turn boundary, marks the beginning of a turn
response_item  role=developer         scaffolding — system instructions (permissions, skills, etc.)
response_item  role=user  (context)   scaffolding — source-generated context, NOT a human trigger
turn_context                          scaffolding — environment metadata (cwd, timezone, model)
response_item  role=user  (trigger)   TRIGGER     — human-authored prompt
event_msg/user_message                TRIGGER     — echo of the human prompt (~60% of triggers)
event_msg/token_count                 scaffolding — token usage
response_item  role=assistant         reaction    — agent reasoning, messages, tool calls
response_item  function_call          reaction    — tool invocation
response_item  function_call_output   reaction    — tool result
event_msg/agent_message               reaction    — agent status updates
event_msg/task_complete               scaffolding — turn boundary, marks the end of a turn
```

Not all records appear in every turn. The `role=developer` and context `role=user` records may be
absent in some turns. The `event_msg/user_message` echo is present for about 60% of triggers. Some
turns end with `event_msg/turn_aborted` instead of `task_complete` when the user interrupts.

### Codex Trigger Detection

A turn typically contains two `response_item` records with `payload.role=user`. The first is
source-generated context; the second is the human-authored trigger. Both have `payload.type=message`,
so structural fields alone do not distinguish them.

**Source-generated user-shaped records** (not triggers) are identified by content prefix:

| Content prefix | Meaning |
| --- | --- |
| `<environment_context>` | Shell, cwd, and date context injected by the CLI |
| `# AGENTS.md instructions` | User instruction file injected as message context |
| `<turn_aborted>` | System notification that the user interrupted the previous turn |
| `<subagent_notification>` | Subagent result injected as a user message for the parent agent |
| `<INSTRUCTIONS>` | Instruction block injected by the CLI (older format variant) |
| `A previous agent produced the plan below` | Reset-plan bootstrap prompt generated from a prior agent plan |

These records carry a user-shaped role or echo, but are authored by the CLI or agent source, not the
human.

**Human-authored triggers** are detected by either:

1. `event_msg` with `payload.type=user_message` after source-generated prefixes have been excluded.
   For ordinary turns this echoes the real human prompt and is the most reliable trigger indicator;
   reset-plan bootstraps are the observed exception.
2. `response_item` with `payload.role=user` and `payload.type=message` whose content does not match
   any source-generated prefix — this is necessary because the `event_msg` echo is absent for ~40%
   of triggers.

A user message and its echo form one trigger only when the `response_item` is immediately followed
by `event_msg/user_message`, both contain exactly the same complete text, and the echo timestamp is
0–100 milliseconds later. Each record can belong to only one pair. Other adjacent user messages
remain separate triggers, even when their text matches. Assistant echoes use the reverse order:
`event_msg/agent_message` followed by `response_item` with the same role, text, and timestamp window.
Unknown shapes and unproven matches remain separate records.

### Codex Turn Boundaries and Pre-Trigger Scaffolding

Between two human triggers, the dominant record sequence is:

```text
... final reaction of trigger N ...
event_msg/task_complete               end of trigger N's turn
event_msg/task_started                start of trigger N+1's turn  ← pre-trigger scaffolding
[response_item role=developer]        system instructions          ← pre-trigger scaffolding
[response_item role=user (context)]   source-generated context     ← pre-trigger scaffolding
turn_context                          environment metadata         ← pre-trigger scaffolding
response_item role=user (trigger)     trigger N+1
```

The records between `task_complete` and the next trigger are pre-trigger scaffolding. They belong to
the next trigger's turn, not to the previous trigger's reactions. Target span construction must
exclude them from the previous trigger's owned range.

Source-owned result and terminal-state messages are reactions: `<subagent_notification>` and
`<turn_aborted>`, including their `event_msg/user_message` echoes, stay in the preceding human
trigger's span. They do not start human turns and must not be trimmed as setup context.

### Codex Subagent Sessions

Codex subagent sessions are identified by `session_meta.payload.thread_source == "subagent"` or by
any string or object variant under `session_meta.payload.source.subagent`, including guardian,
review, compaction, and spawned work agents. Preparation stops scanning when that metadata is
encountered and excludes the child transcript from the report workspace. Codex sessions launched
from Claude Code through the Codex companion are identified by
`session_meta.payload.originator == "Claude Code"` and are treated the same way: their prompt is an
agent-owned delegation, not a human-authored root trigger. Delegation results already recorded in
the parent transcript remain available as evidence of the parent's reactions.

## Claude Code Session Structure

A Claude Code session JSONL file contains one JSON object per line. Records are ordered
chronologically but do not have explicit turn boundaries like Codex.

```text
permission-mode                       scaffolding — session permission configuration
last-prompt                           scaffolding — saved prompt for session resumption
ai-title / custom-title               scaffolding — conversation title metadata
file-history-snapshot                  scaffolding — file change tracking
attachment  type=file                 scaffolding — file context attached to conversation
user        role=user                 TRIGGER     — human-authored message
assistant   role=assistant            reaction    — agent response (may contain tool_use)
user        role=user (tool result)   reaction    — tool result, has sourceToolAssistantUUID
attachment  commandMode=task-notification  scaffolding — async agent completion notice
system      subtype=summary           scaffolding — session summary metadata
system      subtype=turn_duration     scaffolding — turn timing metadata
queue-operation                       scaffolding — task queue management
agent-name                            scaffolding — agent identity metadata
```

### Claude Code Trigger Detection

A Claude Code human trigger is a record where all of these hold:

| Field | Value | Rationale |
| --- | --- | --- |
| `type` | `"user"` | Only user-type records can be triggers |
| `message.role` | `"user"` | Confirms it carries a user message |
| `sourceToolAssistantUUID` | absent | Tool results have this field; triggers do not |
| `isSidechain` | `false` or absent | Sidechain records belong to subagent sessions |
| `isMeta` | `false` or absent | Meta messages are source-generated context |
| `isCompactSummary` | `false` or absent | Compaction summaries are source-generated context |
| `message.content` | not a nonempty list containing only `tool_result` items | Tool-only results are agent reactions, including records without `sourceToolAssistantUUID` |

All 486 triggers observed across the original 52-session sample also have `userType=external` and a
`promptId` field. Neither field is required for trigger detection. Messages mixing tool results with
new human text remain triggers unless explicit source metadata identifies them as machine-authored.

Records with `type=user` and `sourceToolAssistantUUID` present are tool results — the assistant
invoked a tool, and the result is delivered as a `role=user` message. These are agent reactions, not
human triggers.

Claude Code tool results from the Codex companion include a `[codex] Thread ready (<thread-id>)`
line. These parent-visible results remain part of the Claude turn; the launched Codex transcript
is excluded from report input.

### Claude Code Turn Boundaries

Claude Code sessions have no explicit turn start/end markers like Codex's `task_started` /
`task_complete`. Human triggers follow directly after the previous turn's assistant response or
scaffolding records (`system/turn_duration`, `queue-operation`, etc.). There is no pre-trigger
scaffolding that needs to be excluded from the previous trigger's range.

When the session resumes after inactivity, `system/away_summary`, `file-history-snapshot`, or
`permission-mode` records may appear before the next trigger. These are session-level scaffolding,
not reactions to the previous trigger.

### Claude Code Subagent Sessions

Claude Code subagent (sidechain) sessions are identified by path (`subagents/` directory component)
or by `isSidechain=true` on records. Preparation skips child paths without opening them and stops
scanning other files when sidechain metadata appears. Child transcripts are not copied into the
report workspace.

## Design Decisions

### Why content-based filtering for Codex

Codex injects source-generated context as `response_item` records with `payload.role=user`, making
them structurally identical to human-authored triggers. The `event_msg/user_message` echo is the
cleanest discriminator for ordinary turns, but it is absent for ~40% of triggers and can also echo
source-generated context. Content-prefix detection applies to both record forms. The known
prefixes include `<environment_context>`, `# AGENTS.md`, `<turn_aborted>`, `<subagent_notification>`,
`<INSTRUCTIONS>`, and `A previous agent produced the plan below`. Preparation excludes these
source-generated records from human-trigger detection before evidence extraction. Later genuine
human messages in the same session remain eligible triggers.

### Why trigger-owned spans instead of timestamp-per-line

Under timestamp-per-line logic, agent reactions that cross midnight are split between two report
dates. This contradicts the product principle that work-unit membership is determined by the
human-authored trigger, not by later reaction timestamps. Trigger-owned spans keep the entire work
unit together: the trigger and all its reactions belong to the same report, even if the agent
finishes after midnight.

### Why pre-trigger scaffolding is excluded from the previous trigger's span

Records like `task_started` and `turn_context` that appear between two triggers set up the next
trigger's turn. Including them in the previous trigger's target span would misattribute turn
infrastructure to the wrong work unit and inflate the span past the actual reactions. Scanning
backwards from the next trigger to skip these records produces the correct boundary.
