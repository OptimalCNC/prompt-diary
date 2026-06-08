"""The engine-independent abstract layout for the daily report.

Rendering goes ``daily-report.json`` → **abstract layout** → ``{report.md, Notion, …}``. This
module owns the middle term: a tree of presentation primitives that fixes the report's *structure*
— its sections, their order, and the blocks inside them — without any engine's syntax.
:func:`build_layout` projects a finalized report model into that tree; each engine renderer (see
:mod:`prompt_diary.generate.rendering.render_markdown`) walks it and serializes the blocks.

The projection adds no claim-bearing content: every string a block carries — prose, citation, tag,
limit, verbatim message — is copied from the model, never synthesized or inferred. That "no new
claims" property is structural here: the only inputs are model strings, and the only outputs are
those same strings placed into blocks.

Cross-project citation scoping is decided here. Session refs are assigned per project, so a bare
``S0001:lines`` is ambiguous across projects. Inside **Work by Project** (rendered under a project
group) a citation is *unscoped* — the project is implied by the enclosing group. In the
cross-project sections (**Executive Summary**, **Engagement Assessment**, **Team Learning**) a
citation is *scoped* with its project label, resolved from the report's ``projects[]`` by the
citation's ``project_key``. ``build_layout`` sets ``scoped`` on every ref and resolves its
``project_label`` accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

from prompt_diary.generate.prompts import (
    ENGAGEMENT_DIMENSIONS,
    TEAM_LEARNING_PATTERN_KINDS,
    PromptEnumValue,
)

__all__ = [
    "WORK_ITEM_CONTEXT_LABEL",
    "WORK_ITEM_OUTCOMES_LABEL",
    "WORK_ITEM_USER_MESSAGES_LABEL",
    "Block",
    "Callout",
    "Citation",
    "Document",
    "Empty",
    "Group",
    "ListBlock",
    "Prose",
    "Section",
    "Tag",
    "Toggle",
    "build_layout",
]

# Note: a ``Table`` block (the doc's tabular primitive) is deliberately omitted. It is a Notion-only
# affordance — the cross-project slice the linear Markdown view does not provide — and no section of
# the current layout emits one. The Notion renderer ships without it; it remains a future
# cross-project-database affordance.

WORK_ITEM_CONTEXT_LABEL = "Context and Response"
WORK_ITEM_USER_MESSAGES_LABEL = "User Messages"
WORK_ITEM_OUTCOMES_LABEL = "Outcomes"


@dataclass(frozen=True)
class Citation:
    """One or more evidence references resolving to ``{session, lines}``.

    Each ref is a mapping ``{project_label, session_ref, lines, scoped}``. ``scoped`` records
    whether the ref renders with its ``project_label`` (cross-project sections) or bare (within a
    project group). ``turn_ref`` is intentionally absent — the report citation format is
    ``session_ref:lines`` and the turn is an internal identity, not reader-facing.
    """

    refs: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Tag:
    """One controlled value from a named scale (disposition, confidence, …)."""

    value: str
    scale: str


@dataclass(frozen=True)
class Prose:
    """A run of rich text, optionally carrying trailing tags and an inline citation.

    ``tags`` are evidence-quality markers lifted from the model (today only a per-claim
    ``confidence``) that render after the text and before the citation as ``· {value}``. They are
    distinct from the tags on a work-item ``Group`` heading: an outcome's confidence may differ from
    its work item's, so each claim carries its own.
    """

    text: str
    citation: Citation | None = None
    tags: tuple[Tag, ...] = ()


@dataclass(frozen=True)
class Callout:
    """Set-apart emphasis for limits, warnings, or gaps."""

    tone: str
    text: str


@dataclass(frozen=True)
class Empty:
    """An explicit empty-state shown when a section's data is absent."""

    fallback: str


@dataclass(frozen=True)
class ListBlock:
    """A sequence of items, each itself a block (prose or a nested cluster)."""

    style: Literal["bullet", "number"]
    items: tuple[Block, ...]


@dataclass(frozen=True)
class Toggle:
    """A collapsible region, collapsed by default; reveals its children on demand."""

    label: str
    children: tuple[Block, ...]


@dataclass(frozen=True)
class Group:
    """A labeled cluster of blocks repeated over a collection (a project, a work item, …)."""

    label: str
    children: tuple[Block, ...]


