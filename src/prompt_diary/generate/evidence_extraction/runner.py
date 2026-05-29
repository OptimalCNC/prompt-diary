"""Evidence extraction phase runner placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.pipeline import TaskResult, TaskSpec


@dataclass(frozen=True)
class EvidenceExtractionRunner:
    """Run evidence extraction tasks."""

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        """Run one evidence extraction task."""
        return await run_evidence_extraction(workspace_path=workspace_path, task=task)


async def run_evidence_extraction(*, workspace_path: Path, task: TaskSpec) -> TaskResult:
    """Run one evidence extraction task.

    The model-backed implementation will be added later. This placeholder keeps the standalone
    phase API explicit instead of silently falling back to a legacy report writer.
    """
    del workspace_path, task
    raise PromptDiaryError(_not_implemented_message())


def _not_implemented_message() -> str:
    return "evidence extraction phase runner is not implemented yet"
