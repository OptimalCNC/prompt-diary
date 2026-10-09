"""Shared model assignment for basic live SDK and MCP connectivity probes."""

from prompt_diary.generate.agent_settings import AgentSettings

SDK_PROBE_SETTINGS = AgentSettings(model="gpt-6-luna", reasoning_effort="low")
