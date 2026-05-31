The previous turn was written successfully.

Committed result:

```json
{{ write_evidence_result }}
```

Continue with the next assigned turn from the same session. Reuse the transcript model, evidence
chain shape, and extraction rules from the initial prompt. The full transcript was not loaded into
context: read this turn's own line range `turn_start_line`..`turn_end_line` (shown below) from the
same session file as the initial prompt, using a reader that shows absolute file line numbers.
Neighboring lines may be read only as non-citable context. Do not modify or duplicate the previous
turn's evidence chain.

Assigned turn to extract now:

```json
{{ target_turn }}
```

Start now: extract this turn and make one successful `write_evidence` commit. If `write_evidence`
returns `status: invalid`, correct the draft from the returned errors and retry. After it succeeds,
report the committed result and pause.
