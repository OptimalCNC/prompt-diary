"""Publish a rendered Notion page payload into a Notion database.

The deterministic renderer (:mod:`~prompt_diary.generate.rendering.render_notion`) produces
``report.notion.json`` — a title, a metadata-properties map, and a tree of Notion block children.
This module is the side-effecting half: it pushes that payload to Notion as a **new row (page) in a
target database**, never editing an existing row (re-publishing simply adds another dated row, which
the user prunes by hand).
The reader-facing layout contract lives in ``docs/src/generate/rendering.md#abstract-layout``;
publisher request shaping must preserve that rendered structure.

The Notion client is a narrow protocol (:class:`NotionClientProtocol`) so the publishing *logic* —
property mapping, the metadata banner, and the request-shaping that respects Notion's limits — is
unit-tested against a fake, while the real ``notion_client`` SDK lives behind a thin adapter.

Property mapping is schema-driven, so it works against any database without hard-coding column
names:

- the single ``title``-typed property (whatever it is named) gets the page title;
- every ``date``-typed property gets the report date;
- the configured reporter name is written into one named ``rich_text`` column (the 汇报人 column),
  when a reporter is set and that column exists and is a text property; a mismatch (the column
  exists but no name is configured, or a name is configured with no such text column) is reported
  as a ``warning`` rather than silently producing an empty column;
- other property types — including Notion-managed ``created_time`` / ``last_edited_time`` (the
  recommended type for a creation timestamp, which Notion auto-fills with time) — are left alone.

Report metadata that has no column — status, window, overall confidence — would otherwise be lost,
so it is rendered into a **status-colored banner callout prepended to the page body** (final green,
partial yellow), immediately followed by a **table of contents** for navigation. (The report date is
already in the title and a date column, so it is not repeated in the banner.)

Request shaping honors Notion's limits without the caller thinking about them: when the body fits
Notion's create-page limits, the page is created with its body in the same request. Otherwise, the
page is created first and its block tree is appended with ≤100 top-level blocks and ≤1000 block
elements per request. Leaf-only children are inlined into their parent append; deeper descendants
are appended recursively so returned ids are still available for blocks that need follow-up writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.rendering.render_notion import NotionPagePayload

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from prompt_diary.config import ReporterTarget

__all__ = [
    "NotionClientProtocol",
    "PublishResult",
    "publish_report",
    "publish_workspace_report",
]

_REPORT_NOTION_NAME = "report.notion.json"

# Notion appends accept at most 100 block children per request.
_MAX_CHILDREN_PER_REQUEST = 100

# Notion request payloads accept at most 1000 block elements overall. Inlining leaf children saves
# round trips, but batches still need to stay below the request-wide element cap.
_MAX_BLOCK_ELEMENTS_PER_REQUEST = 1000

# The metadata banner's icon — a clipboard, written as an escape so the source carries no literal
# emoji (U+1F4CB clipboard).
_BANNER_ICON = "\U0001f4cb"

# Banner background color by report status (Notion callout colors); the fallback is neutral gray.
_STATUS_COLORS = {"final": "green_background", "partial": "yellow_background"}


class NotionClientProtocol(Protocol):
    """The minimal Notion client surface the publisher needs (a seam over the SDK)."""

    def retrieve_database(self, *, database_id: str) -> dict[str, Any]:
        """Return the database object, including its ``properties`` schema."""
        ...

    def create_page(
        self,
        *,
        parent: dict[str, Any],
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a page under ``parent`` with ``properties``; return it with ``id``/``url``."""
        ...

    def append_children(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        """Append ``children`` under ``block_id``; return ``{"results": [...created blocks]}``."""
        ...


@dataclass(frozen=True)
class PublishResult:
    """The outcome of publishing one report: the created page's id and URL, and any warnings.

    ``warnings`` are non-fatal notes the caller should surface (e.g. the reporter column could not
    be filled); they never block a publish that otherwise succeeded.
    """

    page_id: str
    url: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AppendCandidate:
    request_block: dict[str, Any]
    pending_children: list[dict[str, Any]]
    request_block_count: int


def publish_workspace_report(
    *,
    workspace_path: Path,
    client: NotionClientProtocol,
    database_id: str,
    reporter: ReporterTarget | None = None,
) -> PublishResult:
    """Load a workspace's ``report.notion.json`` and publish it as a new row in ``database_id``."""
    path = workspace_path / _REPORT_NOTION_NAME
    if not path.exists():
        raise PromptDiaryError(_missing_artifact_message(path))
    payload = _payload_from_json(path)
    return publish_report(
        client=client, database_id=database_id, payload=payload, reporter=reporter
    )


def publish_report(
    *,
    client: NotionClientProtocol,
    database_id: str,
    payload: NotionPagePayload,
    reporter: ReporterTarget | None = None,
) -> PublishResult:
    """Create a new database row for ``payload`` and append its body block tree.

    ``reporter`` is the resolved reporter target (a column plus an optional name). When it cannot be
    written cleanly (the column is missing/mistyped, or no name is configured) the publish still
    succeeds and the reason is returned in :attr:`PublishResult.warnings`.
    """
    # Refuse to publish an undated row before any network call: report_date is the row's only
    # reliable sort/filter key (the banner omits it).
    report_date = _required_report_date(payload)
    # Translate SDK failures on the retrieve/create calls — a bad token (401), a wrong or unshared
    # database id (404), a rate limit, or a timeout — into a structured, actionable error instead of
    # an uncaught traceback. Our own structured errors (e.g. no title property) pass through as-is.
    try:
        schema = _database_properties(client, database_id)
        properties = _page_properties(schema, payload.title, report_date)
        reporter_warning = _apply_reporter(properties, schema, reporter)
        body: list[dict[str, Any]] = [
            _banner_block(payload.properties),
            _table_of_contents_block(),
            *payload.children,
        ]
        create_body = body if _can_create_with_body(body) else None
        page = client.create_page(
            parent={"database_id": database_id},
            properties=properties,
            children=create_body,
        )
    except PromptDiaryError:
        raise
    except Exception as exc:
        raise PromptDiaryError(_request_failed_message(database_id, exc)) from exc
    # A created page always carries an id; treat its absence as a contract violation rather than
    # appending the body under an empty id and returning a useless result.
    page_id = _str(page.get("id"))
    url = _str(page.get("url"))
    if not page_id:
        raise PromptDiaryError(_create_missing_id_message())
    if create_body is not None:
        warnings = (reporter_warning,) if reporter_warning else ()
        return PublishResult(page_id=page_id, url=url, warnings=warnings)
    # The row is created before its body is appended, so an append failure leaves a partial row.
    # Surface the created page's id/url in the error so it can be found and deleted (re-publishing
    # always makes a fresh row), instead of letting a raw SDK error hide which page was left behind.
    try:
        _append_tree(client, page_id, body)
    except Exception as exc:
        raise PromptDiaryError(_partial_page_message(page_id, url, exc)) from exc
    warnings = (reporter_warning,) if reporter_warning else ()
    return PublishResult(page_id=page_id, url=url, warnings=warnings)


def _database_properties(client: NotionClientProtocol, database_id: str) -> dict[str, Any]:
    database = client.retrieve_database(database_id=database_id)
    return _mapping(database.get("properties"))


def _required_report_date(payload: NotionPagePayload) -> str:
    report_date = payload.properties.get("report_date", "")
    if not report_date:
        raise PromptDiaryError(_missing_report_date_message())
    return report_date


def _page_properties(schema: dict[str, Any], title: str, report_date: str) -> dict[str, Any]:
    properties: dict[str, Any] = {_title_property_name(schema): {"title": [_text_run(title)]}}
    for name, spec in schema.items():
        if _mapping(spec).get("type") == "date":
            properties[name] = {"date": {"start": report_date}}
    return properties


def _apply_reporter(
    properties: dict[str, Any], schema: dict[str, Any], reporter: ReporterTarget | None
) -> str | None:
    # Write the reporter name into its column when it can be done cleanly; otherwise RETURN a
    # warning (never raise — a reporter mismatch must not fail an otherwise-good publish, but it
    # also must not silently produce an empty column). Column *existence* and *writability* are
    # distinct, so an existing-but-mistyped column is flagged even with no name configured:
    #   • name + text column        → write it, no warning;
    #   • text column, no name       → warn (left empty — the common "forgot to set my name" case);
    #   • column exists, not text    → warn (wrong type, can't hold the reporter — name or not);
    #   • no such column, name set   → warn (the name has nowhere to go);
    #   • no such column, no name    → silent (this database simply has no reporter column).
    if reporter is None:
        return None
    column = reporter.column
    column_exists = column in schema
    is_text_column = _mapping(schema.get(column)).get("type") == "rich_text"
    if is_text_column and reporter.name:
        properties[column] = {"rich_text": [_text_run(reporter.name)]}
        return None
    if is_text_column:
        return _reporter_unset_message(column)
    if column_exists:
        return _reporter_wrong_type_message(column)
    if reporter.name:
        return _reporter_uncolumned_message(column)
    return None


def _title_property_name(schema: dict[str, Any]) -> str:
    for name, spec in schema.items():
        if _mapping(spec).get("type") == "title":
            return name
    raise PromptDiaryError(_no_title_property_message())


def _banner_block(properties: dict[str, str]) -> dict[str, Any]:
    # Surface the metadata that has no database column (status / window / overall confidence) in a
    # banner at the top of the page body, so the report stays self-describing against any schema.
    # The callout is colored by status so an incomplete (partial) report stands out at a glance.
    text = (
        f"Status: {properties.get('status', '')} · "
        f"Window: {properties.get('window', '')} · "
        f"Overall confidence: {properties.get('overall_confidence', '')}"
    )
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [_text_run(text)],
            "icon": {"emoji": _BANNER_ICON},
            "color": _status_color(properties.get("status", "")),
        },
    }


