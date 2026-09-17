## Session Context

- Process current working directory: the prepared report workspace root
- Project key: {{ project_key }}
- Project metadata from `project.json`:

```json
{{ project_json }}
```

- Session reference: {{ session_ref }}
- Session metadata from the prepared index:

```json
{{ session_index_record }}
```

## Turn Assignment

Assigned turn to extract now:

```json
{{ target_turn }}
```

Start now: extract this turn and make one successful `write_evidence` commit.