@dataclass(frozen=True)
class Section:
    """A titled, ordered region with a stated purpose; may nest."""

    title: str
    children: tuple[Block, ...]


@dataclass(frozen=True)
class Document:
    """The report root; ``properties`` are key/value metadata."""

    title: str
    properties: dict[str, str]
    children: tuple[Section, ...]


Block: TypeAlias = Section | Group | Prose | ListBlock | Tag | Citation | Callout | Toggle | Empty

_MATERIAL = "material_work_item"

# The window range separator: an en dash (U+2013), written as an escape so the source carries no
# confusable literal while the rendered header still reads as a start-to-end range.
_RANGE_DASH = "\u2013"

# Empty-state fallback bullets, verbatim from the doc's Markdown Rendering "Empty" list.
_EXEC_SUMMARY_FALLBACK = "No supported work claims found for this report window."
_WORK_FALLBACK = "No supported project-level work items found for this report window."
_ENGAGEMENT_FALLBACK = "Insufficient supported engagement evidence for this report window."
_TEAM_LEARNING_FALLBACK = "No supported reusable agent-driving pattern found."

# The engagement dimensions and team-learning pattern kinds render one group each, present only if
# the group carries entries. The value side and order are taken from the single source the model
# also parses against — ``ENGAGEMENT_DIMENSIONS`` / ``TEAM_LEARNING_PATTERN_KINDS`` in ``prompts`` —
# so a renamed enum value cannot silently drop a group here. Only the reader-facing display label
# (e.g. ``direction`` → ``Direction``) is local; ``_grouping`` zips each enum value to its label and
# fails loudly if a value has none, so adding an enum value without a label is caught immediately.
_ENGAGEMENT_LABELS: dict[str, str] = {
    "direction": "Direction",
    "review": "Review",
    "correction": "Correction",
    "recovery": "Recovery",
}
_TEAM_LEARNING_LABELS: dict[str, str] = {
    "promote": "Promote",
    "avoid": "Avoid",
    "reuse": "Reuse",
}


def _grouping(
    values: tuple[PromptEnumValue, ...], labels: dict[str, str]
) -> tuple[tuple[str, str], ...]:
    """Pair each controlled value with its display label, in the enum's order.

    The enum is the authority for which groups exist and their order; ``labels`` only supplies the
    reader-facing heading. A value with no label is a layout bug (a dimension/kind added upstream
    without a heading), so it raises rather than rendering an unlabeled or dropped group.
    """
    missing = [item.value for item in values if item.value not in labels]
    if missing:  # pragma: no cover - guards against an upstream enum/label drift
        raise KeyError(_MISSING_LABEL_MESSAGE.format(missing))
    return tuple((item.value, labels[item.value]) for item in values)


_MISSING_LABEL_MESSAGE = "no display label for engagement/team-learning value(s): {}"


_ENGAGEMENT_GROUPS = _grouping(ENGAGEMENT_DIMENSIONS, _ENGAGEMENT_LABELS)
_TEAM_LEARNING_GROUPS = _grouping(TEAM_LEARNING_PATTERN_KINDS, _TEAM_LEARNING_LABELS)

# The standing engagement / team-learning limit callouts named in the doc, appended after any
# agent-named limits so the honest boundary always shows even when the agent named none.
_ENGAGEMENT_STANDING_LIMIT = (
    "Offline thinking and review are not visible, and interaction precision is limited to the "
    "work-item grain."
)
_TEAM_LEARNING_STANDING_LIMIT = (
    "Productivity is read from observable proxies, never a precise effort metric; single-day "
    "evidence — recurrence and over-time trends need cross-day data (deferred)."
)


def build_layout(report: dict[str, Any]) -> Document:
    """Project a finalized daily-report model into the abstract layout tree."""
    labels = _project_labels(report)
    return Document(
        title=f"Prompt Diary Report — {_str(report.get('report_date'))}",
        properties=_properties(report),
        children=(
            _executive_summary_section(report, labels),
            _work_by_project_section(report, labels),
            _engagement_section(report, labels),
            _team_learning_section(report, labels),
        ),
    )