def _status_color(status: str) -> str:
    # final → green, partial → yellow (caution), anything else → neutral gray.
    return _STATUS_COLORS.get(status, "gray_background")


def _table_of_contents_block() -> dict[str, Any]:
    # A native Notion ToC auto-links the report's headings for quick navigation at the top.
    return {
        "object": "block",
        "type": "table_of_contents",
        "table_of_contents": {"color": "default"},
    }


def _append_tree(
    client: NotionClientProtocol, parent_id: str, blocks: list[dict[str, Any]]
) -> None:
    # Append the block tree under ``parent_id`` with the deepest safe request shape Notion supports:
    # each request carries ≤100 top-level blocks and ≤1000 block elements overall. A block's
    # children are inlined only when every child is a leaf, because Notion returns ids only for the
    # first level appended; any child that still needs descendants appended later must be created as
    # a first-level result so we can capture its id.
    candidates = [_append_candidate(block) for block in blocks]
    for batch in _chunked_append_candidates(candidates):
        request_blocks = [candidate.request_block for candidate in batch]
        response = client.append_children(block_id=parent_id, children=request_blocks)
        # Notion returns one created block per appended block, in request order; the recursion
        # below relies on both facts to graft each block's stripped children under the right new
        # id. A short/long result is a contract violation, so fail loud rather than drop children.
        created = _as_list(response.get("results"))
        if len(created) != len(request_blocks):
            raise PromptDiaryError(_append_result_count_message(len(request_blocks), len(created)))
        for candidate, made in zip(batch, created, strict=True):
            if candidate.pending_children:
                child_id = _str(_mapping(made).get("id"))
                if not child_id:
                    raise PromptDiaryError(_append_missing_id_message())
                _append_tree(client, child_id, candidate.pending_children)


