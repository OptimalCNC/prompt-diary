"""Evidence extraction phase runner placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskResult, TaskSpec


@dataclass(frozen=True)
class EvidenceExtractionRunner:
    """Run evidence extraction tasks."""

    agent_factory: AgentSessionFactory

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        """Run one evidence extraction task."""
        del workspace_path, task
        raise PromptDiaryError(_not_implemented_message())


def _not_implemented_message() -> str:
    return "evidence extraction phase runner is not implemented yet"
