# Agent-Mock Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an injectable, mockable agent seam so generation phases spawn Codex agents through a neutral port, one shared backend per run, substitutable by a fake in tests with no SDK.

**Architecture:** A neutral port `prompt_diary/agent.py` (`AgentRunner` + `AgentSessionFactory` protocols and the moved `AgentConfig`/`AgentTurnEvent`/`AgentTurnResult` value types). `integrations/codex_runner.py` provides the `CodexAgentSessionFactory` adapter (one backend via `AsyncExitStack`, lifecycle-free runners minted per call). Phase runners take a required `agent_factory`; the workflow takes a required `agent_factory` and enters it once per run; the CLI composition root `build_generation_workflow()` wires one shared factory into all phases and the workflow. `generate/` depends only on the port, never on `integrations/`.

**Tech Stack:** Python 3.10+, `uv`, `dataclasses`, `typing.Protocol`, `contextlib.AsyncExitStack`, pytest, basedpyright (strict), ruff.

> **Project rule (user instruction):** Do NOT commit and do NOT branch. Implement directly in the current worktree. Tasks end with verification gates, not commits. Backward compatibility is explicitly NOT a constraint — rewrite existing code and tests for the cleanest end state.

> **Coverage note:** The package requires 100% line coverage (`coverage report --fail-under=100`), `source = ["prompt_diary"]`, with `src/prompt_diary/integrations/codex_runner.py` omitted. New code in `agent.py`, the phase runners, `workflow.py`, and `cmds/generate.py` MUST be covered. `tests/` files are not coverage-measured. Protocol `...` bodies are auto-excluded by coverage 7.14+ (mirror the existing `PhaseRunner` stub in `pipeline.py`).

> **Lint note:** ruff `TC` (flake8-type-checking) requires imports used ONLY in annotations to live under `if TYPE_CHECKING:`. Imports used at runtime (constructed/called) stay at module top. Unused function/method args must be `del`-ed (ruff `ARG`).

---

### Task 1: Neutral port `agent.py` and rewire `codex_runner.py`

**Files:**
- Create: `src/prompt_diary/agent.py`
- Modify: `src/prompt_diary/integrations/codex_runner.py` (remove the three value-type dataclasses; import them from `agent.py`)
- Modify: `tests/integrations/test_codex_runner.py` (import value types from `agent.py`)

- [ ] **Step 1: Create `src/prompt_diary/agent.py`**

```python
"""Neutral agent execution contract shared by generation phases and runner adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import TracebackType


@dataclass(frozen=True)
class AgentConfig:
    """Per-conversation agent configuration."""

    working_directory: Path
    model: str | None = None
    model_provider: str | None = None
    reasoning_effort: str | None = None
    approval_mode: str | None = None
    sandbox: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    personality: str | None = None


@dataclass(frozen=True)
class AgentTurnEvent:
    """Structured event summary emitted while running one agent turn."""

    kind: str
    summary: str
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class AgentTurnResult:
    """Result from one agent turn."""

    assistant_text: str
    events: tuple[AgentTurnEvent, ...]


class AgentRunner(Protocol):
    """One agent conversation. Turns run sequentially on a single instance."""

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        """Run one prompt turn and return its structured result."""
        ...


class AgentSessionFactory(Protocol):
    """Owns one shared backend and mints a fresh agent conversation per call."""

    async def __aenter__(self) -> AgentSessionFactory:
        """Start the shared backend."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Stop the shared backend."""
        ...

    async def runner(self, config: AgentConfig) -> AgentRunner:
        """Return a fresh agent conversation bound to the shared backend."""
        ...
```

- [ ] **Step 2: Remove the moved dataclasses from `codex_runner.py`**

Delete the `AgentConfig`, `AgentTurnEvent`, and `AgentTurnResult` class definitions (currently lines ~38-67). Keep `CodexBackendConfig`, `CodexRunnerError`, `_empty_env_overrides`, and everything else.

- [ ] **Step 3: Import the moved types in `codex_runner.py`**

Add a runtime import (these are constructed in `_agent_turn_result`/`_agent_turn_event`):

```python
from prompt_diary.agent import AgentTurnEvent, AgentTurnResult
```

Add to the existing `if TYPE_CHECKING:` block (used only in annotations):

```python
    from prompt_diary.agent import AgentConfig
```

(`JsonObject` stays under `TYPE_CHECKING` as before.)

- [ ] **Step 4: Update value-type imports in `tests/integrations/test_codex_runner.py`**

Change the import block so `AgentConfig`, `AgentTurnEvent`, `AgentTurnResult` come from `prompt_diary.agent`, while `CodexAgentRunner`, `CodexBackend`, `CodexBackendConfig`, `CodexRunnerError` stay from `prompt_diary.integrations.codex_runner`:

```python
import prompt_diary.integrations.codex_runner as codex_runner
from prompt_diary.agent import AgentConfig, AgentTurnEvent, AgentTurnResult
from prompt_diary.integrations.codex_runner import (
    CodexAgentRunner,
    CodexBackend,
    CodexBackendConfig,
    CodexRunnerError,
)
```

- [ ] **Step 5: Verify types, lint, and the wrapper tests**

Run:
```bash
uv run basedpyright
uv run ruff check
uv run ruff format --check
uv run pytest tests/integrations/test_codex_runner.py -v
```
Expected: basedpyright 0 errors; ruff clean; all `test_codex_runner.py` tests PASS.

---

### Task 2: `CodexAgentSessionFactory` adapter (TDD)

**Files:**
- Modify: `src/prompt_diary/integrations/codex_runner.py` (add the factory)
- Test: `tests/integrations/test_codex_runner.py` (add factory tests using the existing SDK-fake)

- [ ] **Step 1: Write the failing tests**

Add to `tests/integrations/test_codex_runner.py` (import `AgentSessionFactory` from `prompt_diary.agent` and `CodexAgentSessionFactory` from `codex_runner` — add both to the existing imports):

```python
def test_codex_session_factory_shares_one_backend_across_runners(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_sdk(monkeypatch)

    async def exercise() -> None:
        async with CodexAgentSessionFactory(CodexBackendConfig()) as factory:
            runner_one = await factory.runner(AgentConfig(working_directory=tmp_path))
            runner_two = await factory.runner(AgentConfig(working_directory=tmp_path))
            await runner_one.turn("first")
            await runner_two.turn("second")
        assert FakeAsyncCodex.instances[0].exited

    asyncio.run(exercise())

    assert len(FakeAsyncCodex.instances) == 1
    assert len(FakeAsyncCodex.instances[0].thread_start_calls) == 2


def test_codex_session_factory_satisfies_agent_session_factory() -> None:
    factory: AgentSessionFactory = CodexAgentSessionFactory(CodexBackendConfig())
    assert isinstance(factory, CodexAgentSessionFactory)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integrations/test_codex_runner.py::test_codex_session_factory_shares_one_backend_across_runners -v`
Expected: FAIL — `CodexAgentSessionFactory` is not defined.

- [ ] **Step 3: Implement `CodexAgentSessionFactory`**

Add `from contextlib import AsyncExitStack` at module top of `codex_runner.py`. Add an `AgentRunner` annotation import under `if TYPE_CHECKING:` (`from prompt_diary.agent import AgentConfig, AgentRunner`). Add the class after `CodexAgentRunner`:

```python
class CodexAgentSessionFactory:
    """Own one shared Codex backend and mint a fresh conversation per call."""

    def __init__(self, backend_config: CodexBackendConfig) -> None:
        self._backend_config = backend_config
        self._stack: AsyncExitStack | None = None
        self._backend: CodexBackend | None = None

    async def __aenter__(self) -> CodexAgentSessionFactory:
        """Start the shared backend."""
        stack = AsyncExitStack()
        await stack.__aenter__()
        self._backend = await stack.enter_async_context(CodexBackend(self._backend_config))
        self._stack = stack
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Stop the shared backend."""
        stack = self._stack
        self._stack = None
        self._backend = None
        if stack is None:
            return None
        return await stack.__aexit__(exc_type, exc, traceback)

    async def runner(self, config: AgentConfig) -> AgentRunner:
        """Return a fresh conversation bound to the shared backend."""
        if self._backend is None:
            raise CodexRunnerError(_backend_not_started_message())
        return CodexAgentRunner(self._backend, config)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/integrations/test_codex_runner.py -v`
Expected: PASS (both new tests and all existing tests).

- [ ] **Step 5: Verify types and lint**

Run:
```bash
uv run basedpyright
uv run ruff check
uv run ruff format --check
```
Expected: 0 errors, clean. (`codex_runner.py` is coverage-omitted, so no coverage gate for the factory body.)

---

### Task 3: Shared test fake `tests/agent_fakes.py`

**Files:**
- Create: `tests/agent_fakes.py`

- [ ] **Step 1: Create the fake**

```python
"""Reusable in-memory fakes for the agent seam, used by phase and seam tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from prompt_diary.agent import AgentConfig, AgentRunner, AgentTurnResult


@dataclass
class FakeAgentRunner:
    """Records prompts and returns a scripted result, optionally with side effects."""

    config: AgentConfig
    script: Callable[[str, AgentConfig], AgentTurnResult]
    prompts: list[str] = field(default_factory=list)

    async def turn(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 600.0,
        output_schema: Mapping[str, object] | None = None,
    ) -> AgentTurnResult:
        del timeout_seconds, output_schema
        self.prompts.append(prompt)
        return self.script(prompt, self.config)


@dataclass
class FakeAgentSessionFactory:
    """Counts lifecycle entries and mints recording runners; never starts Codex."""

    script: Callable[[str, AgentConfig], AgentTurnResult]
    entered: int = 0
    exited: int = 0
    runners: list[FakeAgentRunner] = field(default_factory=list)

    async def __aenter__(self) -> FakeAgentSessionFactory:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        self.exited += 1

    async def runner(self, config: AgentConfig) -> AgentRunner:
        new_runner = FakeAgentRunner(config=config, script=self.script)
        self.runners.append(new_runner)
        return new_runner
```

