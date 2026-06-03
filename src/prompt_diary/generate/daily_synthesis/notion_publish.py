"""Publish a rendered Notion page payload into a Notion database.

The deterministic renderer (:mod:`~prompt_diary.generate.daily_synthesis.render_notion`) produces
``report.notion.json`` — a title, a metadata-properties map, and a tree of Notion block children.
This module is the side-effecting half: it pushes that payload to Notion as a **new row (page) in a
target database**, never editing an existing row (re-publishing simply adds another dated row, which
the user prunes by hand).

The Notion client is a narrow protocol (:class:`NotionClientProtocol`) so the publishing *logic* —
property mapping, the metadata banner, and the request-shaping that respects Notion's limits — is
unit-tested against a fake, while the real ``notion_client`` SDK lives behind a thin adapter.

Property mapping is schema-driven, so it works against any database without hard-coding column
names:

- the single ``title``-typed property (whatever it is named) gets the page title;
- every ``date``-typed property gets the report date;
- other property types (people, etc.) are left for the user to fill.

Report metadata that has no column — status, window, overall confidence — would otherwise be lost,
so it is rendered into a **banner callout prepended to the page body**. (The report date is already
in the title and a date column, so it is not repeated in the banner.)

Request shaping honors Notion's limits without the caller thinking about them: the page is created
empty (properties only), then its block tree is appended level by level — each request carries ≤100
blocks stripped to a single nesting level, and the ids returned for blocks that have children drive
a recursive append of those children. This keeps every request well within Notion's ≤100-children
and ~2-level create-nesting limits, for an arbitrarily deep and wide report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.render_notion import NotionPagePayload

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = [
    "NotionClientProtocol",
    "PublishResult",
    "publish_report",
    "publish_workspace_report",
]

_REPORT_NOTION_NAME = "report.notion.json"

# Notion appends accept at most 100 block children per request.
_MAX_CHILDREN_PER_REQUEST = 100

# The metadata banner's icon — a clipboard, written as an escape so the source carries no literal
# emoji (U+1F4CB clipboard).
_BANNER_ICON = "\U0001f4cb"


class NotionClientProtocol(Protocol):
    """The minimal Notion client surface the publisher needs (a seam over the SDK)."""

    def retrieve_database(self, *, database_id: str) -> dict[str, Any]:
        """Return the database object, including its ``properties`` schema."""
        ...

    def create_page(self, *, parent: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
        """Create a page under ``parent`` with ``properties``; return it with ``id``/``url``."""
        ...

    def append_children(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        """Append ``children`` under ``block_id``; return ``{"results": [...created blocks]}``."""
        ...


@dataclass(frozen=True)
class PublishResult:
    """The outcome of publishing one report: the created page's id and URL."""

    page_id: str
    url: str


def publish_workspace_report(
    *, workspace_path: Path, client: NotionClientProtocol, database_id: str
) -> PublishResult:
    """Load a workspace's ``report.notion.json`` and publish it as a new row in ``database_id``."""
    path = workspace_path / _REPORT_NOTION_NAME
    if not path.exists():
        raise PromptDiaryError(_missing_artifact_message(path))
    payload = _payload_from_json(path)
    return publish_report(client=client, database_id=database_id, payload=payload)


def publish_report(
    *, client: NotionClientProtocol, database_id: str, payload: NotionPagePayload
) -> PublishResult:
    """Create a new database row for ``payload`` and append its body block tree."""
    # Refuse to publish an undated row before any network call: report_date is the row's only
    # reliable sort/filter key (the banner omits it).
    report_date = _required_report_date(payload)
    # Translate SDK failures on the retrieve/create calls — a bad token (401), a wrong or unshared
    # database id (404), a rate limit, or a timeout — into a structured, actionable error instead of
    # an uncaught traceback. Our own structured errors (e.g. no title property) pass through as-is.
    try:
        schema = _database_properties(client, database_id)
        properties = _page_properties(schema, payload.title, report_date)
        page = client.create_page(parent={"database_id": database_id}, properties=properties)
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
    body: list[dict[str, Any]] = [_banner_block(payload.properties), *payload.children]
    # The row is created before its body is appended, so an append failure leaves a partial row.
    # Surface the created page's id/url in the error so it can be found and deleted (re-publishing
    # always makes a fresh row), instead of letting a raw SDK error hide which page was left behind.
    try:
        _append_tree(client, page_id, body)
    except Exception as exc:
        raise PromptDiaryError(_partial_page_message(page_id, url, exc)) from exc
    return PublishResult(page_id=page_id, url=url)


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


def _title_property_name(schema: dict[str, Any]) -> str:
    for name, spec in schema.items():
        if _mapping(spec).get("type") == "title":
            return name
    raise PromptDiaryError(_no_title_property_message())


def _banner_block(properties: dict[str, str]) -> dict[str, Any]:
    # Surface the metadata that has no database column (status / window / overall confidence) in a
    # banner at the top of the page body, so the report stays self-describing against any schema.
    text = (
        f"Status: {properties.get('status', '')} · "
        f"Window: {properties.get('window', '')} · "
        f"Overall confidence: {properties.get('overall_confidence', '')}"
    )
    return {
        "object": "block",
        "type": "callout",
        "callout": {"rich_text": [_text_run(text)], "icon": {"emoji": _BANNER_ICON}},
    }


def _append_tree(
    client: NotionClientProtocol, parent_id: str, blocks: list[dict[str, Any]]
) -> None:
    # Append the block tree under ``parent_id`` one nesting level at a time: each request carries a
    # batch of ≤100 blocks with their nested ``children`` stripped (so the request is one level
    # deep), and the ids Notion returns for blocks that had children drive a recursive append of
    # those children. This honors the per-request count and create-nesting-depth limits uniformly.
    for batch in _chunked(blocks, _MAX_CHILDREN_PER_REQUEST):
        shallow = [_without_children(block) for block in batch]
        response = client.append_children(block_id=parent_id, children=shallow)
        # Notion returns one created block per appended block, in request order; the recursion
        # below relies on both facts to graft each block's stripped children under the right new
        # id. A short/long result is a contract violation, so fail loud rather than drop children.
        created = _as_list(response.get("results"))
        if len(created) != len(shallow):
            raise PromptDiaryError(_append_result_count_message(len(shallow), len(created)))
        for original, made in zip(batch, created, strict=True):
            grandchildren = _node_children(original)
            if grandchildren:
                child_id = _str(_mapping(made).get("id"))
                if not child_id:
                    raise PromptDiaryError(_append_missing_id_message())
                _append_tree(client, child_id, grandchildren)


def _without_children(block: dict[str, Any]) -> dict[str, Any]:
    body = _mapping(block.get(_str(block.get("type"))))
    if "children" not in body:
        return block
    stripped_body = {key: value for key, value in body.items() if key != "children"}
    return {**block, _str(block.get("type")): stripped_body}


def _node_children(block: dict[str, Any]) -> list[dict[str, Any]]:
    return _as_list(_mapping(block.get(_str(block.get("type")))).get("children"))


def _chunked(blocks: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(blocks), size):
        yield blocks[start : start + size]


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