def _properties(report: dict[str, Any]) -> dict[str, str]:
    window = _mapping(report.get("window"))
    confidence = report.get("overall_confidence")
    return {
        # ``report_date`` is carried for the Notion renderer's Date property column; the Markdown
        # header reads only status/window/overall_confidence, so it is unaffected by the extra key.
        "report_date": _str(report.get("report_date")),
        "status": _str(report.get("status")),
        "window": (
            f"{_str(window.get('start'))}{_RANGE_DASH}{_str(window.get('end'))}, "
            f"{_str(window.get('timezone'))}"
        ),
        # An empty report has no per-claim confidences to roll up; the header says so plainly.
        "overall_confidence": confidence if isinstance(confidence, str) else "n/a",
    }


def _project_labels(report: dict[str, Any]) -> dict[str, str]:
    """Map each project_key to its project_label so cross-project citations can be scoped."""
    labels: dict[str, str] = {}
    for project in _list(report.get("projects")):
        mapping = _mapping(project)
        labels[_str(mapping.get("project_key"))] = _str(mapping.get("project_label"))
    return labels


# --- Executive Summary ------------------------------------------------------------------------


def _executive_summary_section(report: dict[str, Any], labels: dict[str, str]) -> Section:
    summary = _mapping(report.get("executive_summary"))
    top = _list(summary.get("top_outcomes"))
    open_items = _list(summary.get("open_items"))
    if not top and not open_items:
        return Section("Executive Summary", (Empty(_EXEC_SUMMARY_FALLBACK),))
    return Section(
        "Executive Summary",
        (
            _claim_list(top, labels, scoped=True),
            _claim_list(open_items, labels, scoped=True),
        ),
    )


def _claim_list(entries: list[Any], labels: dict[str, str], *, scoped: bool) -> ListBlock:
    return ListBlock(
        "bullet",
        tuple(_claim_prose(_mapping(entry), labels, scoped=scoped) for entry in entries),
    )


def _claim_prose(entry: dict[str, Any], labels: dict[str, str], *, scoped: bool) -> Prose:
    return Prose(_str(entry.get("text")), _citation(entry.get("citations"), labels, scoped=scoped))


# --- Work by Project --------------------------------------------------------------------------


def _work_by_project_section(report: dict[str, Any], labels: dict[str, str]) -> Section:
    projects = [
        _mapping(project)
        for project in _list(report.get("projects"))
        if _list(_mapping(project).get("work_items"))
    ]
    if not projects:
        return Section("Work by Project", (Empty(_WORK_FALLBACK),))
    # Work-by-Project citations are unscoped (project implied by the group), so no label map here.
    del labels
    return Section("Work by Project", tuple(_project_group(project) for project in projects))


def _project_group(project: dict[str, Any]) -> Group:
    work_items = [_mapping(item) for item in _list(project.get("work_items"))]
    material = [item for item in work_items if item.get("kind") == _MATERIAL]
    minor = [item for item in work_items if item.get("kind") != _MATERIAL]
    messages_by_turn = _messages_by_turn(project)
    children: list[Block] = [_project_summary_prose(project)]
    children.append(
        ListBlock("bullet", tuple(_work_item_group(item, messages_by_turn) for item in material))
    )
    if minor:
        children.append(_minor_activity_toggle(minor, messages_by_turn))
    return Group(_str(project.get("project_label")), tuple(children))


def _project_summary_prose(project: dict[str, Any]) -> Prose:
    summary = _mapping(project.get("summary"))
    # Within the project group, the summary's citations are unscoped (project is implied).
    return Prose(_str(summary.get("text")), _citation(summary.get("citations"), {}, scoped=False))


def _work_item_group(
    item: dict[str, Any], messages_by_turn: dict[tuple[str, str], list[str]]
) -> Group:
    children: list[Block] = []
    disposition = item.get("disposition")
    if isinstance(disposition, str):
        children.append(Tag(disposition, "disposition"))
    children.append(Tag(_str(item.get("confidence")), "confidence"))
    why = _why_toggle(item)
    if why is not None:
        children.append(why)
    user_messages = _user_messages_toggle(item, messages_by_turn)
    if user_messages is not None:
        children.append(user_messages)
    children.append(_outcomes_block(item))
    children.extend(_limit_callouts(item))
    return Group(_str(item.get("title")), tuple(children))


def _why_toggle(item: dict[str, Any]) -> Toggle | None:
    """The work item's trigger summary, plus its agent reaction when present."""
    trigger = item.get("trigger_summary")
    reaction = item.get("agent_reaction_summary")
    parts = [part for part in (trigger, reaction) if isinstance(part, str) and part]
    if not parts:
        return None
    # Trigger and agent reaction render as separate paragraphs inside the toggle.
    return Toggle(WORK_ITEM_CONTEXT_LABEL, tuple(Prose(part) for part in parts))


