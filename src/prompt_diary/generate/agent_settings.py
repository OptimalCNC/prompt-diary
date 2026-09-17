"""Package-owned model and reasoning settings for generation agents."""

from __future__ import annotations

from importlib.resources import files
from typing import Annotated, Literal

import msgspec


class AgentSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Explicit model and reasoning selection for one generation assignment."""

    model: Annotated[str, msgspec.Meta(min_length=1)]
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"]


class DailySynthesisAgentSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Independent settings for each daily synthesis pass."""

    project_summary: AgentSettings
    report_title: AgentSettings
    engagement: AgentSettings
    team_learning: AgentSettings


class GenerationAgentSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Complete internal generation settings, parsed before starting agents."""

    evidence_extraction: AgentSettings
    project_synthesis: AgentSettings
    daily_synthesis: DailySynthesisAgentSettings


def load_agent_settings() -> GenerationAgentSettings:
    """Read the bundled configuration; no user config, path, or environment overrides."""
    resource = files("prompt_diary.generate").joinpath("agent-settings.json")
    return msgspec.json.decode(resource.read_bytes(), type=GenerationAgentSettings)
