"""Serialize the abstract layout to a Notion page payload (``report.notion.json``).

Notion is a second presentation engine for the daily report, beside Markdown. :func:`render_notion`
walks the :mod:`~prompt_diary.generate.rendering.layout` tree and serializes each block into
Notion block objects per the doc's Block→Notion mapping; :func:`render_notion_artifact` reads the
finalized ``daily-report.json``, builds the layout, renders it, and atomically writes the page
payload to ``report.notion.json`` at the workspace root. A separate publisher
(:mod:`~prompt_diary.generate.rendering.notion_publish`) pushes that payload to Notion.
The source-of-truth structure is documented in ``docs/src/generate/rendering.md#abstract-layout``;
update that section with any reader-facing layout change made here.

Like the Markdown renderer it only reads model strings carried by the layout blocks and synthesizes
no prose of its own. The injection-safety story is *structural and simpler than Markdown's*: every
model-derived string is placed only into a rich-text ``text.content`` field (plain text, no
``link``, default annotations). Notion stores ``content`` as literal text and never parses
Markdown/HTML inside it, so a session-derived string carrying ``</details>``, ``# heading``,
``[x](url)``, or a code span renders verbatim and cannot forge structure. There is therefore no
escaping pass: the one invariant is that model text never populates a ``text.link.url`` (or any
other interpreted field), and this renderer emits no link anywhere. Citation runs may carry
renderer-owned target metadata for the publisher to turn into native Notion inline links after the
target page IDs are known.

Mapping (the "best Notion" choices, not 1:1 with Markdown):

- ``Section`` → ``heading_2``; its children follow as sibling blocks.
- ``Group`` as a direct section child (a project, or an engagement / team-learning dimension) →
  ``heading_3``.
- ``Group`` as a list item (a work item) → a native ``toggle`` (label + disposition / confidence),
  its blocks nested inside — a collapsible record, the idiomatic Notion form for a titled cluster in
  a list. Position decides this, like the Markdown renderer's special-casing of a group in a list.
- ``Prose`` → a ``paragraph`` (standalone) or a ``bulleted_list_item`` / ``numbered_list_item``
  (in a list); its trailing confidence tags and inline citation metadata ride in the same rich-text
  array.
- ``ListBlock`` → a run of list-item blocks (prose items) or toggle blocks (group items).
- ``Toggle`` → a colored label callout followed by its children. Native toggles are reserved for
  work-item ``Group`` list items so the published page stays shallow and fast to append. Inside a
  work-item toggle, section groups are separated by divider blocks and outcome lists get the same
  label treatment as the context and user-message sections.
- ``Callout`` tone ``quote`` (a verbatim user message) → a ``quote`` block; tone ``limit`` → a
  ``callout`` block with a warning icon.
- ``Empty`` → a ``bulleted_list_item`` carrying the section's fallback text.

Notion content limits are honored in the emitted payload: each ``text.content`` is split into
≤2000-character runs, and each block's rich-text array is capped at ≤100 runs (a longer single
string is truncated with a fixed marker — see ``_cap_runs`` — because the publisher can split the
block *tree* across requests but not one block's rich-text array). The request-shaping limits (≤100
children / ≤1000 blocks per request and the ~2-level nesting depth per create call) are the
publisher's concern, since they constrain API requests, not the block tree this module builds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.generate.rendering.layout import (
    EVIDENCE_APPENDIX_TITLE,
    WORK_ITEM_CONTEXT_LABEL,
    WORK_ITEM_OUTCOMES_LABEL,
    WORK_ITEM_USER_MESSAGES_LABEL,
    Block,
    Callout,
    Citation,
    Document,
    Empty,
    EvidenceChainEntry,
    Group,
    ListBlock,
    Prose,
    Tag,
    Toggle,
    build_layout,
    load_evidence_appendix,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "EVIDENCE_APPENDIX_METADATA_KEY",
    "EVIDENCE_TARGET_METADATA_KEY",
    "LINK_TARGET_METADATA_KEY",
    "NotionPagePayload",
    "render_notion",
    "render_notion_artifact",
]

_REPORT_NAME = "daily-report.json"
_OUTPUT_NAME = "report.notion.json"

# A Section is the top-level region (the page title owns the page itself); a Group nested directly
# in a section deepens to heading_3. Notion has no heading past heading_3 in this mapping — deeper
# titled clusters (work items) become toggles, not headings — so the level is capped here.
_SECTION_HEADING_LEVEL = 2
_MAX_HEADING_LEVEL = 3

# Notion caps a rich-text ``content`` string at 2000 characters; a longer model string splits into
# several consecutive text runs (runs concatenate on display, so no text is lost).
_MAX_CONTENT = 2000

# Notion also caps a block's rich-text array at 100 objects. A string long enough to need more than
# this (>~200K characters) is truncated with the marker below — see ``_cap_runs``.
_MAX_RUNS_PER_BLOCK = 100
_TRUNCATION_MARKER = " [truncated]"

LINK_TARGET_METADATA_KEY = "_prompt_diary_link_target"
EVIDENCE_TARGET_METADATA_KEY = "_prompt_diary_evidence_target"
EVIDENCE_APPENDIX_METADATA_KEY = "_prompt_diary_evidence_appendix"

# The limit callout's icon — a warning sign, written as escapes so the source carries no literal
# emoji (U+26A0 warning sign + U+FE0F emoji-presentation selector).
_LIMIT_ICON = "\u26a0\ufe0f"

_MINOR_ACTIVITY_LABEL = "Minor activity"
_SECTION_LABEL_COLORS = {
    WORK_ITEM_CONTEXT_LABEL: "blue_background",
    WORK_ITEM_USER_MESSAGES_LABEL: "purple_background",
    WORK_ITEM_OUTCOMES_LABEL: "green_background",
    _MINOR_ACTIVITY_LABEL: "gray_background",
}


@dataclass(frozen=True)
class NotionPagePayload:
    """A Notion page ready to create: a title, metadata properties, and the body block children.

    ``properties`` are the report's metadata as plain strings (report_date, status, window,
    overall_confidence); the publisher maps them to the target database's property columns.
    ``title`` is the page title and ``children`` the body — Notion block objects (plain JSON dicts).
    """

    title: str
    properties: dict[str, str]
    children: list[dict[str, Any]]


def render_notion_artifact(*, workspace_path: Path) -> Path:
    """Render ``daily-report.json`` to ``report.notion.json`` and return the written path."""
    report = _load_json(workspace_path / _REPORT_NAME)
    evidence_chains = load_evidence_appendix(workspace_path=workspace_path, report=report)
    payload = render_notion(build_layout(report, evidence_chains=evidence_chains))
    return _write_atomic(workspace_path / _OUTPUT_NAME, _payload_json(payload))


def render_notion(document: Document) -> NotionPagePayload:
    """Serialize a layout :class:`Document` to a Notion page payload."""
    children: list[dict[str, Any]] = []
    for section in document.children:
        if section.title == EVIDENCE_APPENDIX_TITLE:
            children.append(
                _evidence_appendix_toggle(_render_blocks(section.children, heading_level=2))
            )
        else:
            children.extend(
                _render_container(
                    section.title, (), section.children, heading_level=_SECTION_HEADING_LEVEL
                )
            )
    return NotionPagePayload(
        title=document.title,
        properties=dict(document.properties),
        children=children,
    )


def _render_container(
    title: str, tags: tuple[Tag, ...], children: tuple[Block, ...], *, heading_level: int
) -> list[dict[str, Any]]:
    # A titled region (a Section, or a Group rendered as a heading): the heading block, then its
    # children as the following sibling blocks (Notion headings do not contain their section body).
    return [
        _heading(heading_level, title, tags),
        *_render_blocks(children, heading_level=heading_level + 1),
    ]


def _render_blocks(children: tuple[Block, ...], *, heading_level: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for child in children:
        blocks.extend(_render_one(child, heading_level=heading_level))
    return blocks


def _render_one(block: Block, *, heading_level: int) -> list[dict[str, Any]]:
    if isinstance(block, Group):
        # A Group reached here is a direct section child (a project or a dimension), so it renders
        # as a heading; a work-item Group is a list item, handled by ``_render_list`` as a toggle.
        tags, body = _split_tags(block)
        return _render_container(block.label, tags, body, heading_level=heading_level)
    if isinstance(block, Prose):
        return [_paragraph(block)]
    if isinstance(block, ListBlock):
        return _render_list(block, heading_level=heading_level)
    if isinstance(block, Toggle):
        return [
            _section_label(block.label),
            *_render_blocks(block.children, heading_level=heading_level),
        ]
    if isinstance(block, Callout):
        return [_callout(block)]
    if isinstance(block, Empty):
        return [_list_item_block("bulleted_list_item", _text_runs(block.fallback))]
    if isinstance(block, EvidenceChainEntry):
        return [_evidence_chain_entry(block)]
    # Tag / Citation / Section never reach here standalone in a well-formed layout.
    return []  # pragma: no cover


def _render_list(block: ListBlock, *, heading_level: int) -> list[dict[str, Any]]:
    """Serialize a list: prose items become list-item blocks, group items become toggles.

    A list of *leaves* (Prose — outcomes, observations, synthesized judgments) renders as bulleted /
    numbered list items. A list whose items are *clusters* (a work-item ``Group``) renders each as a
    native ``toggle`` instead — the faithful Notion form for a titled, collapsible record. The two
    never mix in one list.
    """
    blocks: list[dict[str, Any]] = []
    for item in block.items:
        # The layout guarantees a list's items are uniformly work-item ``Group``s or leaf
        # ``Prose``s (see ``layout.py``); no other block kind is ever a list item, so the two
        # arms below are exhaustive.
        if isinstance(item, Group):
            tags, body = _split_tags(item)
            children = _render_work_item_body(body, heading_level=heading_level + 1)
            blocks.append(_toggle(_label_rich_text(item.label, tags), children))
        elif isinstance(item, Prose):
            blocks.append(_list_item_block(_list_item_type(block.style), _prose_rich_text(item)))
    return blocks


def _render_work_item_body(
    children: tuple[Block, ...], *, heading_level: int
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    previous_was_limit = False
    for child in children:
        rendered = _render_work_item_body_block(child, heading_level=heading_level)
        if not rendered:
            continue
        current_is_limit = isinstance(child, Callout) and child.tone == "limit"
        if blocks and not (previous_was_limit and current_is_limit):
            blocks.append(_divider())
        blocks.extend(rendered)
        previous_was_limit = current_is_limit
    return blocks


def _render_work_item_body_block(block: Block, *, heading_level: int) -> list[dict[str, Any]]:
    if isinstance(block, Toggle):
        return [
            _section_label(block.label),
            *_render_blocks(block.children, heading_level=heading_level),
        ]
    if isinstance(block, ListBlock):
        return [
            _section_label(WORK_ITEM_OUTCOMES_LABEL),
            *_render_list(block, heading_level=heading_level),
        ]
    return _render_one(block, heading_level=heading_level)


def _split_tags(group: Group) -> tuple[tuple[Tag, ...], tuple[Block, ...]]:
    tags = tuple(child for child in group.children if isinstance(child, Tag))
    body = tuple(child for child in group.children if not isinstance(child, Tag))
    return tags, body


def _heading(level: int, title: str, tags: tuple[Tag, ...]) -> dict[str, Any]:
    key = f"heading_{min(level, _MAX_HEADING_LEVEL)}"
    return _block(key, {"rich_text": _label_rich_text(title, tags)})


def _paragraph(prose: Prose) -> dict[str, Any]:
    return _block("paragraph", {"rich_text": _prose_rich_text(prose)})


def _section_label(label: str) -> dict[str, Any]:
    return _block(
        "callout",
        {
            "rich_text": _text_runs(label),
            "color": _SECTION_LABEL_COLORS.get(label, "gray_background"),
        },
    )


def _divider() -> dict[str, Any]:
    return _block("divider", {})


def _toggle(rich_text: list[dict[str, Any]], children: list[dict[str, Any]]) -> dict[str, Any]:
    return _block("toggle", {"rich_text": rich_text, "children": children})


def _evidence_appendix_toggle(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **_block(
            "heading_1",
            {
                "rich_text": _text_runs(EVIDENCE_APPENDIX_TITLE),
                "is_toggleable": True,
                "children": children,
            },
        ),
        EVIDENCE_APPENDIX_METADATA_KEY: True,
    }


def _evidence_chain_entry(block: EvidenceChainEntry) -> dict[str, Any]:
    return {
        **_toggle(
            _text_runs(_ref_label(block.session_ref, block.turn_ref)),
            [
                *[
                    _list_item_block("bulleted_list_item", _prose_rich_text(item))
                    for item in block.items
                ],
                *[_callout(message) for message in block.messages],
            ],
        ),
        EVIDENCE_TARGET_METADATA_KEY: dict(block.target),
    }


def _callout(block: Callout) -> dict[str, Any]:
    # A verbatim user message renders as a quote; a limit / caveat as a callout with a warning icon.
    if block.tone == "quote":
        return _block("quote", {"rich_text": _text_runs(block.text)})
    return _block("callout", {"rich_text": _text_runs(block.text), "icon": {"emoji": _LIMIT_ICON}})


def _list_item_block(item_type: str, rich_text: list[dict[str, Any]]) -> dict[str, Any]:
    return _block(item_type, {"rich_text": rich_text})


def _list_item_type(style: str) -> str:
    return "numbered_list_item" if style == "number" else "bulleted_list_item"


def _block(block_type: str, body: dict[str, Any]) -> dict[str, Any]:
    # Cap the block's rich-text array at Notion's 100-object limit here — this is the single
    # chokepoint every block passes through, and the only layer that can enforce it: the publisher
    # splits the block *tree* across requests but cannot split one block's rich-text array.
    if "rich_text" in body:
        body = {**body, "rich_text": _cap_runs(body["rich_text"])}
    return {"object": "block", "type": block_type, block_type: body}


def _cap_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Notion rejects a block whose rich-text array exceeds 100 objects. A single model string long
    # enough to need >100 runs (>~200K characters — only a pasted verbatim user message would
    # realistically reach this) is truncated with a renderer-controlled marker run; the full text
    # stays in daily-report.json and the session transcript. The marker is fixed, not model-derived.
    if len(runs) <= _MAX_RUNS_PER_BLOCK:
        return runs
    return [*runs[: _MAX_RUNS_PER_BLOCK - 1], _text(_TRUNCATION_MARKER)]


def _prose_rich_text(prose: Prose) -> list[dict[str, Any]]:
    # text runs, then the inline confidence tag(s), then the citation placeholder runs. The citation
    # gets a leading space run because Notion does not insert whitespace between runs.
    runs = _text_runs(prose.text)
    if prose.tags:
        runs.append(_text(" · " + " · ".join(tag.value for tag in prose.tags)))
    if prose.citation is not None:
        runs.append(_text(" "))
        runs.extend(_citation_runs(prose.citation))
    return runs


def _label_rich_text(label: str, tags: tuple[Tag, ...]) -> list[dict[str, Any]]:
    # A heading / toggle label: the text, then any tags as a ``— value · value`` suffix run.
    runs = _text_runs(label)
    if tags:
        runs.append(_text(" — " + " · ".join(tag.value for tag in tags)))
    return runs


def _citation_runs(citation: Citation) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for index, ref in enumerate(citation.refs):
        if index:
            runs.append(_text("; "))
        target = _mapping(ref.get("target")) if _ref_str(ref, "anchor") else {}
        runs.extend(_citation_text_runs(_ref_text(ref), link_target=target or None))
    return runs


def _ref_text(ref: dict[str, Any]) -> str:
    body = _ref_label(_ref_str(ref, "session_ref"), _ref_str(ref, "turn_ref"))
    if ref.get("scoped"):
        return f"{_ref_str(ref, 'project_label')} · {body}"
    return body


def _ref_label(session_ref: str, turn_ref: str) -> str:
    return f"{session_ref}/{turn_ref}" if turn_ref else session_ref


def _ref_str(ref: dict[str, Any], key: str) -> str:
    value = ref.get(key, "")
    return value if isinstance(value, str) else ""


def _text(content: str) -> dict[str, Any]:
    # A plain rich-text run. No ``link`` and no annotations: model content is literal, never markup.
    return {"type": "text", "text": {"content": content}}


def _citation_text(content: str, *, link_target: dict[str, Any] | None = None) -> dict[str, Any]:
    # A citation placeholder. It stays plain text in the artifact; the publisher may replace
    # metadata-targeted runs with native Notion page mentions once target page IDs are known.
    run = _text(content)
    if link_target is not None:
        run[LINK_TARGET_METADATA_KEY] = link_target
    return run


def _text_runs(content: str) -> list[dict[str, Any]]:
    return [_text(chunk) for chunk in _chunks(content)]


def _citation_text_runs(
    content: str, *, link_target: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [_citation_text(chunk, link_target=link_target) for chunk in _chunks(content)]


def _chunks(content: str) -> list[str]:
    if not content:
        return []
    return [content[index : index + _MAX_CONTENT] for index in range(0, len(content), _MAX_CONTENT)]


def _payload_json(payload: NotionPagePayload) -> str:
    data = {
        "title": payload.title,
        "properties": payload.properties,
        "children": payload.children,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write_atomic(path: Path, text: str) -> Path:
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}
