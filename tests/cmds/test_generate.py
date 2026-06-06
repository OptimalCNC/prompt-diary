from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import pytest

from prompt_diary.cmds.generate import (
    build_generation_workflow,
    resolve_notion_publish,
)
from prompt_diary.errors import PromptDiaryError
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig
from prompt_diary.secret import Secret

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
    assert set(runners) == {
        "evidence_extraction",
        "project_synthesis",
        "daily_synthesis",
        "rendering",
    }
    # The three agent phases share the one Codex-backed factory; rendering is deterministic and
    # holds no agent factory.
    for kind in ("evidence_extraction", "project_synthesis", "daily_synthesis"):
        assert cast("_HasAgentFactory", runners[kind]).agent_factory is factory
    assert not hasattr(runners["rendering"], "agent_factory")

    overrides = _backend_config(factory).mcp_config_overrides
    assert 'mcp_servers.prompt_diary.command="report"' in overrides


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
    # unset + configured -> publish (the token is wrapped in a redacting Secret)
    assert resolve_notion_publish(notion=None) == (Secret("tok"), "db")


def test_resolve_notion_publish_explicit_notion_requires_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTION_API_KEY", "tok")
    monkeypatch.setenv("NOTION_PAGE_ID", "db")
    assert resolve_notion_publish(notion=True) == (Secret("tok"), "db")  # configured -> publish
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_PAGE_ID", raising=False)
    with pytest.raises(PromptDiaryError, match="config init"):
        resolve_notion_publish(notion=True)  # --notion + unconfigured -> fail fast
