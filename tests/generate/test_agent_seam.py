from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from prompt_diary.agent import AgentConfig, AgentTurnResult
from prompt_diary.generate.agent_language import LanguageNormAgentSessionFactory
from prompt_diary.generate.pipeline import (
    ArtifactSpec,
    GeneratePipelineRunner,
    GenerationPlan,
    PhaseRunner,
    TaskKind,
    TaskResult,
    TaskSpec,
)
from prompt_diary.language import GENERATED_AGENTS_MARKER, LanguageNorm
from prompt_diary.progress.reporter import NULL_REPORTER
from tests.agent_fakes import FakeAgentRunner, FakeAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.progress.reporter import ProgressReporter


def _ok_turn(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    return AgentTurnResult(assistant_text="done", events=())


@dataclass
class AgentDrivingRunner:
    """A representative phase runner that drives the injected agent and writes its output."""

    agent_factory: AgentSessionFactory
    prompts: list[str] = field(default_factory=list)

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        runner = await self.agent_factory.runner(AgentConfig(working_directory=workspace_path))
        result = await runner.turn(f"do {task.task_id}")
        self.prompts.append(result.assistant_text)
        for artifact in task.output_artifacts:
            output_path = workspace_path / artifact.path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("{}\n", encoding="utf-8")
        return TaskResult(task_id=task.task_id, status="success")


def test_phase_drives_mocked_agent_through_shared_factory(tmp_path: Path) -> None:
    factory = FakeAgentSessionFactory(script=_ok_turn)
    driving = AgentDrivingRunner(agent_factory=factory)
    plan = GenerationPlan(
        tasks=(
            TaskSpec(
                task_id="a",
                kind="daily_synthesis",
                output_artifacts=(ArtifactSpec(PurePosixPath("a.json"), "a"),),
            ),
            TaskSpec(
                task_id="b",
                kind="daily_synthesis",
                depends_on=("a",),
                output_artifacts=(ArtifactSpec(PurePosixPath("b.json"), "b"),),
            ),
        )
    )

    phase_runners: dict[TaskKind, PhaseRunner] = {"daily_synthesis": driving}

    async def run() -> None:
        async with factory:
            result = await GeneratePipelineRunner(phase_runners=phase_runners).run(
                workspace_path=tmp_path, plan=plan
            )
        assert result.ok

    asyncio.run(run())

    assert factory.entered == 1
    assert factory.exited == 1
    assert len(factory.runners) == 2
    assert [runner.prompts[0] for runner in factory.runners] == ["do a", "do b"]
    assert (tmp_path / "a.json").exists()
    assert (tmp_path / "b.json").exists()


def test_fake_runner_records_prompts(tmp_path: Path) -> None:
    factory = FakeAgentSessionFactory(script=_ok_turn)

    async def run() -> None:
        async with factory:
            runner = await factory.runner(AgentConfig(working_directory=tmp_path))
            await runner.turn("hello")

    asyncio.run(run())

    assert factory.runners[0].prompts == ["hello"]


@dataclass
class _RecordingFactory:
    """Records runner calls and verifies AGENTS.md exists before delegation."""

    entered: int = 0
    exited: int = 0
    configs: list[AgentConfig] = field(default_factory=list)
    saw_agents_before_runner: bool = False

    async def __aenter__(self) -> _RecordingFactory:
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

    async def runner(self, config: AgentConfig) -> FakeAgentRunner:
        self.saw_agents_before_runner = GENERATED_AGENTS_MARKER in (
            config.working_directory / "AGENTS.md"
        ).read_text(encoding="utf-8")
        self.configs.append(config)
        return FakeAgentRunner(config=config, script=_ok_turn)


def test_language_factory_writes_agents_and_merges_developer_instructions(
    tmp_path: Path,
) -> None:
    inner = _RecordingFactory()
    factory = LanguageNormAgentSessionFactory(
        inner=inner,
        workspace_path=tmp_path,
        language=LanguageNorm.from_tag("zh-Hans"),
    )

    async def run() -> None:
        async with factory:
            await factory.runner(
                AgentConfig(
                    working_directory=tmp_path,
                    developer_instructions="Phase-specific instruction.",
                )
            )

    asyncio.run(run())

    assert inner.entered == 1
    assert inner.exited == 1
    assert inner.saw_agents_before_runner is True
    merged = inner.configs[0].developer_instructions
    assert merged is not None
    assert merged.startswith("Phase-specific instruction.\n\n")
    assert "Selected content language tag: `zh-Hans`." in merged
    assert "| Generated natural-language content values | 用简体中文 (`zh-Hans`) 撰写" in merged
    assert "zh-Hans" in merged


@dataclass
class _ConcurrentDrivingRunner:
    """Drive a per-task agent only after all expected tasks are concurrently in-flight."""

    agent_factory: AgentSessionFactory
    expected: int
    all_arrived: asyncio.Event
    arrived: list[str] = field(default_factory=list)

    async def run(
        self, *, workspace_path: Path, task: TaskSpec, reporter: ProgressReporter = NULL_REPORTER
    ) -> TaskResult:
        del reporter
        runner = await self.agent_factory.runner(AgentConfig(working_directory=workspace_path))
        self.arrived.append(task.task_id)
        if len(self.arrived) >= self.expected:
            self.all_arrived.set()
        await self.all_arrived.wait()
        await runner.turn(f"do {task.task_id}")
        for artifact in task.output_artifacts:
            output_path = workspace_path / artifact.path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("{}\n", encoding="utf-8")
        return TaskResult(task_id=task.task_id, status="success")


def test_concurrent_tasks_each_get_a_conversation_off_one_shared_backend(
    tmp_path: Path,
) -> None:
    factory = FakeAgentSessionFactory(script=_ok_turn)
    plan = GenerationPlan(
        tasks=tuple(
            TaskSpec(
                task_id=f"e{index}",
                kind="evidence_extraction",
                output_artifacts=(ArtifactSpec(PurePosixPath(f"e{index}.json"), f"e{index}"),),
            )
            for index in (1, 2, 3)
        )
    )

    async def run() -> _ConcurrentDrivingRunner:
        driving = _ConcurrentDrivingRunner(
            agent_factory=factory, expected=3, all_arrived=asyncio.Event()
        )
        phase_runners: dict[TaskKind, PhaseRunner] = {"evidence_extraction": driving}
        async with factory:
            result = await asyncio.wait_for(
                GeneratePipelineRunner(phase_runners=phase_runners).run(
                    workspace_path=tmp_path, plan=plan
                ),
                timeout=2,
            )
        assert result.ok
        return driving

    driving = asyncio.run(run())

    assert factory.entered == 1
    assert factory.exited == 1
    assert len(factory.runners) == 3
    assert sorted(driving.arrived) == ["e1", "e2", "e3"]
    for index in (1, 2, 3):
        assert (tmp_path / f"e{index}.json").exists()
