## Project Context

- Project key: {{ project_key }}
- Project metadata from `project.json`:

```json
{{ project_json }}
```

{% if committed_work_items %}
### Committed Work Items

{{ committed_work_items }}
{% endif %}

### Evidence Chains

{{ evidence_chains }}
