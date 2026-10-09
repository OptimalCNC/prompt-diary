"""Preserve semantic roles needed by Notion without changing the shared layout."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from prompt_diary.generate.prompts import TEAM_LEARNING_PATTERN_KINDS
from prompt_diary.generate.rendering.layout import (
    WORK_ITEM_CONTEXT_LABEL,
    Block,
    Document,
    Group,
    ListBlock,
    Prose,
    Section,
    Toggle,
    build_layout,
)

__all__ = [
    "DirectionProse",
    "PracticeProse",
    "ResultList",
    "build_notion_layout",
    "with_notion_project_labels",
]


@dataclass(frozen=True, kw_only=True)
class DirectionProse(Prose):
    """A work summary with its human or agent role retained."""

    role: Literal["Human direction", "Agent response"]


@dataclass(frozen=True, kw_only=True)
class PracticeProse(Prose):
    """A learning statement whose supporting fields remain separate."""

    rationale: str
    recurrence: str


@dataclass(frozen=True, kw_only=True)
class ResultList(ListBlock):
    """Work results distinguished from terminal states without an outcome."""

    label: Literal["Outcomes", "Recorded result"]


def with_notion_project_labels(report: dict[str, Any]) -> dict[str, Any]:
    """Disambiguate repeated display labels, retaining the original report and project keys."""
    projects = _list(report.get("projects"))
    counts = Counter(_text(_mapping(project).get("project_label")) for project in projects)
    if all(count == 1 for count in counts.values()):
        return report
    seen: Counter[str] = Counter()
    labeled: list[object] = []
    for project in projects:
        source = _mapping(project)
        label = _text(source.get("project_label"))
        seen[label] += 1
        labeled.append(
            {**source, "project_label": f"{label} ·{seen[label]}"} if counts[label] > 1 else project
        )
    return {**report, "projects": labeled}


def build_notion_layout(
    report: dict[str, Any], *, evidence_chains: tuple[Group, ...] = ()
) -> Document:
    """Enrich shared blocks from source fields before their semantic distinctions are lost."""
    document = build_layout(report, evidence_chains=evidence_chains)
    work, engagement, learning, *appendix = document.children
    projects = [
        project for project in _mappings(report.get("projects")) if _list(project.get("work_items"))
    ]
    if projects:
        work = replace(
            work,
            children=tuple(
                _project_group(cast("Group", group), project)
                for group, project in zip(work.children, projects, strict=True)
            ),
        )
    return replace(
        document,
        children=(work, engagement, _learning_section(learning, report), *appendix),
    )


def _project_group(group: Group, project: dict[str, Any]) -> Group:
    items = _mappings(project.get("work_items"))
    material = [item for item in items if item.get("kind") == "material_work_item"]
    minor = [item for item in items if item.get("kind") != "material_work_item"]
    children = [group.children[0], _work_list(cast("ListBlock", group.children[1]), material)]
    if minor:
        toggle = cast("Toggle", group.children[2])
        children.append(
            replace(toggle, children=(_work_list(cast("ListBlock", toggle.children[0]), minor),))
        )
    return replace(group, children=tuple(children))


def _work_list(block: ListBlock, sources: list[dict[str, Any]]) -> ListBlock:
    return replace(
        block,
        items=tuple(
            _work_item(cast("Group", group), source)
            for group, source in zip(block.items, sources, strict=True)
        ),
    )


def _work_item(group: Group, source: dict[str, Any]) -> Group:
    context: list[DirectionProse] = []
    trigger, response = source.get("trigger_summary"), source.get("agent_reaction_summary")
    if isinstance(trigger, str) and trigger:
        context.append(DirectionProse(trigger, role="Human direction"))
    if isinstance(response, str) and response:
        context.append(DirectionProse(response, role="Agent response"))
    children: list[Block] = []
    for block in group.children:
        if isinstance(block, Toggle) and block.label == WORK_ITEM_CONTEXT_LABEL:
            children.append(replace(block, children=tuple(context)))
        elif isinstance(block, ListBlock):
            label = "Outcomes" if _list(source.get("outcomes")) else "Recorded result"
            children.append(ResultList(block.style, block.items, label=label))
        else:
            children.append(block)
    return replace(group, children=tuple(children))


def _learning_section(section: Section, report: dict[str, Any]) -> Section:
    patterns = _mappings(_mapping(report.get("team_learning")).get("patterns"))
    if not patterns:
        return section
    sources = [
        matching
        for kind in TEAM_LEARNING_PATTERN_KINDS
        if (matching := [pattern for pattern in patterns if pattern.get("kind") == kind.value])
    ]
    groups: list[Group] = []
    for block, entries in zip(section.children[1:-1], sources, strict=True):
        group = cast("Group", block)
        listing = cast("ListBlock", group.children[0])
        leaves: list[PracticeProse] = []
        for leaf, source in zip(listing.items, entries, strict=True):
            prose = cast("Prose", leaf)
            leaves.append(
                PracticeProse(
                    _text(source.get("statement")),
                    citation=prose.citation,
                    tags=prose.tags,
                    rationale=_text(source.get("rationale")),
                    recurrence=_text(source.get("recurrence")),
                )
            )
        groups.append(replace(group, children=(replace(listing, items=tuple(leaves)),)))
    return replace(section, children=(section.children[0], *groups, section.children[-1]))


def _mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _mappings(value: object) -> list[dict[str, Any]]:
    return [_mapping(item) for item in _list(value)]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
