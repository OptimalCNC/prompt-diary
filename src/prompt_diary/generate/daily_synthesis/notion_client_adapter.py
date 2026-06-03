"""The real ``notion_client`` SDK adapter for the Notion publisher.

This is the only module that talks to the network, so it is deliberately thin: it implements
:class:`~prompt_diary.generate.daily_synthesis.notion_publish.NotionClientProtocol` by forwarding to
the ``notion_client`` SDK, with no logic of its own (mapping, banner, and request shaping all live
in the unit-tested publisher). It is excluded from coverage for the same reason
``integrations/codex_runner.py`` is — its behaviour is the SDK's, exercised by the live publish path
rather than the unit suite.

The Notion API version is pinned to ``2022-06-28``: under that version a database retrieve returns a
flat ``properties`` map and a page is created with a ``{"database_id": ...}`` parent, which is the
shape the publisher's schema-driven mapping expects (newer versions split a database into data
sources, which the publisher does not model).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from notion_client import Client

if TYPE_CHECKING:
    from prompt_diary.generate.daily_synthesis.notion_publish import NotionClientProtocol

__all__ = ["NotionSDKClient", "build_notion_client"]

_NOTION_VERSION = "2022-06-28"


class NotionSDKClient:
    """Adapt the ``notion_client`` SDK to :class:`NotionClientProtocol`.

    The SDK types every endpoint as ``SyncAsync[Any]`` (one signature for both the sync and async
    clients); on the sync client used here each call returns the parsed JSON object, so the results
    are cast to the concrete dict shapes the protocol declares.
    """

    def __init__(self, *, token: str) -> None:
        self._client = Client(auth=token, notion_version=_NOTION_VERSION)

    def retrieve_database(self, *, database_id: str) -> dict[str, Any]:
        return cast("dict[str, Any]", self._client.databases.retrieve(database_id=database_id))

    def create_page(self, *, parent: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            self._client.pages.create(parent=parent, properties=properties),
        )

    def append_children(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            self._client.blocks.children.append(block_id=block_id, children=children),
        )


def build_notion_client(*, token: str) -> NotionClientProtocol:
    """Build the real Notion client for the given integration token."""
    return NotionSDKClient(token=token)
