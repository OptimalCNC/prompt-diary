"""Tests for the pure Notion credential-validation result parsers."""

from __future__ import annotations

from prompt_diary.generate.daily_synthesis.notion_validate import (
    NotionDatabaseInfo,
    NotionIdentity,
    parse_database_info,
    parse_identity,
)


def test_parse_identity_extracts_name_workspace_and_owner_type() -> None:
    raw = {
        "object": "user",
        "name": "Prompt Diary Bot",
        "type": "bot",
        "bot": {"workspace_name": "Acme HQ", "owner": {"type": "workspace", "workspace": True}},
    }

    assert parse_identity(raw) == NotionIdentity(
        integration_name="Prompt Diary Bot",
        workspace_name="Acme HQ",
        owner_type="workspace",
    )


def test_parse_identity_tolerates_missing_bot_block() -> None:
    assert parse_identity({"name": "Solo"}) == NotionIdentity(integration_name="Solo")


def test_parse_identity_ignores_non_string_and_missing_fields() -> None:
    # A numeric name and an owner block without a type must collapse to None, not crash.
    assert parse_identity({"name": 123, "bot": {"owner": {}}}) == NotionIdentity()


def test_parse_identity_tolerates_non_mapping_response() -> None:
    assert parse_identity("not a mapping") == NotionIdentity()


def test_parse_database_info_joins_title_segments() -> None:
    raw = {"object": "database", "title": [{"plain_text": "Daily "}, {"plain_text": "Report"}]}

    assert parse_database_info(raw, database_id="db-1") == NotionDatabaseInfo(
        database_id="db-1", title="Daily Report"
    )


def test_parse_database_info_skips_non_mapping_title_segments() -> None:
    raw = {"title": [{"plain_text": "A"}, "junk", {"plain_text": "B"}]}

    assert parse_database_info(raw, database_id="db-1").title == "AB"


def test_parse_database_info_returns_none_title_when_absent_blank_or_wrong_type() -> None:
    assert parse_database_info({}, database_id="db-1") == NotionDatabaseInfo(database_id="db-1")
    assert parse_database_info({"title": "nope"}, database_id="db-2").title is None
    assert parse_database_info({"title": [{"plain_text": "   "}]}, database_id="db-3").title is None
