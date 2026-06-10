"""The real ``notion_client`` SDK adapter for the Notion publisher.

This is the only module that talks to the network, so it is deliberately thin: it implements
:class:`~prompt_diary.generate.rendering.notion_publish.NotionClientProtocol` by forwarding to
the ``notion_client`` SDK, with no logic of its own. Mapping, banner, and request shaping all live
in the publisher. Unit tests replace the SDK client with an in-process fake, so this adapter's
boundary is covered without live Notion credentials or network access.

The Notion API version is pinned to ``2022-06-28``: under that version a database retrieve returns a
flat ``properties`` map and a page is created with a ``{"database_id": ...}`` parent, which is the
shape the publisher's schema-driven mapping expects (newer versions split a database into data
sources, which the publisher does not model).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from notion_client import Client

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.rendering.notion_validate import (
    parse_database_info,
    parse_identity,
)

if TYPE_CHECKING:
    from prompt_diary.generate.rendering.notion_publish import NotionClientProtocol
    from prompt_diary.generate.rendering.notion_validate import (
        NotionDatabaseInfo,
        NotionIdentity,
    )

__all__ = [
    "NotionSDKClient",
    "NotionSDKValidator",
    "NotionValidator",
    "build_notion_client",
    "build_notion_validator",
]

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

    def create_page(
        self,
        *,
        parent: dict[str, Any],
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"parent": parent, "properties": properties}
        if children is not None:
            kwargs["children"] = children
        return cast(
            "dict[str, Any]",
            self._client.pages.create(**kwargs),
        )

    def append_children(
        self,
        *,
        block_id: str,
        children: list[dict[str, Any]],
        after: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"block_id": block_id, "children": children}
        if after is not None:
            kwargs["after"] = after
        return cast(
            "dict[str, Any]",
            self._client.blocks.children.append(**kwargs),
        )


def build_notion_client(*, token: str) -> NotionClientProtocol:
    """Build the real Notion client for the given integration token."""
    return NotionSDKClient(token=token)


class NotionValidator(Protocol):
    """Validate Notion credentials live so the config wizard can reject bad input before storing."""

    def verify_token(self) -> NotionIdentity:
        """Return the integration identity; raise :class:`PromptDiaryError` if not accepted."""
        ...

    def verify_database(self, database_id: str) -> NotionDatabaseInfo:
        """Return the reachable database; raise :class:`PromptDiaryError` if missing or unshared."""
        ...


class NotionSDKValidator:
    """Validate credentials by calling the SDK; failures become token-free domain errors."""

    def __init__(self, *, token: str) -> None:
        self._client = Client(auth=token, notion_version=_NOTION_VERSION)

    def verify_token(self) -> NotionIdentity:
        try:
            response = self._client.users.me()
        except Exception as exc:
            raise PromptDiaryError(_invalid_token_message()) from exc
        return parse_identity(response)

    def verify_database(self, database_id: str) -> NotionDatabaseInfo:
        try:
            response = self._client.databases.retrieve(database_id=database_id)
        except Exception as exc:
            raise PromptDiaryError(_invalid_database_message(database_id)) from exc
        return parse_database_info(response, database_id=database_id)


def build_notion_validator(*, token: str) -> NotionValidator:
    """Build the real Notion credential validator for the given integration token."""
    return NotionSDKValidator(token=token)


def _invalid_token_message() -> str:
    return "the Notion integration token was rejected; check the token and try again."


def _invalid_database_message(database_id: str) -> str:
    return (
        f"could not open Notion database {database_id}; check the id and that the database is "
        "shared with the integration."
    )
