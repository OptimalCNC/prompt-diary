"""Project synthesis phase runner placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskResult, TaskSpec
    from prompt_diary.progress.reporter import ProgressReporter


@dataclass(frozen=True)
class ProjectSynthesisRunner:
    """Run project synthesis tasks."""

    agent_factory: AgentSessionFactory

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter
    ) -> TaskResult:
        """Run one project synthesis task."""
        del workspace_path, task, reporter
        raise PromptDiaryError(_not_implemented_message())


def _not_implemented_message() -> str:
    return "project synthesis phase runner is not implemented yet"