def _can_create_with_body(blocks: list[dict[str, Any]]) -> bool:
    return (
        len(blocks) <= _MAX_CHILDREN_PER_REQUEST
        and sum(_request_block_count(block) for block in blocks) <= _MAX_BLOCK_ELEMENTS_PER_REQUEST
        and all(len(_node_children(block)) <= _MAX_CHILDREN_PER_REQUEST for block in blocks)
        and all(not _node_children(child) for block in blocks for child in _node_children(block))
    )


def _append_candidate(block: dict[str, Any]) -> _AppendCandidate:
    children = _node_children(block)
    if _can_inline_leaf_children(children):
        return _AppendCandidate(
            request_block=block,
            pending_children=[],
            request_block_count=_request_block_count(block),
        )
    return _AppendCandidate(
        request_block=_without_children(block),
        pending_children=children,
        request_block_count=1,
    )


def _can_inline_leaf_children(children: list[dict[str, Any]]) -> bool:
    return (
        bool(children)
        and len(children) <= _MAX_CHILDREN_PER_REQUEST
        and all(not _node_children(child) for child in children)
    )


def _request_block_count(block: dict[str, Any]) -> int:
    return 1 + sum(_request_block_count(child) for child in _node_children(block))


def _without_children(block: dict[str, Any]) -> dict[str, Any]:
    body = _mapping(block.get(_str(block.get("type"))))
    if "children" not in body:
        return block
    stripped_body = {key: value for key, value in body.items() if key != "children"}
    return {**block, _str(block.get("type")): stripped_body}


