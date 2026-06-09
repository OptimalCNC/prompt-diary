"""Typed results and pure parsers for live Notion credential validation.

The config wizard validates the Notion integration token and target database live before storing
them. The networked SDK calls live in ``notion_client_adapter``; this module holds the *pure* half —
the small result types the wizard reports back to the user and the parsers that turn a raw Notion
``users.me`` / ``databases.retrieve`` response into them. Keeping the parsing here keeps it
unit-tested and free of any network dependency.

The parsers are deliberately defensive: a successful response that omits an optional field, or
carries an unexpected shape, yields ``None`` for that field rather than raising. A rejected
credential is signalled by the adapter raising on the request itself; a request that *succeeds* but
simply lacks an optional field (a nameless integration, a workspace-less owner, an untitled
database) is normal and must not crash the wizard.
"""

from __future__ import annotations

from typing import Any, cast

import msgspec


class NotionIdentity(msgspec.Struct, frozen=True):
    """Who an accepted Notion integration token authenticates as, for display in the wizard.

    Notion's ``users.me`` returns the bot identity but not the integration's capability scopes, so
    ``owner_type`` (``workspace`` or ``user``) is the only available indication of its reach.
    """

    integration_name: str | None = None
    workspace_name: str | None = None
    owner_type: str | None = None


class NotionDatabaseInfo(msgspec.Struct, frozen=True):
    """The reachable target database behind an accepted page id, for display in the wizard."""

    database_id: str
    title: str | None = None


def parse_identity(raw: object) -> NotionIdentity:
    """Parse a Notion ``users.me`` response into a :class:`NotionIdentity`.

    For an integration token the response is the bot user: its ``name`` is the integration name and
    its ``bot`` block carries the ``workspace_name`` and the owner ``type``.
    """
    data = _mapping(raw)
    bot = _mapping(data.get("bot"))
    owner = _mapping(bot.get("owner"))
    return NotionIdentity(
        integration_name=_text(data.get("name")) or None,
        workspace_name=_text(bot.get("workspace_name")) or None,
        owner_type=_text(owner.get("type")) or None,
    )


def parse_database_info(raw: object, *, database_id: str) -> NotionDatabaseInfo:
    """Parse a Notion ``databases.retrieve`` response into a :class:`NotionDatabaseInfo`."""
    title = _title_text(_mapping(raw).get("title")) or None
    return NotionDatabaseInfo(database_id=database_id, title=title)


def _title_text(value: object) -> str:
    # A Notion title is rich-text segments; the database name is their joined plain_text.
    parts = [_text(_mapping(segment).get("plain_text")) for segment in _sequence(value)]
    return "".join(parts).strip()


def _mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _sequence(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
