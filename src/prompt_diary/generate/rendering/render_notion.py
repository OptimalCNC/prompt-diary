"""Render a project-first, evidenced daily report as native Notion blocks.

Project summaries stay visible for scanning the day's work; each work item folds its human
direction, agent response, results, limits, and original messages into one native toggle.
Engagement and team learning each keep their reading and qualifications together, followed by
headings and supporting paragraphs. Spacing and typography establish hierarchy; routine content
does not need a highlighted container. Evidence remains in a collapsed appendix with citations.

The Notion-local layout preserves semantic fields that the shared prose layout combines. Rendering
introduces only navigation labels, never new claims. Model strings enter literal ``text.content``
fields only; annotations and link-target metadata are renderer-owned. The publisher resolves those
citation targets and batches the shallow block tree into requests.

Rich text is split into at most 2000 characters per run and capped at 100 runs per block, with a
fixed truncation marker for oversized content. Request limits belong to the publisher.
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
    Section,
    Tag,
    Toggle,
    load_evidence_appendix,
)
from prompt_diary.generate.rendering.notion_layout import (
    DirectionProse,
    PracticeProse,
    ResultList,
    build_notion_layout,
    with_notion_project_labels,
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

# Sections own the top heading level; projects deepen once, and work items become toggles.
_SECTION_HEADING_LEVEL = 1
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

_MINOR_ACTIVITY_LABEL = "Minor activity"
# Role colors aid scanning; only the short labels carry color, leaving the narrative neutral.
_ROLE_LABEL_COLORS = {"Human direction": "blue", "Agent response": "purple"}


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
    report = with_notion_project_labels(_load_json(workspace_path / _REPORT_NAME))
    evidence_chains = load_evidence_appendix(workspace_path=workspace_path, report=report)
    payload = render_notion(build_notion_layout(report, evidence_chains=evidence_chains))
    return _write_atomic(workspace_path / _OUTPUT_NAME, _payload_json(payload))


def render_notion(document: Document) -> NotionPagePayload:
    """Serialize a document, with semantic roles supplied by :func:`build_notion_layout`."""
    children: list[dict[str, Any]] = []
    for section in document.children:
        if section.title == EVIDENCE_APPENDIX_TITLE:
            children.append(
                _evidence_appendix_toggle(_render_blocks(section.children, heading_level=2))
            )
        else:
            children.append(_heading(_SECTION_HEADING_LEVEL, section.title, ()))
            if section.title == "Work by Project":
                for child in section.children:
                    children.extend(_render_project(child))
            elif section.title in {"Engagement Assessment", "Team Learning"}:
                children.extend(_render_reading(section))
            else:
                children.extend(_render_blocks(section.children, heading_level=2))
    return NotionPagePayload(
        title=document.title,
        properties=dict(document.properties),
        children=children,
    )


def _render_project(block: Block) -> list[dict[str, Any]]:
    if not isinstance(block, Group):
        return _render_one(block, heading_level=2)
    children = [_heading(2, block.label, ())]
    for child in block.children:
        if isinstance(child, Prose):
            children.append(_paragraph(child, separate_metadata=True))
        else:
            children.extend(_render_one(child, heading_level=3))
    return children


def _render_reading(section: Section) -> list[dict[str, Any]]:
    """Keep each assessment's qualifications with its lead, and group supporting observations."""
    limits = [child for child in section.children if isinstance(child, Callout)]
    blocks: list[dict[str, Any]] = []
    for child in section.children:
        if isinstance(child, Prose):
            blocks.append(_paragraph(child, separate_metadata=True))
            blocks.extend(
                _labeled_paragraph("Reading limits\n", limit.text, muted=True) for limit in limits
            )
        elif isinstance(child, Group):
            blocks.append(_heading(2, child.label, ()))
            practices = [
                item
                for entry in child.children
                if isinstance(entry, ListBlock)
                for item in entry.items
                if isinstance(item, PracticeProse)
            ]
            if practices:
                for practice in practices:
                    blocks.extend(_render_practice(practice))
            else:
                blocks.extend(_render_blocks(child.children, heading_level=3))
        elif not isinstance(child, Callout):
            blocks.extend(_render_one(child, heading_level=2))
    return blocks


def _render_practice(practice: PracticeProse) -> list[dict[str, Any]]:
    children = [
        _block("paragraph", {"rich_text": _label_rich_text(practice.text, practice.tags)}),
        _labeled_paragraph("Why it matters", practice.rationale),
        _labeled_paragraph("Recurrence", practice.recurrence, muted=True),
    ]
    if practice.citation is not None:
        children.append(
            _block(
                "paragraph",
                {
                    "rich_text": [
                        *_text_runs("Evidence  ", annotations={"bold": True, "color": "gray"}),
                        *_citation_runs(practice.citation),
                    ]
                },
            )
        )
    return children