def _node_children(block: dict[str, Any]) -> list[dict[str, Any]]:
    return _as_list(_mapping(block.get(_str(block.get("type")))).get("children"))


def _chunked_append_candidates(
    candidates: list[_AppendCandidate],
) -> Iterator[list[_AppendCandidate]]:
    batch: list[_AppendCandidate] = []
    block_count = 0
    for candidate in candidates:
        if batch and (
            len(batch) >= _MAX_CHILDREN_PER_REQUEST
            or block_count + candidate.request_block_count > _MAX_BLOCK_ELEMENTS_PER_REQUEST
        ):
            yield batch
            batch = []
            block_count = 0
        batch.append(candidate)
        block_count += candidate.request_block_count
    if batch:
        yield batch


def _payload_from_json(path: Path) -> NotionPagePayload:
    raw = _mapping(json.loads(path.read_text(encoding="utf-8")))
    return NotionPagePayload(
        title=_str(raw.get("title")),
        properties={
            key: value
            for key, value in _mapping(raw.get("properties")).items()
            if isinstance(value, str)
        },
        children=_as_list(raw.get("children")),
    )


def _text_run(content: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": content}}


def _mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _missing_artifact_message(path: Path) -> str:
    return f"no Notion report payload to publish at {path}; render the report first"


def _no_title_property_message() -> str:
    return "target Notion database has no title property; cannot create a report row"


def _reporter_unset_message(column: str) -> str:
    return (
        f"no reporter name is configured, so the '{column}' column was left empty; "
        "set one with `prompt-diary config init`."
    )


def _reporter_wrong_type_message(column: str) -> str:
    return (
        f"the '{column}' column is not a text property, so the reporter could not be written; "
        "make it a text column to record the reporter."
    )


def _reporter_uncolumned_message(column: str) -> str:
    return (
        f"a reporter name is configured but the target database has no '{column}' column, "
        "so it was not written."
    )


def _missing_report_date_message() -> str:
    return "Notion report payload has no report_date; refusing to publish an undated row"


def _request_failed_message(database_id: str, cause: object) -> str:
    return (
        f"Notion request failed for database {database_id}; check the integration token and that "
        f"the integration can access this database: {cause}"
    )


def _create_missing_id_message() -> str:
    return "Notion create-page response carried no page id; cannot append the report body"


def _append_result_count_message(sent: int, received: int) -> str:
    return (
        f"Notion append returned {received} result(s) for {sent} block(s); "
        "cannot reliably graft nested children, so refusing to continue"
    )


def _append_missing_id_message() -> str:
    return "Notion append result carried no block id; cannot graft this block's nested children"


def _partial_page_message(page_id: str, url: str, cause: object) -> str:
    location = url or page_id
    return (
        f"failed to append the report body to Notion page {location}; a partial row may exist and "
        f"can be deleted (re-publishing creates a fresh row): {cause}"
    )