def _user_messages_toggle(
    item: dict[str, Any], messages_by_turn: dict[tuple[str, str], list[str]]
) -> Toggle | None:
    """The verbatim ``source_user_messages`` for the work item's covered turns.

    Untrusted display content: each message is carried verbatim as a ``Callout(tone="quote")`` so
    the renderer shows it quoted and escaped, never interpreted as Markdown or layout. Returns
    ``None`` when the covered turns carry no messages (e.g. a gap turn), mirroring ``_why_toggle``,
    so an empty toggle is not emitted.
    """
    quotes: list[Block] = []
    for ref in _list(item.get("covered_turns")):
        mapping = _mapping(ref)
        key = (_str(mapping.get("session_ref")), _str(mapping.get("turn_ref")))
        quotes.extend(Callout("quote", message) for message in messages_by_turn.get(key, []))
    if not quotes:
        return None
    return Toggle(WORK_ITEM_USER_MESSAGES_LABEL, tuple(quotes))


def _outcomes_block(item: dict[str, Any]) -> ListBlock:
    """What changed, one list item per outcome; a material-less item shows its terminal states."""
    outcomes = _list(item.get("outcomes"))
    if outcomes:
        items = tuple(_outcome_prose(_mapping(outcome)) for outcome in outcomes)
    else:
        items = tuple(
            _terminal_state_prose(_mapping(state)) for state in _list(item.get("terminal_states"))
        )
    return ListBlock("bullet", items)


def _terminal_state_prose(state: dict[str, Any]) -> Prose:
    # For a no-outcome material item the terminal summary is the visible claim, so it carries its
    # citation — unscoped within the project group, like an outcome. A terminal state has no
    # per-claim confidence in the model, so (unlike an outcome) it renders no confidence tag.
    return Prose(_str(state.get("summary")), _citation(state.get("citations"), {}, scoped=False))


def _outcome_prose(outcome: dict[str, Any]) -> Prose:
    # Within a project group, outcome citations are unscoped. The outcome carries its own confidence
    # (it may differ from the work item's), rendered inline as a tag per the doc layout.
    return Prose(
        _str(outcome.get("what_changed")),
        _citation(outcome.get("citations"), {}, scoped=False),
        tags=_confidence_tags(outcome),
    )


def _limit_callouts(item: dict[str, Any]) -> list[Callout]:
    return [
        Callout("limit", limit) for limit in _list(item.get("limits")) if isinstance(limit, str)
    ]


def _minor_activity_toggle(
    minor: list[dict[str, Any]], messages_by_turn: dict[tuple[str, str], list[str]]
) -> Toggle:
    return Toggle(
        "Minor activity",
        (ListBlock("bullet", tuple(_work_item_group(item, messages_by_turn) for item in minor)),),
    )