def _labeled_paragraph(label: str, text: str, *, muted: bool = False) -> dict[str, Any]:
    return _block(
        "paragraph",
        {
            "rich_text": [
                *_text_runs(
                    f"{label}  ",
                    annotations={"bold": True, "color": _ROLE_LABEL_COLORS.get(label, "gray")},
                ),
                *_text_runs(text, annotations={"color": "gray"} if muted else None),
            ]
        },
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
        # Evidence project groups render as headings; work groups become toggles in lists,
        # while assessment groups are handled by ``_render_reading``.
        tags, body = _split_tags(block)
        return _render_container(block.label, tags, body, heading_level=heading_level)
    if isinstance(block, DirectionProse):
        return [_labeled_paragraph(block.role, block.text)]
    if isinstance(block, Prose):
        return [_paragraph(block)]
    if isinstance(block, ListBlock):
        return _render_list(block, heading_level=heading_level)
    if isinstance(block, Toggle):
        if block.label == _MINOR_ACTIVITY_LABEL:
            return [
                _section_label(block.label, muted=True),
                *[
                    item
                    for child in block.children
                    if isinstance(child, ListBlock)
                    for item in _render_list(child, heading_level=heading_level, minor=True)
                ],
            ]
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


def _render_list(
    block: ListBlock, *, heading_level: int, minor: bool = False
) -> list[dict[str, Any]]:
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
            # The collapsed reading path needs task names; repeated metadata belongs in details.
            metadata = _tag_runs(tags, separator="")
            if metadata:
                children.insert(0, _block("paragraph", {"rich_text": metadata}))
            toggle = _toggle(_text_runs(item.label), children)
            if minor:
                toggle["toggle"]["color"] = "gray"
            blocks.append(toggle)
        elif isinstance(item, Prose):
            blocks.append(_list_item_block(_list_item_type(block.style), _prose_rich_text(item)))
    return blocks


def _render_work_item_body(
    children: tuple[Block, ...], *, heading_level: int
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for child in children:
        if isinstance(child, Toggle) and child.label == WORK_ITEM_USER_MESSAGES_LABEL:
            messages = [
                _section_label("Original user messages", muted=True),
                *_render_blocks(child.children, heading_level=heading_level),
            ]
        elif isinstance(child, Toggle) and child.label == WORK_ITEM_CONTEXT_LABEL:
            blocks.extend(_render_blocks(child.children, heading_level=heading_level))
        elif isinstance(child, ListBlock):
            if child.items:
                label = child.label if isinstance(child, ResultList) else WORK_ITEM_OUTCOMES_LABEL
                blocks.append(_section_label(label))
                blocks.extend(_render_list(child, heading_level=heading_level))
        else:
            blocks.extend(_render_one(child, heading_level=heading_level))
    return [*blocks, *messages]


def _split_tags(group: Group) -> tuple[tuple[Tag, ...], tuple[Block, ...]]:
    tags = tuple(child for child in group.children if isinstance(child, Tag))
    body = tuple(child for child in group.children if not isinstance(child, Tag))
    return tags, body


def _heading(level: int, title: str, tags: tuple[Tag, ...]) -> dict[str, Any]:
    key = f"heading_{min(level, _MAX_HEADING_LEVEL)}"
    return _block(key, {"rich_text": _label_rich_text(title, tags)})


def _paragraph(prose: Prose, *, separate_metadata: bool = False) -> dict[str, Any]:
    return _block(
        "paragraph", {"rich_text": _prose_rich_text(prose, separate_metadata=separate_metadata)}
    )


def _section_label(label: str, *, muted: bool = False) -> dict[str, Any]:
    annotations: dict[str, str | bool] = {"bold": True}
    if muted:
        annotations["color"] = "gray"
    return _block("paragraph", {"rich_text": _text_runs(label, annotations=annotations)})


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
    # The shared layout's limit tone describes qualifications, not warning severity.
    if block.tone == "quote":
        return _block("quote", {"rich_text": _text_runs(block.text)})
    return _labeled_paragraph("Limits", block.text)


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


def _prose_rich_text(prose: Prose, *, separate_metadata: bool = False) -> list[dict[str, Any]]:
    # Summaries give sources their own line; short outcome bullets keep their references inline.
    runs = _text_runs(prose.text)
    metadata = _tag_runs(prose.tags, separator="" if separate_metadata else " · ")
    if prose.citation is not None:
        if metadata or not separate_metadata:
            metadata.append(_text(" "))
        metadata.extend(_citation_runs(prose.citation))
    if metadata and separate_metadata:
        runs.append(_text("\n"))
    return [*runs, *metadata]


def _label_rich_text(label: str, tags: tuple[Tag, ...]) -> list[dict[str, Any]]:
    # Keep the title prominent; recorded state is metadata, not a latest-status verdict.
    return [
        *_text_runs(label, annotations={"bold": True}),
        *_tag_runs(tags, separator=" — "),
    ]


def _tag_runs(tags: tuple[Tag, ...], *, separator: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for tag in tags:
        if not tag.value:
            continue
        if runs or separator:
            runs.append(_text(" · " if runs else separator, annotations={"color": "gray"}))
        label = (
            f"{tag.value} confidence"
            if tag.scale == "confidence"
            else f"Recorded state: {tag.value}"
            if tag.scale == "disposition"
            else tag.value
        )
        runs.extend(_text_runs(label, annotations={"color": "gray"}))
    return runs


def _citation_runs(citation: Citation) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for index, ref in enumerate(citation.refs):
        if index:
            runs.append(_text("; ", annotations={"color": "gray"}))
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


def _text(content: str, *, annotations: dict[str, str | bool] | None = None) -> dict[str, Any]:
    # Annotations are renderer-owned; model content stays literal, never markup or a link.
    run: dict[str, Any] = {"type": "text", "text": {"content": content}}
    if annotations is not None:
        run["annotations"] = annotations
    return run


def _citation_text(content: str, *, link_target: dict[str, Any] | None = None) -> dict[str, Any]:
    # A citation placeholder. It stays plain text in the artifact; the publisher may replace
    # metadata-targeted runs with native Notion page mentions once target page IDs are known.
    run = _text(content, annotations={"color": "gray"})
    if link_target is not None:
        run[LINK_TARGET_METADATA_KEY] = link_target
    return run


def _text_runs(
    content: str, *, annotations: dict[str, str | bool] | None = None
) -> list[dict[str, Any]]:
    return [_text(chunk, annotations=annotations) for chunk in _chunks(content)]


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
