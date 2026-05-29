"""Project synthesis phase runner placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult, TaskSpec


@dataclass(frozen=True)
class ProjectSynthesisRunner:
    """Run project synthesis tasks."""

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        """Run one project synthesis task."""
        return await run_project_synthesis(workspace_path=workspace_path, task=task)


async def run_project_synthesis(*, workspace_path: Path, task: TaskSpec) -> TaskResult:
    """Run one project synthesis task."""
    del workspace_path, task
    raise PromptDiaryError(_not_implemented_message())


def _not_implemented_message() -> str:
    return "project synthesis phase runner is not implemented yet"
