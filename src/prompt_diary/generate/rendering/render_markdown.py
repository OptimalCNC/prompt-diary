"""Serialize the abstract layout to ``report.md``.

Markdown is a presentation format, not the source of truth for the report's structure or evidence
model. :func:`render_markdown` walks the :mod:`~prompt_diary.generate.rendering.layout` tree
and serializes each block per the doc's Block→Markdown mapping; :func:`render_report` reads the
finalized ``daily-report.json``, builds the layout, renders it, and atomically writes ``report.md``
to the workspace root.

The renderer only reads model strings carried by the layout blocks — it never reaches back to the
report, the evidence cards, or the work items, and it synthesizes no prose of its own. The "no new
claims" guarantee is therefore structural: a block's text is rendered as-is, a citation is formatted
from its stored ``session_ref``/``lines``, and verbatim user messages are quoted and escaped as
untrusted display content, never interpreted.

Every model-derived display string is session-derived and therefore a prompt-injection surface:
titles, summaries, statements, rationale, recurrence, limits, outcome / exec text, and project
labels can all carry active Markdown (links, images, headings, code spans) or, via embedded
newlines, forge a new block. So the renderer Markdown-neutralizes *all* of them — not just the
verbatim user messages — through one escaper: it HTML-escapes, backslash-escapes the Markdown
punctuation, and neutralizes a leading ``#`` so none of that activates. Single-block strings
(headings, prose) additionally collapse embedded newlines so one model string cannot open a second
block; callout text keeps its newlines because every callout line is re-prefixed with ``>``. Only
renderer-controlled tokens (the resolved ``session_ref``/``lines``, the controlled disposition /
confidence tags, the status / window / overall-confidence metadata, and the fixed Empty fallbacks)
stay on the plain HTML-escape — they cannot carry free model text.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from prompt_diary.generate.rendering.layout import (
    Block,
    Callout,
    Citation,
    Document,
    Empty,
    Group,
    ListBlock,
    Prose,
    Section,
    Tag,
    Toggle,
    build_layout,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

__all__ = ["render_markdown", "render_report"]

_REPORT_NAME = "daily-report.json"
_OUTPUT_NAME = "report.md"

# A Section starts at ``##`` (the Document title owns ``#``); each nesting level deepens by one.
_SECTION_BASE_LEVEL = 2
_MAX_HEADING_LEVEL = 6

# The Markdown punctuation neutralized in every model-derived string so links, images, emphasis, and
# code spans render literally. A leading ``#``/``>`` is handled per-line separately, since those are
# only special at the start of a line. Backslash-escaped in this order after the HTML-escape.
_MARKDOWN_PUNCTUATION = ("`", "*", "_", "[", "]", "(", ")", "!")


def render_report(*, workspace_path: Path) -> Path:
    """Render the workspace's ``daily-report.json`` to ``report.md`` and return the written path."""
    report = _load_json(workspace_path / _REPORT_NAME)
    text = render_markdown(build_layout(report))
    return _write_atomic(workspace_path / _OUTPUT_NAME, text)


def render_markdown(document: Document) -> str:
    """Serialize a layout :class:`Document` to a Markdown string."""
    lines: list[str] = [f"# {_escape(document.title)}", "", _properties_line(document.properties)]
    for section in document.children:
        lines.append("")
        lines.extend(_render_block(section, level=_SECTION_BASE_LEVEL))
    return "\n".join(lines) + "\n"


def _properties_line(properties: dict[str, str]) -> str:
    return (
        f"Status: {_escape(properties.get('status', ''))} · "
        f"Window: {_escape(properties.get('window', ''))} · "
        f"Overall confidence: {_escape(properties.get('overall_confidence', ''))}"
    )


def _render_block(block: Block, *, level: int) -> list[str]:
    if isinstance(block, Section):
        return _render_container(block.title, block.children, level=level, tags=())
    if isinstance(block, Group):
        return _render_group(block, level=level)
    if isinstance(block, Prose):
        return [_prose_line(block)]
    if isinstance(block, ListBlock):
        return _render_list(block, level=level)
    if isinstance(block, Toggle):
        return _render_toggle(block, level=level)
    if isinstance(block, Callout):
        return _render_callout(block)
    if isinstance(block, Empty):
        return [f"- {_escape(block.fallback)}"]
    # ``Tag`` and ``Citation`` never render standalone: a Tag rides on its Group heading and a
    # Citation is appended to its Prose, so this branch is unreachable for a well-formed layout.
    return []  # pragma: no cover


def _render_group(group: Group, *, level: int) -> list[str]:
    tags = tuple(child for child in group.children if isinstance(child, Tag))
    body = tuple(child for child in group.children if not isinstance(child, Tag))
    return _render_container(group.label, body, level=level, tags=tags)


def _render_container(
    title: str, children: tuple[Block, ...], *, level: int, tags: tuple[Tag, ...]
) -> list[str]:
    # A title is a single-block model string (a Section constant or a model-derived Group label),
    # so it is fully Markdown-neutralized and newline-collapsed: it cannot inject a link/image or
    # break out of its heading line into a forged block.
    heading = f"{'#' * min(level, _MAX_HEADING_LEVEL)} {_escape_inline(title)}{_tag_suffix(tags)}"
    lines: list[str] = [heading]
    for child in children:
        lines.append("")
        lines.extend(_render_block(child, level=level + 1))
    return lines


def _tag_suffix(tags: tuple[Tag, ...]) -> str:
    if not tags:
        return ""
    return " — " + " · ".join(_escape(tag.value) for tag in tags)