def _messages_by_turn(project: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    by_turn: dict[tuple[str, str], list[str]] = {}
    for entry in _list(project.get("source_user_messages")):
        mapping = _mapping(entry)
        key = (_str(mapping.get("session_ref")), _str(mapping.get("turn_ref")))
        by_turn[key] = [
            message for message in _list(mapping.get("messages")) if isinstance(message, str)
        ]
    return by_turn


# --- Engagement Assessment --------------------------------------------------------------------


def _engagement_section(report: dict[str, Any], labels: dict[str, str]) -> Section:
    engagement = report.get("engagement_assessment")
    if engagement is None:
        return Section("Engagement Assessment", (Empty(_ENGAGEMENT_FALLBACK),))
    mapping = _mapping(engagement)
    children: list[Block] = [_cited_text_prose(mapping.get("overall_reading"), labels)]
    children.extend(
        _dimension_group(label, dimension, _list(mapping.get("observations")), labels)
        for dimension, label in _ENGAGEMENT_GROUPS
        if _has_kind(_list(mapping.get("observations")), "dimension", dimension)
    )
    children.append(_limits_callout(mapping.get("limits"), _ENGAGEMENT_STANDING_LIMIT))
    return Section("Engagement Assessment", tuple(children))


def _dimension_group(
    label: str, dimension: str, observations: list[Any], labels: dict[str, str]
) -> Group:
    items = tuple(
        Prose(
            _str(mapping.get("statement")),
            _citation(mapping.get("citations"), labels, scoped=True),
            tags=_confidence_tags(mapping),
        )
        for mapping in (_mapping(obs) for obs in observations)
        if mapping.get("dimension") == dimension
    )
    return Group(label, (ListBlock("bullet", items),))


# --- Team Learning ----------------------------------------------------------------------------


def _team_learning_section(report: dict[str, Any], labels: dict[str, str]) -> Section:
    learning = report.get("team_learning")
    if learning is None:
        return Section("Team Learning", (Empty(_TEAM_LEARNING_FALLBACK),))
    mapping = _mapping(learning)
    children: list[Block] = [_cited_text_prose(mapping.get("takeaways"), labels)]
    children.extend(
        _pattern_group(label, kind, _list(mapping.get("patterns")), labels)
        for kind, label in _TEAM_LEARNING_GROUPS
        if _has_kind(_list(mapping.get("patterns")), "kind", kind)
    )
    children.append(_limits_callout(mapping.get("limits"), _TEAM_LEARNING_STANDING_LIMIT))
    return Section("Team Learning", tuple(children))


def _pattern_group(label: str, kind: str, patterns: list[Any], labels: dict[str, str]) -> Group:
    items = tuple(
        Prose(
            _pattern_text(mapping),
            _citation(mapping.get("citations"), labels, scoped=True),
            tags=_confidence_tags(mapping),
        )
        for mapping in (_mapping(pattern) for pattern in patterns)
        if mapping.get("kind") == kind
    )
    return Group(label, (ListBlock("bullet", items),))


def _pattern_text(pattern: dict[str, Any]) -> str:
    """Compose a pattern's required statement, rationale, and recurrence into one prose run.

    All three are lifted verbatim from the model and joined with fixed separators — no new claim is
    introduced, only the model's own fields placed together: ``{statement} — {rationale} ·
    recurrence: {recurrence}``.
    """
    return (
        f"{_str(pattern.get('statement'))} — {_str(pattern.get('rationale'))} "
        f"· recurrence: {_str(pattern.get('recurrence'))}"
    )


# --- shared helpers ---------------------------------------------------------------------------


def _cited_text_prose(value: object, labels: dict[str, str]) -> Prose:
    # The engagement overall_reading and team-learning takeaways are standalone judgments that carry
    # their own confidence (unlike a per-project summary), so the lead Prose shows it inline as a
    # ``· {confidence}`` tag — the same hedge outcomes/observations/patterns already render.
    mapping = _mapping(value)
    return Prose(
        _str(mapping.get("text")),
        _citation(mapping.get("citations"), labels, scoped=True),
        tags=_confidence_tags(mapping),
    )


def _confidence_tags(entry: dict[str, Any]) -> tuple[Tag, ...]:
    """The per-claim ``confidence`` as an inline tag, present only when the model carries one."""
    confidence = entry.get("confidence")
    return (Tag(confidence, "confidence"),) if isinstance(confidence, str) and confidence else ()


def _limits_callout(limits: object, standing: str) -> Callout:
    # Each limit is its own blockquote paragraph (joined with a blank-line break) so an agent-named
    # caveat and the always-present standing boundary read as distinct, not fused into one run-on
    # line. The standing-limit constant text is unchanged.
    named = [limit for limit in _list(limits) if isinstance(limit, str) and limit]
    return Callout("limit", "\n\n".join([*named, standing]))


def _has_kind(entries: list[Any], key: str, value: str) -> bool:
    return any(_mapping(entry).get(key) == value for entry in entries)


def _citation(value: object, labels: dict[str, str], *, scoped: bool) -> Citation | None:
    refs = [_citation_ref(_mapping(citation), labels, scoped=scoped) for citation in _list(value)]
    return Citation(tuple(refs)) if refs else None


def _citation_ref(
    citation: dict[str, Any], labels: dict[str, str], *, scoped: bool
) -> dict[str, Any]:
    project_key = _str(citation.get("project_key"))
    return {
        # Defensive fallback: a stale or cross-project project_key with no entry in the report's
        # projects[] resolves to the bare key rather than an empty label, so the ref stays legible.
        "project_label": labels.get(project_key, project_key),
        "session_ref": _str(citation.get("session_ref")),
        "lines": _str(citation.get("lines")),
        "scoped": scoped,
    }


def _mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""