- [ ] **Step 2: Verify types and lint**

Run:
```bash
uv run basedpyright
uv run ruff check
uv run ruff format --check
```
Expected: 0 errors, clean. (`tests/` has `__init__.py`, so `INP` is satisfied; the module is importable as `tests.agent_fakes`.)

---

### Task 4: Phase runners require `agent_factory`; drop the free functions

**Files:**
- Modify: `src/prompt_diary/generate/evidence_extraction/runner.py`
- Modify: `src/prompt_diary/generate/evidence_extraction/__init__.py`
- Modify: `src/prompt_diary/generate/project_synthesis/runner.py`
- Modify: `src/prompt_diary/generate/project_synthesis/__init__.py`
- Modify: `src/prompt_diary/generate/daily_synthesis/runner.py`
- Modify: `src/prompt_diary/generate/daily_synthesis/__init__.py`
- Modify: `tests/generate/test_pipeline.py` (placeholder test + imports)

- [ ] **Step 1: Rewrite `evidence_extraction/runner.py`**

```python
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
```

- [ ] **Step 2: Rewrite `project_synthesis/runner.py`**

```python
"""Project synthesis phase runner placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskResult, TaskSpec


@dataclass(frozen=True)
class ProjectSynthesisRunner:
    """Run project synthesis tasks."""

    agent_factory: AgentSessionFactory

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        """Run one project synthesis task."""
        del workspace_path, task
        raise PromptDiaryError(_not_implemented_message())


def _not_implemented_message() -> str:
    return "project synthesis phase runner is not implemented yet"
```

- [ ] **Step 3: Rewrite `daily_synthesis/runner.py`**

```python
"""Daily synthesis phase runner placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import TaskResult, TaskSpec


@dataclass(frozen=True)
class DailySynthesisRunner:
    """Run daily synthesis tasks."""

    agent_factory: AgentSessionFactory

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        """Run the daily synthesis task."""
        del workspace_path, task
        raise PromptDiaryError(_not_implemented_message())


def _not_implemented_message() -> str:
    return "daily synthesis phase runner is not implemented yet"
```

- [ ] **Step 4: Update the three phase `__init__.py` exports**

`evidence_extraction/__init__.py` — PRESERVE the `mcp.py` exports added by commit `ff816a7`; only drop `run_evidence_extraction`. Re-read the file first; the result should be:
```python
"""Evidence extraction phase package."""

from prompt_diary.generate.evidence_extraction.mcp import (
    EvidenceWriteError,
    WriteEvidenceAppendedResult,
    WriteEvidenceInvalidResult,
    WriteEvidenceResult,
    write_evidence,
)
from prompt_diary.generate.evidence_extraction.runner import EvidenceExtractionRunner

__all__ = [
    "EvidenceExtractionRunner",
    "EvidenceWriteError",
    "WriteEvidenceAppendedResult",
    "WriteEvidenceInvalidResult",
    "WriteEvidenceResult",
    "write_evidence",
]
```
(If the `mcp.py` export list has changed again, re-read and keep whatever it currently exports; only remove `run_evidence_extraction`.)

`project_synthesis/__init__.py`:
```python
"""Project synthesis phase package."""

from prompt_diary.generate.project_synthesis.runner import ProjectSynthesisRunner

__all__ = ["ProjectSynthesisRunner"]
```

`daily_synthesis/__init__.py`:
```python
"""Daily synthesis phase package."""

from prompt_diary.generate.daily_synthesis.runner import DailySynthesisRunner

__all__ = ["DailySynthesisRunner"]
```

(Match the existing docstring/format style of each `__init__.py`; only the removed `run_*` symbol changes.)

- [ ] **Step 5: Rewrite the placeholder test in `tests/generate/test_pipeline.py`**

Remove `run_evidence_extraction`, `run_project_synthesis`, `run_daily_synthesis` from the imports (and the `ProjectSynthesisRunner`/`run_project_synthesis` and `run_daily_synthesis` import lines). Keep `EvidenceExtractionRunner`, add `ProjectSynthesisRunner`, `DailySynthesisRunner`. Add `from tests.agent_fakes import FakeAgentSessionFactory` and a module-level unused script. Replace `test_standalone_phase_placeholders_fail_explicitly` with:

```python
def test_standalone_phase_placeholders_fail_explicitly(tmp_path: Path) -> None:
    task = TaskSpec(task_id="placeholder", kind="daily_synthesis")
    factory = FakeAgentSessionFactory(script=_unused_agent_script)

    with pytest.raises(PromptDiaryError, match="evidence extraction phase runner"):
        asyncio.run(EvidenceExtractionRunner(agent_factory=factory).run(
            workspace_path=tmp_path, task=task))
    with pytest.raises(PromptDiaryError, match="project synthesis phase runner"):
        asyncio.run(ProjectSynthesisRunner(agent_factory=factory).run(
            workspace_path=tmp_path, task=task))
    with pytest.raises(PromptDiaryError, match="daily synthesis phase runner"):
        asyncio.run(DailySynthesisRunner(agent_factory=factory).run(
            workspace_path=tmp_path, task=task))
```

Add this module-level helper near the other helpers (it must never be called):

```python
def _unused_agent_script(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    raise AssertionError("placeholder phase runners must not mint an agent turn")
```

Add the imports it needs at the top of the file:
```python
from prompt_diary.agent import AgentConfig, AgentTurnResult
```
(These are used in the helper's signature; with `from __future__ import annotations` already present they may go under `TYPE_CHECKING`, but the function body references neither at runtime, so place them under the existing `if TYPE_CHECKING:` block.)

- [ ] **Step 6: Run the pipeline tests, types, and lint**

Run:
```bash
uv run pytest tests/generate/test_pipeline.py -v
uv run basedpyright
uv run ruff check
uv run ruff format --check
```
Expected: all PASS; 0 type/lint errors. (The agentless fakes `WritingPhaseRunner`, `FailingEvidenceRunner`, etc. used with `GeneratePipelineRunner` are unchanged — they implement `PhaseRunner.run` only and never needed a factory.)

---

### Task 5: Workflow factory scope + CLI composition root + entry-point tests

**Files:**
- Modify: `src/prompt_diary/generate/workflow.py`
- Modify: `src/prompt_diary/cmds/generate.py`
- Modify: `tests/generate/test_workflow.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_prompt_diary_e2e_qa.py`

- [ ] **Step 1: Rewrite `generate/workflow.py`**

Make `agent_factory` a required field, enter it once per run inside `asyncio.run`, and delete `default_phase_runners`, `_phase_runners_or_default`, `run_generate_pipeline`, and `run_generate_phase`. New file:

```python
"""Generation workflow API built on the artifact-first pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.pipeline import (
    GeneratePipelineRunner,
    PhaseRunner,
    PipelineRunResult,
    TaskKind,
    TaskResult,
    TaskSpec,
    build_generation_plan,
    daily_synthesis_task_id,
    evidence_task_id,
    project_synthesis_task_id,
    run_generation_task_with_lifecycle,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory
    from prompt_diary.generate.pipeline import GenerationPlan

PhaseName = Literal["evidence", "project", "daily"]


@dataclass(frozen=True)
class GeneratePipelineWorkflowResult:
    """Result from running the full generation pipeline."""

    workspace_path: Path
    daily_report_path: Path
    report_path: Path
    pipeline_result: PipelineRunResult
    messages: tuple[str, ...]


@dataclass(frozen=True)
class GeneratePhaseWorkflowResult:
    """Result from running one generation phase task."""

    workspace_path: Path
    task: TaskSpec
    task_result: TaskResult
    messages: tuple[str, ...]


@dataclass(frozen=True)
class GenerateWorkspaceWorkflow:
    """Run generation workflows against one prepared workspace."""

    phase_runners: Mapping[TaskKind, PhaseRunner]
    agent_factory: AgentSessionFactory

    def run_pipeline(
        self,
        *,
        workspace_path: Path,
        messages: tuple[str, ...] = (),
    ) -> GeneratePipelineWorkflowResult:
        """Run the full generation pipeline from a prepared workspace."""
        _require_workspace(workspace_path)
        plan = build_generation_plan(workspace_path)
        pipeline_result = asyncio.run(self._run_plan(workspace_path=workspace_path, plan=plan))
        if not pipeline_result.ok:
            raise PromptDiaryError(_pipeline_failed_message(pipeline_result))

        report_path = workspace_path / "report.md"
        daily_report_path = workspace_path / "daily-report.json"
        return GeneratePipelineWorkflowResult(
            workspace_path=workspace_path,
            daily_report_path=daily_report_path,
            report_path=report_path,
            pipeline_result=pipeline_result,
            messages=(
                *messages,
                f"Wrote daily report model {daily_report_path}.",
                f"Wrote rendered report {report_path}.",
            ),
        )

    def run_phase(
        self,
        *,
        workspace_path: Path,
        phase: PhaseName,
        project_key: str | None = None,
        session_ref: str | None = None,
    ) -> GeneratePhaseWorkflowResult:
        """Run one generation phase task from a prepared workspace."""
        _require_workspace(workspace_path)
        task = _select_task(
            workspace_path=workspace_path,
            phase=phase,
            project_key=project_key,
            session_ref=session_ref,
        )
        task_result = asyncio.run(self._run_task(workspace_path=workspace_path, task=task))
        if not task_result.ok:
            raise PromptDiaryError(_task_failed_message(task_result))
        return GeneratePhaseWorkflowResult(
            workspace_path=workspace_path,
            task=task,
            task_result=task_result,
            messages=(f"Completed generation task {task.task_id}.",),
        )

    async def _run_plan(self, *, workspace_path: Path, plan: GenerationPlan) -> PipelineRunResult:
        runner = GeneratePipelineRunner(phase_runners=self.phase_runners)
        async with self.agent_factory:
            return await runner.run(workspace_path=workspace_path, plan=plan)

    async def _run_task(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        phase_runner = self.phase_runners[task.kind]
        async with self.agent_factory:
            return await run_generation_task_with_lifecycle(
                workspace_path=workspace_path,
                task=task,
                phase_runner=phase_runner,
            )


def _select_task(
    *,
    workspace_path: Path,
    phase: PhaseName,
    project_key: str | None,
    session_ref: str | None,
) -> TaskSpec:
    plan = build_generation_plan(workspace_path)
    tasks = plan.task_map()
    task_id = _task_id_for_phase(phase=phase, project_key=project_key, session_ref=session_ref)
    task = tasks.get(task_id)
    if task is None:
        raise PromptDiaryError(_missing_task_message(task_id))
    return task


def _require_workspace(workspace_path: Path) -> None:
    if not workspace_path.exists():
        raise PromptDiaryError(_missing_workspace_message(workspace_path))


def _task_id_for_phase(
    *,
    phase: PhaseName,
    project_key: str | None,
    session_ref: str | None,
) -> str:
    if phase == "evidence":
        if project_key is None or session_ref is None:
            raise PromptDiaryError(_evidence_scope_message())
        return evidence_task_id(project_key, session_ref)
    if phase == "project":
        if project_key is None:
            raise PromptDiaryError(_project_scope_message())
        return project_synthesis_task_id(project_key)
    return daily_synthesis_task_id()


def _pipeline_failed_message(result: PipelineRunResult) -> str:
    failed = [task_result for task_result in result.results if not task_result.ok]
    details = "\n".join(
        f"- {task_result.task_id}: {'; '.join(task_result.errors) or task_result.status}"
        for task_result in failed
    )
    return f"Generation pipeline failed:\n{details}"


def _task_failed_message(result: TaskResult) -> str:
    details = "\n".join(f"- {error}" for error in result.errors)
    if details:
        return f"Generation task {result.task_id} failed:\n{details}"
    return f"Generation task {result.task_id} failed with status {result.status}."


def _missing_workspace_message(workspace_path: Path) -> str:
    return f"prepared workspace is missing: {workspace_path}"


def _missing_task_message(task_id: str) -> str:
    return f"generation task is not present in the prepared workspace: {task_id}"


def _evidence_scope_message() -> str:
    return "evidence phase requires --project-key and --session-ref"


def _project_scope_message() -> str:
    return "project phase requires --project-key"
```

- [ ] **Step 2: Rewrite the wiring in `cmds/generate.py`**

Replace the `run_generate_phase`/`run_generate_pipeline` import and calls with a composition-root builder. Update the import block:

```python
from prompt_diary.generate.daily_synthesis import DailySynthesisRunner
from prompt_diary.generate.evidence_extraction import EvidenceExtractionRunner
from prompt_diary.generate.project_synthesis import ProjectSynthesisRunner
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow, PhaseName
from prompt_diary.integrations.codex_runner import CodexAgentSessionFactory, CodexBackendConfig
```

Add the builder (the only place that knows both `generate/` and `integrations/`):

```python
def build_generation_workflow() -> GenerateWorkspaceWorkflow:
    """Build the default generation workflow with one shared Codex agent backend."""
    factory = CodexAgentSessionFactory(CodexBackendConfig())
    return GenerateWorkspaceWorkflow(
        phase_runners={
            "evidence_extraction": EvidenceExtractionRunner(agent_factory=factory),
            "project_synthesis": ProjectSynthesisRunner(agent_factory=factory),
            "daily_synthesis": DailySynthesisRunner(agent_factory=factory),
        },
        agent_factory=factory,
    )
```

In `generate(...)`, replace the pipeline call:
```python
        workflow = build_generation_workflow()
        result = workflow.run_pipeline(workspace_path=workspace_path, messages=messages)
```

In `_run_phase_command(...)`, replace the phase call:
```python
        workflow = build_generation_workflow()
        result = workflow.run_phase(
            workspace_path=workspace_path,
            phase=phase,
            project_key=project_key,
            session_ref=session_ref,
        )
```

- [ ] **Step 3: Rewrite `tests/generate/test_workflow.py` to construct the workflow directly**

Replace the `run_generate_pipeline`/`run_generate_phase` import with `GenerateWorkspaceWorkflow`, add the fake factory + a no-material script, and route every call through a constructed workflow. Update imports:

```python
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow
from tests.agent_fakes import FakeAgentSessionFactory
```

Add a module-level script and a workflow builder near the helpers:

```python
def _no_agent_turns(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    raise AssertionError("workflow tests use file-writing phase runners, not agent turns")


def _workflow(phase_runner: PhaseRunner) -> GenerateWorkspaceWorkflow:
    return GenerateWorkspaceWorkflow(
        phase_runners=_all_phase_runners(phase_runner),
        agent_factory=FakeAgentSessionFactory(script=_no_agent_turns),
    )
```

Add the imports the script needs under the existing `if TYPE_CHECKING:` block:
```python
    from prompt_diary.agent import AgentConfig, AgentTurnResult
```

Rewrite each test body to use `_workflow(...)` and call its methods. Concretely:
- `run_generate_pipeline(workspace_path=W, phase_runners=_all_phase_runners(R), messages=M)` → `_workflow(R).run_pipeline(workspace_path=W, messages=M)`.
- `run_generate_phase(workspace_path=W, phase=P, project_key=K, session_ref=S, phase_runners=_all_phase_runners(R))` → `_workflow(R).run_phase(workspace_path=W, phase=P, project_key=K, session_ref=S)`.

Delete `test_generate_workflow_default_pipeline_fails_until_phase_runners_are_implemented` (it asserted the removed `None`→default behavior; the default now requires a real Codex backend and is covered by the build-workflow construction test in Step 5).

- [ ] **Step 4: Run the workflow tests**

Run: `uv run pytest tests/generate/test_workflow.py -v`
Expected: all PASS (the `FakeAgentSessionFactory` is entered/exited around each run; file-writing phase runners never mint an agent).

- [ ] **Step 5: Rewrite the CLI generation tests in `tests/test_cli.py`**

The generate tests currently monkeypatch `generate_cmd.run_generate_pipeline` / `run_generate_phase`. Retarget them to `generate_cmd.build_generation_workflow`, returning a fake workflow. Add near the top of the test module:

```python
@dataclass
class _FakeWorkflowResult:
    messages: tuple[str, ...]


@dataclass
class _FakeWorkflow:
    pipeline_messages: tuple[str, ...] = ()
    phase_messages: tuple[str, ...] = ()
    pipeline_error: str | None = None
    phase_error: str | None = None

    def run_pipeline(
        self, *, workspace_path: Path, messages: tuple[str, ...] = ()
    ) -> _FakeWorkflowResult:
        del workspace_path
        if self.pipeline_error is not None:
            raise PromptDiaryError(self.pipeline_error)
        return _FakeWorkflowResult(messages=(*messages, *self.pipeline_messages))

    def run_phase(
        self,
        *,
        workspace_path: Path,
        phase: str,
        project_key: str | None = None,
        session_ref: str | None = None,
    ) -> _FakeWorkflowResult:
        del workspace_path, phase, project_key, session_ref
        if self.phase_error is not None:
            raise PromptDiaryError(self.phase_error)
        return _FakeWorkflowResult(messages=self.phase_messages)
```

(Add `from dataclasses import dataclass` to the imports.) Then update each affected test's monkeypatch:
- `test_generate_error_exits_with_stderr`: replace the `run_generate_pipeline` monkeypatch with
  `monkeypatch.setattr(generate_cmd, "build_generation_workflow", lambda: _FakeWorkflow(pipeline_error=GENERATE_FAILED))`. Keep the `workspace_for_generate_target` monkeypatch.
- `test_generate_prints_pipeline_messages`: drop the `Result`/`fake_run_generate_pipeline` block; set `monkeypatch.setattr(generate_cmd, "build_generation_workflow", lambda: _FakeWorkflow(pipeline_messages=("generated",)))` and keep `workspace_for_generate_target` returning `(tmp_path, ("prepared",))`. Expected stdout stays `"prepared\ngenerated\n"`.
- `test_generate_phase_error_exits_with_stderr`, `test_generate_project_error_exits_with_stderr`, `test_generate_daily_error_exits_with_stderr`: replace the `run_generate_phase` monkeypatch with
  `monkeypatch.setattr(generate_cmd, "build_generation_workflow", lambda: _FakeWorkflow(phase_error=PHASE_FAILED))`. Keep the `workspace_for_existing_target` monkeypatch.
- `test_generate_phase_commands_delegate`: this asserts the `(phase, project_key, session_ref)` calls. Capture them via a fake workflow that records:
  ```python
  calls: list[tuple[str, str | None, str | None]] = []

  @dataclass
  class _RecordingWorkflow:
      def run_phase(self, *, workspace_path, phase, project_key=None, session_ref=None):
          del workspace_path
          calls.append((phase, project_key, session_ref))
          return _FakeWorkflowResult(messages=("completed",))

  monkeypatch.setattr(generate_cmd, "build_generation_workflow", lambda: _RecordingWorkflow())
  ```
  Keep the `workspace_for_existing_target` monkeypatch and the existing assertions on `calls`.

- [ ] **Step 6: Add a construction test for the real builder (coverage)**

In `tests/test_cli.py`, add a test that exercises the real `build_generation_workflow` (construction only — no Codex spawn, since the backend starts lazily on `__aenter__`, not on construction):

```python
def test_build_generation_workflow_wires_one_shared_factory() -> None:
    workflow = generate_cmd.build_generation_workflow()

    assert set(workflow.phase_runners) == {
        "evidence_extraction",
        "project_synthesis",
        "daily_synthesis",
    }
    factories = {id(runner.agent_factory) for runner in workflow.phase_runners.values()}
    assert factories == {id(workflow.agent_factory)}
```

(Each placeholder runner exposes its injected `agent_factory`; this asserts all three share the single instance the workflow holds. Add `import prompt_diary.cmds.generate as generate_cmd` if not already imported — it is.)

- [ ] **Step 7: Rewrite the e2e override in `tests/test_prompt_diary_e2e_qa.py`**

It currently monkeypatches `generate_workflow.default_phase_runners`. Retarget to `cmds.generate.build_generation_workflow`, returning a workflow built from file-writing phase runners + a fake factory. Update imports:

```python
import prompt_diary.cmds.generate as generate_cmd
from prompt_diary.generate.workflow import GenerateWorkspaceWorkflow
from tests.agent_fakes import FakeAgentSessionFactory
```

Add the script helper (with `AgentConfig`/`AgentTurnResult` under `TYPE_CHECKING`):

```python
def _no_agent_turns(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    raise AssertionError("e2e uses a file-writing phase runner, not agent turns")
```

In both e2e tests, replace the `default_phase_runners` monkeypatch with:

```python
    phase_runner = WritingPhaseRunner()
    monkeypatch.setattr(
        generate_cmd,
        "build_generation_workflow",
        lambda: GenerateWorkspaceWorkflow(
            phase_runners=_all_phase_runners(phase_runner),
            agent_factory=FakeAgentSessionFactory(script=_no_agent_turns),
        ),
    )
```

- [ ] **Step 8: Run the full suite, types, lint**

Run:
```bash
uv run pytest -v
uv run basedpyright
uv run ruff check
uv run ruff format --check
```
Expected: all PASS; 0 type/lint errors.

---

### Task 6: Seam proof tests (sharing, lifecycle, agent-driving)

**Files:**
- Create: `tests/generate/test_agent_seam.py`

- [ ] **Step 1: Write the proof tests**

These prove a real phase can drive a mocked agent through the real workflow scope, and that one factory is shared and entered once per run. The in-test phase runner mints a runner, calls `turn()`, and writes its declared artifact.

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from prompt_diary.agent import AgentConfig, AgentTurnResult
from prompt_diary.generate.pipeline import (
    ArtifactSpec,
    GeneratePipelineRunner,
    GenerationPlan,
    PhaseRunner,
    TaskKind,
    TaskResult,
    TaskSpec,
)
from tests.agent_fakes import FakeAgentSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_diary.agent import AgentSessionFactory


def _ok_turn(prompt: str, config: AgentConfig) -> AgentTurnResult:
    del prompt, config
    return AgentTurnResult(assistant_text="done", events=())


@dataclass
class AgentDrivingRunner:
    """A representative phase runner that drives the injected agent and writes its output."""

    agent_factory: AgentSessionFactory
    prompts: list[str] = field(default_factory=list)

    async def run(self, *, workspace_path: Path, task: TaskSpec) -> TaskResult:
        runner = await self.agent_factory.runner(
            AgentConfig(working_directory=workspace_path)
        )
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
    assert len(factory.runners) == 2          # one conversation per task
    assert [r.prompts[0] for r in factory.runners] == ["do a", "do b"]
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
```

- [ ] **Step 2: Run the proof tests**

Run: `uv run pytest tests/generate/test_agent_seam.py -v`
Expected: both tests PASS.

- [ ] **Step 3: Verify types and lint**

Run:
```bash
uv run basedpyright
uv run ruff check
uv run ruff format --check
```
Expected: 0 errors, clean.

---

### Task 7: Update developer docs

**Files:**
- Modify: `docs/src/dev/codex-agent-runner.md`
- Modify: `docs/src/dev/generation-pipeline.md`
- Modify: `docs/src/dev/architecture.md`

- [ ] **Step 1: Update `codex-agent-runner.md`**

Add a section describing the neutral port `prompt_diary/agent.py` (the `AgentRunner` and `AgentSessionFactory` protocols, and that `AgentConfig`/`AgentTurnEvent`/`AgentTurnResult` now live there). Document `CodexAgentSessionFactory` as the adapter that owns one backend via `AsyncExitStack` and mints lifecycle-free `CodexAgentRunner` conversations. Update the "Coverage" section: phase tests mock at the `AgentSessionFactory` seam (a fake factory), while the wrapper's own tests mock the Codex SDK import.

- [ ] **Step 2: Update `generation-pipeline.md`**

Revise the `PhaseRunner` section: backend ownership has moved from the phase to the run scope. Each concrete phase runner holds an injected `AgentSessionFactory`; the workflow enters one shared factory once per run (inside `asyncio.run`), and every task mints its own conversation off the shared backend. Replace the "runner may own one Codex backend reused by multiple conversations / the full pipeline enters each unique managed runner once" wording with the factory-at-workflow-scope model. Keep the agent-agnostic description of `GeneratePipelineRunner`.

- [ ] **Step 3: Update `architecture.md`**

Add a codemap row: `src/prompt_diary/agent.py` — "Neutral agent execution contract (port): `AgentRunner`/`AgentSessionFactory` protocols and shared agent value types, depended on by generation phases and runner adapters." Note that the generation phase wiring composition root lives in the CLI layer (`cmds/generate.py::build_generation_workflow`), and that `integrations/codex_runner.py` provides the `CodexAgentSessionFactory` adapter.

- [ ] **Step 4: Check the README generation note and any docs build**

Confirm `README.md`'s line that "generation currently fails clearly at the unimplemented phase runner" still reads true; if a docs build is configured (look for `book.toml`), run it; otherwise re-read the three edited pages for accuracy.

Run (if `book.toml` exists):
```bash
ls docs/book.toml 2>/dev/null && (cd docs && mdbook build) || echo "no mdbook config; review docs manually"
```
Expected: docs build succeeds, or manual review confirms accuracy.

---

### Task 8: Full pre-submit verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete pre-submit sequence**

Run:
```bash
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest
uv run coverage run -m pytest
uv run coverage report
uv build
```
Expected: ruff clean; basedpyright 0 errors; `coverage report` shows **100%** for `prompt_diary` (with `codex_runner.py` omitted); `uv build` succeeds.

**Baseline note (concurrent commit `ff816a7`):** the repo has 14 intentionally TDD-red evidence-write tests (`tests/generate/evidence_extraction/test_write_api.py`, `tests/mcp/test_server.py` `write_evidence` cases) that fail with "write_evidence API is not implemented yet" — they are the user's separate evidence-extraction track, out of scope here. The seam's gate is therefore: **the 120 tests that passed before the seam still pass, the seam introduces no new failures, and the 14 evidence-write tests remain in exactly their prior red state** (do not "fix" them, do not break them further). Coverage stays 100% because those placeholder lines are executed by the failing tests.

- [ ] **Step 2: If coverage is below 100%, close the gap**

Inspect `uv run coverage report --show-missing`. Likely gaps and their owning tests:
- `agent.py` value-type/protocol lines — covered by import via `codex_runner`/tests; protocol `...` auto-excluded.
- `cmds/generate.py::build_generation_workflow` — covered by `test_build_generation_workflow_wires_one_shared_factory`.
- `workflow.py::_run_plan`/`_run_task` and the `async with self.agent_factory` scope — covered by `test_workflow.py` (fake factory) and the e2e tests.
- phase runner `run()` raising — covered by `test_standalone_phase_placeholders_fail_explicitly`.

Add a targeted test for any remaining uncovered line and re-run Step 1.

---

## Self-Review

**Spec coverage:** port (`agent.py`) → Task 1; adapter (`CodexAgentSessionFactory`) → Task 2; fake → Task 3; required phase-runner field + dropped free functions → Task 4; required workflow factory + scope + composition root + entry-point test rewrites → Task 5; proof tests → Task 6; doc updates → Task 7; 100% coverage + build → Task 8. All spec sections map to a task.

**Type consistency:** `AgentRunner.turn(prompt, *, timeout_seconds=600.0, output_schema=None) -> AgentTurnResult` and `AgentSessionFactory.{__aenter__,__aexit__,runner}` are used identically across `agent.py`, `codex_runner.py`, `agent_fakes.py`, the phase runners, `workflow.py`, and the proof tests. `build_generation_workflow` returns `GenerateWorkspaceWorkflow`; phase runners expose `agent_factory`; the workflow exposes `phase_runners` and `agent_factory`.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; mechanical test edits enumerate the exact old→new substitution per test.