def _render_list(block: ListBlock, *, level: int) -> list[str]:
    """Serialize a list.

    A list of *leaves* (Prose — outcomes, observations, synthesized judgments) renders as
    ``-``/``1.`` bullets. A list whose items are *clusters* (a work-item ``Group``) renders each
    item as its own deepening sub-heading block instead: a heading cannot sit on a bullet line, so
    the heading form is the faithful Markdown for a titled cluster. The two never mix in one list.
    """
    lines: list[str] = []
    for index, item in enumerate(block.items, start=1):
        if index > 1:
            lines.append("")
        if isinstance(item, Group):
            lines.extend(_render_block(item, level=level))
        else:
            marker = f"{index}." if block.style == "number" else "-"
            lines.append(f"{marker} {_render_block(item, level=level)[0]}")
    return lines


def _render_toggle(block: Toggle, *, level: int) -> list[str]:
    lines: list[str] = ["<details>", f"<summary>{_escape(block.label)}</summary>", ""]
    for index, child in enumerate(block.children):
        if index:
            lines.append("")
        lines.extend(_render_block(child, level=level + 1))
    lines.extend(("", "</details>"))
    return lines


def _render_callout(block: Callout) -> list[str]:
    # Every callout carries model-derived text — a verbatim user message ("quote") or session
    # derived model prose such as a work-item limit. Both are Markdown-neutralized (HTML-escape +
    # punctuation + leading ``#``) so embedded markup cannot break out of the blockquote or the
    # enclosing <details>, and links/images/emphasis render literally rather than activating.
    # Newlines are kept (not collapsed): every produced line is re-prefixed with ``>``, so a newline
    # only opens another blockquote line, never a forged block — and a blank line in ``text``
    # separates blockquote paragraphs (one limit per paragraph).
    return [f"> {line}" if line else ">" for line in _escape_markdown(block.text).split("\n")]


def _escape(text: str) -> str:
    """HTML-escape a string so any embedded ``&``/``<``/``>`` renders as literal text.

    The base escape for the renderer-controlled tokens — the resolved ``session_ref``/``lines``, the
    controlled disposition / confidence tags, the status / window / overall-confidence metadata, and
    the fixed Empty fallbacks — so embedded HTML such as ``</details>`` cannot prematurely close a
    toggle or otherwise break the report structure. Free model text instead goes through
    :func:`_escape_markdown` / :func:`_escape_inline`, which build on this. The renderer's own
    Markdown punctuation is added around the escaped string, never escaped.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_markdown(text: str) -> str:
    """Neutralize a model-derived string: HTML-escape, then disable Markdown punctuation.

    On top of :func:`_escape`, backslash-escape the punctuation that activates Markdown — code
    spans, emphasis, links, and images — plus a leading ``#`` (heading) on each line, so the string
    renders exactly as written instead of becoming an active link, image, heading, or formatting
    run. A leading ``>`` (blockquote) needs no special case: :func:`_escape` has already turned it
    into ``&gt;``, which is not a blockquote marker. Newlines are preserved — callers that render
    into a single block collapse them via :func:`_escape_inline`; callout text keeps them because
    every callout line is re-prefixed with ``>``.
    """
    escaped = _escape(text)
    for char in _MARKDOWN_PUNCTUATION:
        escaped = escaped.replace(char, "\\" + char)
    return "\n".join(_escape_heading_lead(line) for line in escaped.split("\n"))


def _escape_inline(text: str) -> str:
    """Neutralize a single-block model string (a heading or prose run).

    On top of :func:`_escape_markdown`, collapse every embedded newline to a space so the string
    cannot break out of its one line/paragraph and open a forged heading, list, or section below
    it. Used where the rendered string must stay a single block: headings and prose lines.
    """
    return _escape_markdown(text).replace("\n", " ")


def _escape_heading_lead(line: str) -> str:
    # A leading ``#`` is only special at a line's start (after up to a little indentation), so a
    # message beginning with ``#`` would otherwise render as a heading inside the blockquote.
    stripped = line.lstrip(" ")
    indent = line[: len(line) - len(stripped)]
    if stripped.startswith("#"):
        return f"{indent}\\{stripped}"
    return line


def _prose_line(block: Prose) -> str:
    # The prose text is a single-block model string, so it is fully Markdown-neutralized and
    # newline-collapsed. The tags are controlled scale values (disposition / confidence), not free
    # text, so the plain HTML-escape suffices for them.
    parts = [_escape_inline(block.text), *(f"· {_escape(tag.value)}" for tag in block.tags)]
    if block.citation is not None:
        parts.append(_citation_text(block.citation))
    return " ".join(parts)


def _citation_text(citation: Citation) -> str:
    return "[" + "; ".join(_ref_text(ref) for ref in citation.refs) + "]"


def _ref_text(ref: dict[str, Any]) -> str:
    # ``session_ref``/``lines`` are resolver-produced tokens (e.g. ``S0001``, ``2-8``), so the plain
    # HTML-escape suffices. The scoped ``project_label`` is a model-derived string, so it is
    # Markdown-neutralized like any other display label.
    body = f"{_escape(_ref_str(ref, 'session_ref'))}:{_escape(_ref_str(ref, 'lines'))}"
    if ref.get("scoped"):
        return f"{_escape_inline(_ref_str(ref, 'project_label'))} · {body}"
    return body


def _ref_str(ref: dict[str, Any], key: str) -> str:
    value = ref.get(key, "")
    return value if isinstance(value, str) else ""


def _write_atomic(path: Path, text: str) -> Path:
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
