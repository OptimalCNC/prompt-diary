"""Build the project synthesizer prompt inputs for one project.

The synthesizer agent has no file access: it works only from the chains pasted into its prompt. This
module reads the project's committed evidence chains and renders them, trimmed to summaries, grouped
by session under a ``#### Session <session_ref>`` heading with each chain labelled
``<session_ref>/<turn_ref>``. Citations and quoted message text are dropped — the agent references
turns and the summaries are sufficient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.project_synthesis.cards import load_committed_chains
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.generate.project_synthesis.cards import CommittedChain
    from prompt_diary.generate.workspace import PreparedWorkspace

_EMPTY_PASTE = "(No extracted evidence chains for this project.)"


@dataclass(frozen=True)
class ProjectSynthesisInputs:
    """Rendered-ready inputs for synthesizing one project's work items."""

    project_key: str
    project_json: str
    evidence_chains: str


def build_project_synthesis_inputs(
    *,
    workspace_path: Path,
    project_key: str,
) -> ProjectSynthesisInputs:
    """Build prompt inputs for one project from the prepared workspace."""
    workspace = load_prepared_workspace(workspace_path)
    _require_project(workspace, project_key)
    project_dir = workspace_path / "projects" / project_key
    return ProjectSynthesisInputs(
        project_key=project_key,
        project_json=_normalized_json(project_dir / "project.json"),
        evidence_chains=render_evidence_chains(load_committed_chains(workspace_path, project_key)),
    )


def render_evidence_chains(chains: tuple[CommittedChain, ...]) -> str:
    """Render committed chains into the session-grouped, trimmed paste."""
    if not chains:
        return _EMPTY_PASTE
    sections: list[str] = []
    for session_ref in _session_order(chains):
        session_chains = tuple(chain for chain in chains if chain.session_ref == session_ref)
        sections.append(_render_session(session_ref, session_chains))
    return "\n\n".join(sections)


def _render_session(session_ref: str, chains: tuple[CommittedChain, ...]) -> str:
    heading = f"#### Session {session_ref} ({_count_label(len(chains))})"
    blocks = [_render_chain(chain) for chain in chains]
    return heading + "\n\n" + "\n\n".join(blocks)


def _render_chain(chain: CommittedChain) -> str:
    lines = [
        f"**{chain.session_ref}/{chain.turn_ref}** [{chain.materiality}]",
        f"trigger: {chain.trigger_summary}",
    ]
    if chain.reaction_summaries:
        lines.append(f"reaction: {' '.join(chain.reaction_summaries)}")
    if chain.outcomes:
        lines.append("outcomes:")
        lines.extend(f"- {outcome.category}: {outcome.summary}" for outcome in chain.outcomes)
    lines.append(f"terminal: {chain.terminal_type}: {chain.terminal_summary}")
    return "\n".join(lines)


def _session_order(chains: tuple[CommittedChain, ...]) -> tuple[str, ...]:
    order: list[str] = []
    for chain in chains:
        if chain.session_ref not in order:
            order.append(chain.session_ref)
    return tuple(order)


def _count_label(count: int) -> str:
    return f"{count} chain" if count == 1 else f"{count} chains"


def _require_project(workspace: PreparedWorkspace, project_key: str) -> None:
    if not any(item.project_key == project_key for item in workspace.projects):
        raise PromptDiaryError(_unknown_project_message(project_key))


def _normalized_json(path: Path) -> str:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(raw, indent=2, ensure_ascii=False)


def _unknown_project_message(project_key: str) -> str:
    return f"unknown project_key {project_key!r} in prepared workspace"
