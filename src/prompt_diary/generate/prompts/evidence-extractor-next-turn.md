The previous turn was written successfully.

Committed result:

```json
{{ write_evidence_result }}
```

Continue with the next assigned turn from the same session. Reuse the transcript model, the
`read_session_lines` reading rules, the evidence chain shape, and the extraction rules from the
initial prompt. The full transcript was not loaded into context: call `read_session_lines` for
this turn's own line range `turn_start_line`..`turn_end_line` (shown below) with `mode="compact"`,
using the same `project_key` and `session_ref` as the initial prompt. Neighboring lines may be read
through `read_session_lines` only as non-citable context. The raw session-file prohibition from the
initial prompt still applies: do NOT read the raw session file by any means — not `cat`, `awk`,
`sed`, `grep`, a script, nor any built-in file-read tool — not even a single line; use
`read_session_lines(mode="full")` only for a narrow range when compact output is genuinely
insufficient. Do not modify or duplicate the previous turn's evidence chain.

Assigned turn to extract now:

```json
{{ target_turn }}
```

Start now: extract this turn and make one successful `write_evidence` commit. Work silently — do not
narrate or post status messages. If `write_evidence` returns `status: invalid`, correct the draft
from the returned errors and retry. After it succeeds, stop without summarizing what you wrote.
