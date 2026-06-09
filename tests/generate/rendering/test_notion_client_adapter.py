"""Tests for the Notion SDK adapter using an in-process fake SDK client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import pytest

import prompt_diary.generate.rendering.notion_client_adapter as adapter
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.rendering.notion_validate import NotionDatabaseInfo, NotionIdentity

if TYPE_CHECKING:
    from collections.abc import Callable

_FAKE_TOKEN = "not-a-secret"
_REJECTED_TOKEN = "secret-" + "123"


class _FakeSDKClient:
    """A tiny stand-in for ``notion_client.Client`` that records endpoint calls."""

    last: ClassVar[_FakeSDKClient | None] = None
    users_response: ClassVar[object] = {}
    database_response: ClassVar[object] = {"properties": {}}
    page_response: ClassVar[object] = {"id": "page-1", "url": "https://notion.so/page-1"}
    append_response: ClassVar[object] = {"results": []}
    users_error: ClassVar[Exception | None] = None
    database_error: ClassVar[Exception | None] = None

    def __init__(self, *, auth: str, notion_version: str) -> None:
        self.auth = auth
        self.notion_version = notion_version
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.users = _FakeUsersEndpoint(self)
        self.databases = _FakeDatabasesEndpoint(self)
        self.pages = _FakePagesEndpoint(self)
        self.blocks = _FakeBlocksEndpoint(self)
        type(self).last = self

    @classmethod
    def reset(cls) -> None:
        cls.last = None
        cls.users_response = {}
        cls.database_response = {"properties": {}}
        cls.page_response = {"id": "page-1", "url": "https://notion.so/page-1"}
        cls.append_response = {"results": []}
        cls.users_error = None
        cls.database_error = None


class _FakeUsersEndpoint:
    def __init__(self, client: _FakeSDKClient) -> None:
        self._client = client

    def me(self) -> object:
        self._client.calls.append(("users.me", {}))
        if _FakeSDKClient.users_error is not None:
            raise _FakeSDKClient.users_error
        return _FakeSDKClient.users_response


class _FakeDatabasesEndpoint:
    def __init__(self, client: _FakeSDKClient) -> None:
        self._client = client

    def retrieve(self, *, database_id: str) -> object:
        self._client.calls.append(("databases.retrieve", {"database_id": database_id}))
        if _FakeSDKClient.database_error is not None:
            raise _FakeSDKClient.database_error
        return _FakeSDKClient.database_response


class _FakePagesEndpoint:
    def __init__(self, client: _FakeSDKClient) -> None:
        self._client = client

    def create(self, **kwargs: object) -> object:
        self._client.calls.append(("pages.create", dict(kwargs)))
        return _FakeSDKClient.page_response


class _FakeBlocksEndpoint:
    def __init__(self, client: _FakeSDKClient) -> None:
        self.children = _FakeChildrenEndpoint(client)


class _FakeChildrenEndpoint:
    def __init__(self, client: _FakeSDKClient) -> None:
        self._client = client

    def append(self, *, block_id: str, children: list[dict[str, Any]]) -> object:
        self._client.calls.append(
            ("blocks.children.append", {"block_id": block_id, "children": children})
        )
        return _FakeSDKClient.append_response


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> Callable[[], _FakeSDKClient]:
    _FakeSDKClient.reset()
    monkeypatch.setattr(adapter, "Client", _FakeSDKClient)

    def current() -> _FakeSDKClient:
        client = _FakeSDKClient.last
        assert client is not None
        return client

    return current


def test_build_notion_client_forwards_publisher_calls_without_network(
    fake_client: Callable[[], _FakeSDKClient],
) -> None:
    _FakeSDKClient.database_response = {"properties": {"Name": {"type": "title"}}}
    _FakeSDKClient.page_response = {"id": "page-2", "url": "https://notion.so/page-2"}
    _FakeSDKClient.append_response = {"results": [{"id": "block-1"}]}

    client = adapter.build_notion_client(token=_FAKE_TOKEN)

    assert client.retrieve_database(database_id="db-1") == {
        "properties": {"Name": {"type": "title"}}
    }
    assert client.create_page(parent={"database_id": "db-1"}, properties={"Name": {}}) == {
        "id": "page-2",
        "url": "https://notion.so/page-2",
    }
    children: list[dict[str, Any]] = [{"object": "block", "type": "paragraph", "paragraph": {}}]
    assert client.append_children(block_id="page-2", children=children) == {
        "results": [{"id": "block-1"}]
    }

    sdk = fake_client()
    assert sdk.auth == _FAKE_TOKEN
    assert sdk.notion_version == "2022-06-28"
    assert sdk.calls == [
        ("databases.retrieve", {"database_id": "db-1"}),
        (
            "pages.create",
            {"parent": {"database_id": "db-1"}, "properties": {"Name": {}}},
        ),
        ("blocks.children.append", {"block_id": "page-2", "children": children}),
    ]


def test_notion_client_includes_children_only_when_create_page_receives_them(
    fake_client: Callable[[], _FakeSDKClient],
) -> None:
    client = adapter.NotionSDKClient(token=_FAKE_TOKEN)
    children: list[dict[str, Any]] = [{"object": "block", "type": "paragraph", "paragraph": {}}]

    client.create_page(parent={"database_id": "db-1"}, properties={}, children=children)

    assert fake_client().calls == [
        (
            "pages.create",
            {"parent": {"database_id": "db-1"}, "properties": {}, "children": children},
        )
    ]


def test_build_notion_validator_parses_successful_responses(
    fake_client: Callable[[], _FakeSDKClient],
) -> None:
    _FakeSDKClient.users_response = {
        "name": "Prompt Diary Bot",
        "bot": {"workspace_name": "Acme", "owner": {"type": "workspace"}},
    }
    _FakeSDKClient.database_response = {"title": [{"plain_text": "Daily Reports"}]}

    validator = adapter.build_notion_validator(token=_FAKE_TOKEN)

    assert validator.verify_token() == NotionIdentity(
        integration_name="Prompt Diary Bot",
        workspace_name="Acme",
        owner_type="workspace",
    )
    assert validator.verify_database("db-1") == NotionDatabaseInfo(
        database_id="db-1", title="Daily Reports"
    )
    sdk = fake_client()
    assert sdk.auth == _FAKE_TOKEN
    assert sdk.notion_version == "2022-06-28"
    assert sdk.calls == [
        ("users.me", {}),
        ("databases.retrieve", {"database_id": "db-1"}),
    ]


def test_notion_validator_wraps_rejected_token_without_exposing_it(
    fake_client: Callable[[], _FakeSDKClient],
) -> None:
    del fake_client
    _FakeSDKClient.users_error = RuntimeError(f"HTTP 401 rejected {_REJECTED_TOKEN}")

    validator = adapter.NotionSDKValidator(token=_REJECTED_TOKEN)

    with pytest.raises(PromptDiaryError, match="integration token was rejected") as exc_info:
        validator.verify_token()
    assert _REJECTED_TOKEN not in str(exc_info.value)


def test_notion_validator_wraps_unshared_database_without_exposing_token(
    fake_client: Callable[[], _FakeSDKClient],
) -> None:
    del fake_client
    _FakeSDKClient.database_error = RuntimeError(f"HTTP 404 rejected {_REJECTED_TOKEN}")

    validator = adapter.NotionSDKValidator(token=_REJECTED_TOKEN)

    with pytest.raises(PromptDiaryError, match="could not open Notion database db-1") as exc_info:
        validator.verify_database("db-1")
    error = str(exc_info.value)
    assert "db-1" in error
    assert _REJECTED_TOKEN not in error
