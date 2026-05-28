The previous turn was written successfully.

Committed result:

```json
{{ write_evidence_result }}
```

Continue with the next assigned turn from the same session. Use the same session context, evidence
chain shape, and extraction rules from the initial prompt. Do not modify or duplicate the previous
turn's evidence chain.

Assigned turn to extract now:

```json
{{ target_turn }}
```

Start now: extract this turn and make one successful `write_evidence` commit. If `write_evidence`
returns `status: invalid`, correct the draft from the returned errors and retry. After it succeeds,
report the committed result and pause.
