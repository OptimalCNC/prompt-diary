"""Prompt-reading fake agent that fills daily-report slots via the real write_* APIs.

Like :class:`GroupingAgentRunner`, this fake never starts Codex: each turn it detects which pass it
is running from the tool name named in the prompt (``write_project_summary`` /
``write_report_title`` / ``write_engagement`` / ``write_team_learning``) and calls the matching
real write API with a valid submission derived from the workspace's committed evidence — so the
runner's slot checks, Finalize, and the Markdown render all run against real, validated data.

A valid submission must cite a committed turn (one carrying an extracted evidence chain). The fake
reads the committed chains per project from the workspace, cites the first available one (naming its
``project_key`` on the cross-project engagement/team-learning passes), and skips a write entirely
for any pass named in ``skip_pass`` so the runner's pass-failure branch can be exercised.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from prompt_diary.agent import AgentTurnResult
from prompt_diary.generate.daily_synthesis.mcp import (
    EngagementWrittenResult,
    ProjectSummaryWrittenResult,
    ReportTitleWrittenResult,
    TeamLearningWrittenResult,
    write_engagement,
    write_project_summary,
    write_report_title,
    write_team_learning,
)
from prompt_diary.generate.project_synthesis.cards import load_committed_chains
from prompt_diary.generate.workspace import load_prepared_workspace

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from prompt_diary.agent import AgentConfig
    from prompt_diary.generate.project_synthesis.cards import CommittedChain

_PROJECT_KEY_RE = re.compile(r"^- Project key: (.+)$", re.MULTILINE)

_SUMMARY = "project_summary"
_REPORT_TITLE = "report_title"
_ENGAGEMENT = "engagement"
_TEAM_LEARNING = "team_learning"


@dataclass
class DailySynthesisAgentRunner:
    """One fake conversation that fills the pass's slot from the prompt's tool name."""

    config: AgentConfig
    skip_pass: frozenset[str]
    prompts: list[str]
    tamper_citation: bool = False

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del timeout_seconds, output_schema
        self.prompts.append(prompt)
        pass_name = _pass_of(prompt)
        if pass_name not in self.skip_pass:
            self._write(pass_name, prompt)
        return AgentTurnResult(assistant_text=f"{pass_name} done", events=())

    def _write(self, pass_name: str, prompt: str) -> None:
        if pass_name == _SUMMARY:
            self._write_summary(prompt)
        elif pass_name == _REPORT_TITLE:
            self._write_report_title()
        elif pass_name == _ENGAGEMENT:
            self._write_engagement()
        else:
            self._write_team_learning()
            if self.tamper_citation:
                # Drop a resolved key from a stored citation so Finalize rejects the otherwise
                # valid report — modelling the malformed-citation state its validation guards.
                _drop_citation_lines(self.config.working_directory)

    def _write_summary(self, prompt: str) -> None:
        project_key = _require_project_key(prompt)
        chain = _first_committed(self.config.working_directory, project_key)
        _ok(
            write_project_summary(
                workspace_path=self.config.working_directory,
                project_key=project_key,
                summary={
                    "text": f"Summary of {project_key} for the day.",
                    "citations": [_project_citation(chain)],
                },
            )
        )

    def _write_report_title(self) -> None:
        chain, project_key = _first_committed_across(self.config.working_directory)
        _ok(
            write_report_title(
                workspace_path=self.config.working_directory,
                title={
                    "text": "Evidence Tools and QA Strategy",
                    "citations": [_cross_citation(chain, project_key)],
                },
            )
        )

    def _write_engagement(self) -> None:
        chain, project_key = _first_committed_across(self.config.working_directory)
        cite = [_cross_citation(chain, project_key)]
        _ok(
            write_engagement(
                workspace_path=self.config.working_directory,
                overall_reading={
                    "text": "The user framed concrete goals and reviewed results.",
                    "citations": cite,
                    "confidence": "medium",
                },
                observations=[
                    {
                        "dimension": "direction",
                        "statement": "The user supplied a concrete goal.",
                        "citations": cite,
                        "confidence": "medium",
                    }
                ],
                limits=["Offline thinking and review are not observable."],
            )
        )

    def _write_team_learning(self) -> None:
        chain, project_key = _first_committed_across(self.config.working_directory)
        cite = [_cross_citation(chain, project_key)]
        _ok(
            write_team_learning(
                workspace_path=self.config.working_directory,
                takeaways={
                    "text": "A reusable workflow is worth capturing.",
                    "citations": cite,
                    "confidence": "low",
                },
                patterns=[
                    {
                        "kind": "reuse",
                        "statement": "A repeatable approach was written down.",
                        "rationale": "It lowers the attention cost of future work.",
                        "recurrence": "single sighting; likely to recur",
                        "citations": cite,
                        "confidence": "low",
                    }
                ],
                limits=["Single-day evidence; recurrence cannot be confirmed."],
            )
        )


@dataclass
class DailySynthesisAgentSessionFactory:
    """Mints daily-synthesis fake runners off a shared record; never starts Codex."""

    skip_pass: frozenset[str] = frozenset()
    tamper_citation: bool = False
    entered: int = 0
    exited: int = 0
    runners: list[DailySynthesisAgentRunner] = field(default_factory=list)

    async def __aenter__(self) -> DailySynthesisAgentSessionFactory:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None:
        del exc_type, exc, traceback
        self.exited += 1

    async def runner(self, config: AgentConfig) -> DailySynthesisAgentRunner:
        new_runner = DailySynthesisAgentRunner(
            config=config,
            skip_pass=self.skip_pass,
            prompts=[],
            tamper_citation=self.tamper_citation,
        )
        self.runners.append(new_runner)
        return new_runner

    @property
    def prompts(self) -> list[str]:
        """All prompts seen across every minted runner, in turn order."""
        return [prompt for runner in self.runners for prompt in runner.prompts]


def _pass_of(prompt: str) -> str:
    if "write_project_summary" in prompt:
        return _SUMMARY
    if "write_report_title" in prompt:
        return _REPORT_TITLE
    if "write_engagement" in prompt:
        return _ENGAGEMENT
    if "write_team_learning" in prompt:
        return _TEAM_LEARNING
    raise AssertionError(_unknown_pass_message())


def _first_committed(workspace_path: Path, project_key: str) -> CommittedChain:
    chains = load_committed_chains(workspace_path, project_key)
    if not chains:
        raise AssertionError(_no_committed_message(project_key))
    return chains[0]


def _first_committed_across(workspace_path: Path) -> tuple[CommittedChain, str]:
    workspace = load_prepared_workspace(workspace_path)
    for project in workspace.projects:
        chains = load_committed_chains(workspace_path, project.project_key)
        if chains:
            return chains[0], project.project_key
    raise AssertionError(_no_committed_anywhere_message())


def _drop_citation_lines(workspace_path: Path) -> None:
    path = workspace_path / "daily-report.json"
    report = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
    learning = cast("dict[str, Any]", report["team_learning"])
    takeaways = cast("dict[str, Any]", learning["takeaways"])
    citation = cast("dict[str, Any]", cast("list[Any]", takeaways["citations"])[0])
    del citation["lines"]
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _project_citation(chain: CommittedChain) -> dict[str, str]:
    return {"session_ref": chain.session_ref, "turn_ref": chain.turn_ref}


def _cross_citation(chain: CommittedChain, project_key: str) -> dict[str, str]:
    return {
        "project_key": project_key,
        "session_ref": chain.session_ref,
        "turn_ref": chain.turn_ref,
    }


_WRITTEN = (
    ProjectSummaryWrittenResult,
    ReportTitleWrittenResult,
    EngagementWrittenResult,
    TeamLearningWrittenResult,
)


def _ok(result: object) -> None:
    # The fake's submissions are derived from committed evidence, so a rejection is a test bug.
    if isinstance(result, _WRITTEN):
        return
    raise AssertionError(_rejected_message(result))


def _require_project_key(prompt: str) -> str:
    match = _PROJECT_KEY_RE.search(prompt)
    if match is None:
        raise AssertionError(_missing_project_key_message())
    return match.group(1).strip()


def _rejected_message(result: object) -> str:
    return f"fake agent daily-report write was rejected: {result!r}"


def _unknown_pass_message() -> str:
    return "fake agent could not detect the pass from the prompt"


def _missing_project_key_message() -> str:
    return "fake agent could not find the project key in the summary prompt"


def _no_committed_message(project_key: str) -> str:
    return f"fake agent found no committed chain to cite for project {project_key!r}"


def _no_committed_anywhere_message() -> str:
    return "fake agent found no committed chain to cite in any project"
