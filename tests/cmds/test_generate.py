from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from prompt_diary.cmds.generate import build_generation_workflow
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
