"""Resolve daily-report citations to their indexed-turn line ranges.

A daily-report citation references one indexed turn within a project, as
``(project_key, session_ref, turn_ref)``. Session refs are assigned per project, so the project key
is part of the citation identity. Resolving a citation looks up that turn's 1-based inclusive line
range from the prepared workspace's session index. A turn that is not indexed does not resolve. How
a resolved citation renders — and how cross-project citations are qualified by project — is a
rendering concern, decided downstream rather than here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prompt_diary.generate.workspace import PreparedWorkspace


@dataclass(frozen=True)
class ResolvedCitation:
    """A citation resolved to its project-scoped session ref and line range."""

    project_key: str
    session_ref: str
    turn_ref: str
    lines: str

    def to_json(self) -> dict[str, str]:
        """Serialize to the stored daily-report citation shape."""
        return {
            "project_key": self.project_key,
            "session_ref": self.session_ref,
            "turn_ref": self.turn_ref,
            "lines": self.lines,
        }


@dataclass(frozen=True)
class CitationResolver:
    """Resolve ``(project_key, session_ref, turn_ref)`` to an indexed turn's line range."""

    spans: Mapping[tuple[str, str, str], str]

    @classmethod
    def from_workspace(cls, workspace: PreparedWorkspace) -> CitationResolver:
        """Build a resolver from a prepared workspace's session index."""
        spans: dict[tuple[str, str, str], str] = {}
        for project in workspace.projects:
            for session in project.sessions:
                for turn in session.turns:
                    key = (project.project_key, session.session_ref, turn.turn_ref)
                    spans[key] = f"{turn.span.start}-{turn.span.end}"
        return cls(spans=spans)

    def resolve(
        self, *, project_key: str, session_ref: str, turn_ref: str
    ) -> ResolvedCitation | None:
        """Resolve one turn reference, or return ``None`` if it is not an indexed turn."""
        lines = self.spans.get((project_key, session_ref, turn_ref))
        if lines is None:
            return None
        return ResolvedCitation(
            project_key=project_key, session_ref=session_ref, turn_ref=turn_ref, lines=lines
        )
