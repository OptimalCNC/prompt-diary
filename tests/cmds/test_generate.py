from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import pytest

import prompt_diary.cmds.generate as generate_cmd
from prompt_diary.cmds.generate import (
    build_generation_workflow,
    publish_report_to_notion,
    resolve_notion_publish,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.notion_publish import PublishResult
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory


class _HasAgentFactory(Protocol):
    agent_factory: AgentSessionFactory


def _backend_config(factory: CodexAgentSessionFactory) -> CodexBackendConfig:
    config = vars(factory)["_backend_config"]
    assert isinstance(config, CodexBackendConfig)
    return config


def test_build_generation_workflow_builds_workspace_aware_codex_runners(tmp_path: Path) -> None:
    workflow = build_generation_workflow()
    factory = workflow.build_agent_factory(tmp_path)
    runners = workflow.build_phase_runners(factory)

    assert isinstance(factory, CodexAgentSessionFactory)
    assert set(runners) == {"evidence_extraction", "project_synthesis", "daily_synthesis"}
    for runner in runners.values():
        assert cast("_HasAgentFactory", runner).agent_factory is factory

    overrides = _backend_config(factory).mcp_config_overrides
    assert 'mcp_servers.prompt_diary.command="report"' in overrides


class _StubNotionClient:
    """A do-nothing client that structurally satisfies ``NotionClientProtocol`` for wiring tests."""

    def retrieve_database(self, *, database_id: str) -> dict[str, object]:
        del database_id
        return {}

    def create_page(
        self, *, parent: dict[str, object], properties: dict[str, object]
    ) -> dict[str, object]:
        del parent, properties
        return {}

    def append_children(
        self, *, block_id: str, children: list[dict[str, object]]
    ) -> dict[str, object]:
        del block_id, children
        return {}


def _stub_factory(*, token: str) -> _StubNotionClient:
    del token
    return _StubNotionClient()


def test_publish_report_to_notion_uses_frozen_credentials_and_returns_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_publish(*, workspace_path: Path, client: object, database_id: str) -> PublishResult:
        seen["workspace_path"] = workspace_path
        seen["client"] = client
        seen["database_id"] = database_id
        return PublishResult(page_id="page-1", url="https://notion.so/page-x")

    monkeypatch.setattr(generate_cmd, "publish_workspace_report", fake_publish)
    tokens: list[str] = []

    def fake_factory(*, token: str) -> _StubNotionClient:
        tokens.append(token)
        return _StubNotionClient()

    # The token and database id are the frozen pair resolved before the pipeline, not re-read here.
    messages = publish_report_to_notion(
        tmp_path, credentials=("tok-123", "db-456"), client_factory=fake_factory
    )

    assert tokens == ["tok-123"]
    assert seen["database_id"] == "db-456"
    assert seen["workspace_path"] == tmp_path
    assert messages == ("Published report to Notion: https://notion.so/page-x",)


def test_publish_report_to_notion_passes_through_structured_publish_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    structured = "a partial row may exist"

    def raises_structured(
        *, workspace_path: Path, client: object, database_id: str
    ) -> PublishResult:
        del workspace_path, client, database_id
        raise PromptDiaryError(structured)

    monkeypatch.setattr(generate_cmd, "publish_workspace_report", raises_structured)

    # A structured publisher error (e.g. partial-page) passes through with its actionable message.
    with pytest.raises(PromptDiaryError, match=structured):
        publish_report_to_notion(tmp_path, credentials=("tok", "db"), client_factory=_stub_factory)


def test_publish_report_to_notion_wraps_unexpected_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raises_value_error(
        *, workspace_path: Path, client: object, database_id: str
    ) -> PublishResult:
        del workspace_path, client, database_id
        raise ValueError(_MALFORMED)

    monkeypatch.setattr(generate_cmd, "publish_workspace_report", raises_value_error)

    # An unexpected (non-structured) failure becomes a clean, token-free PromptDiaryError.
    with pytest.raises(PromptDiaryError, match="failed to publish the report to Notion"):
        publish_report_to_notion(tmp_path, credentials=("tok", "db"), client_factory=_stub_factory)


_MALFORMED = "malformed artifact"


def test_resolve_notion_publish_no_notion_never_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")
    assert resolve_notion_publish(notion=False) is None  # --no-notion skips even when configured


def test_resolve_notion_publish_default_follows_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_PAGE_ID", raising=False)
    assert resolve_notion_publish(notion=None) is None  # unset + unconfigured -> skip
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")
    assert resolve_notion_publish(notion=None) == ("tok", "db")  # unset + configured -> publish


def test_resolve_notion_publish_explicit_notion_requires_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")
    assert resolve_notion_publish(notion=True) == ("tok", "db")  # configured -> publish
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_PAGE_ID", raising=False)
    with pytest.raises(PromptDiaryError, match="config init"):
        resolve_notion_publish(notion=True)  # --notion + unconfigured -> fail fast
