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

**Source-generated context** (not triggers) is identified by content prefix:

| Content prefix | Meaning |
| --- | --- |
| `<environment_context>` | Shell, cwd, and date context injected by the CLI |
| `# AGENTS.md instructions` | User instruction file injected as message context |
| `<turn_aborted>` | System notification that the user interrupted the previous turn |
| `<subagent_notification>` | Subagent result injected as a user message for the parent agent |
| `<INSTRUCTIONS>` | Instruction block injected by the CLI (older format variant) |

These records carry `payload.role=user` but are authored by the CLI, not the human.

**Human-authored triggers** are detected by either:

1. `event_msg` with `payload.type=user_message` — always echoes the real human prompt, never the
   context messages. When present, this is the most reliable trigger indicator.
2. `response_item` with `payload.role=user` and `payload.type=message` whose content does not match
   any source-generated prefix — this is necessary because the `event_msg` echo is absent for ~40%
   of triggers.

When both records appear for the same human action, they share the same timestamp and appear on
consecutive lines.

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

### Codex Subagent Sessions

Codex subagent sessions are identified by `session_meta.payload.thread_source == "subagent"` or by
the presence of `session_meta.payload.source.subagent.thread_spawn.parent_thread_id`. Subagent
sessions are not scanned for human triggers during root session discovery.

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

All 486 triggers observed across 52 real sessions also have `userType=external` and a `promptId`
field, but the four fields above are sufficient for detection.

Records with `type=user` and `sourceToolAssistantUUID` present are tool results — the assistant
invoked a tool, and the result is delivered as a `role=user` message. These are agent reactions, not
human triggers.

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
or by `isSidechain=true` on records. Sidechain sessions are not scanned for human triggers during
root session discovery.

## Design Decisions

### Why content-based filtering for Codex

Codex injects source-generated context as `response_item` records with `payload.role=user`, making
them structurally identical to human-authored triggers. The `event_msg/user_message` echo is the
cleanest discriminator (it only echoes real human prompts), but it is absent for ~40% of triggers.
Content-prefix detection handles the remaining cases. The known prefixes (`<environment_context>`,
`# AGENTS.md`, `<turn_aborted>`, `<subagent_notification>`) are stable CLI conventions unlikely to
appear in human-authored prompts.

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
