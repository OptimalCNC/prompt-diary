## Continue: cover the remaining turns

You reported that you were finished, but `write_work_item` shows that some indexed turns are still
not covered by any work item. Every indexed turn must belong to exactly one work item, so create work
items that account for the turns listed below and submit each one with `write_work_item`.

- Project key: {{ project_key }}

### Uncovered turns

{{ uncovered_turns }}

Work only from the evidence chains already shown to you earlier in this conversation; do not read any
files.

- For a turn marked **has an evidence chain**, group it into a work item — a new work item, or one
  like those you already created.
- For a turn marked **no evidence chain**, cover it with an `evidence_gap_item`. You may create more
  than one `evidence_gap_item`, and an `evidence_gap_item` may cover several such turns at once.

Reference turns as `{session_ref, turn_ref}`. If `write_work_item` returns `status: invalid`, correct
the work item from the returned errors and retry. Keep calling `write_work_item` until it reports that
no turns remain uncovered, then stop.
