from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

import prompt_diary.render.notion as render_api
from prompt_diary.config import ReporterTarget, StoredConfig, save_config
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.notion_publish import PublishResult
from prompt_diary.progress.events import PhaseFinished, PhaseStarted
from tests.support.daily_synthesis import (
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    fill_synthesize_slots,
    finalize_daily_report_via_api,
)
from tests.support.progress import RecordingReporter

if TYPE_CHECKING:
    from pathlib import Path

_RENDER_SHOULD_NOT_RUN = "render should not run without daily-report.json"
_CLIENT_SHOULD_NOT_RUN_WITHOUT_DAILY = "client should not be built without daily-report.json"
_CLIENT_SHOULD_NOT_RUN_WITHOUT_OUTPUT = (
    "client should not be built when rendering produced no artifact"
)
_PARTIAL_ROW = "a partial row may exist"
_LOW_LEVEL_FAILURE = "low-level failure"
_RENDER_STRUCTURED = "structured render failure"
_RENDER_UNEXPECTED = "unexpected render failure"
_TOKEN_IN_ERROR = "HTTP 401 rejected token-from-env"  # an SDK message that echoes the token


@dataclass
class _FakeNotionClient:
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def retrieve_database(self, *, database_id: str) -> dict[str, Any]:
        self.calls.append(("retrieve", database_id))
        return {
            "properties": {
                "Name": {"type": "title"},
                "Date": {"type": "date"},
            }
        }

    def create_page(self, *, parent: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create", parent, properties))
        return {"id": "page-1", "url": "https://notion.so/page-x"}

    def append_children(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(("append", block_id, children))
        return {"results": [{"id": f"block-{index}"} for index, _ in enumerate(children)]}


def _complete_daily_workspace(tmp_path: Path) -> Path:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    finalize_daily_report_via_api(workspace)
    return workspace


def _configure_notion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROMPT_DIARY_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("NOTION_API_KEY", "token-from-env")
    monkeypatch.setenv("NOTION_PAGE_ID", "database-from-env")


def test_render_workspace_report_to_notion_requires_daily_report_before_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PROMPT_DIARY_CONFIG", str(tmp_path / "missing-config.json"))

    def render_must_not_run(*, workspace_path: Path) -> Path:
        del workspace_path
        raise AssertionError(_RENDER_SHOULD_NOT_RUN)

    def factory_must_not_run(*, token: str) -> _FakeNotionClient:
        del token
        raise AssertionError(_CLIENT_SHOULD_NOT_RUN_WITHOUT_DAILY)

    monkeypatch.setattr(render_api, "render_notion_artifact", render_must_not_run)

    with pytest.raises(PromptDiaryError, match=r"daily-report\.json"):
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory_must_not_run)


def test_render_workspace_report_to_notion_regenerates_payload_before_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    stale = workspace / "report.notion.json"
    stale.write_text('{"title": "stale"}\n', encoding="utf-8")
    _configure_notion(monkeypatch, tmp_path)
    client = _FakeNotionClient()

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return client

    result = render_api.render_workspace_report_to_notion(
        workspace,
        client_factory=factory,
    )

    create = next(call for call in client.calls if call[0] == "create")
    title = create[2]["Name"]["title"][0]["text"]["content"]
    assert title == "Prompt Diary Report — 2026-05-28"
    assert stale.read_text(encoding="utf-8") != '{"title": "stale"}\n'
    assert result.artifact_path == stale
    assert result.page_id == "page-1"
    assert result.url == "https://notion.so/page-x"


def test_render_workspace_report_to_notion_reports_rendering_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)
    reporter = RecordingReporter()

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    render_api.render_workspace_report_to_notion(
        workspace,
        client_factory=factory,
        progress_reporter=reporter,
    )

    rendering_events = [
        event
        for event in reporter.events
        if isinstance(event, PhaseStarted | PhaseFinished) and event.phase_id == "rendering"
    ]
    assert [type(event).__name__ for event in rendering_events] == [
        "PhaseStarted",
        "PhaseFinished",
    ]


def test_render_workspace_report_to_notion_requires_rendered_output_before_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)

    def fake_render(*, workspace_path: Path) -> Path:
        return workspace_path / "report.notion.json"

    def factory_must_not_run(*, token: str) -> _FakeNotionClient:
        del token
        raise AssertionError(_CLIENT_SHOULD_NOT_RUN_WITHOUT_OUTPUT)

    monkeypatch.setattr(render_api, "render_notion_artifact", fake_render)

    with pytest.raises(PromptDiaryError, match=r"report\.notion\.json"):
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory_must_not_run)


def test_render_workspace_report_to_notion_does_not_publish_stale_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    stale = workspace / "report.notion.json"
    stale.write_text('{"title": "stale"}\n', encoding="utf-8")
    _configure_notion(monkeypatch, tmp_path)

    def fake_render(*, workspace_path: Path) -> Path:
        return workspace_path / "report.notion.json"

    def factory_must_not_run(*, token: str) -> _FakeNotionClient:
        del token
        raise AssertionError(_CLIENT_SHOULD_NOT_RUN_WITHOUT_OUTPUT)

    monkeypatch.setattr(render_api, "render_notion_artifact", fake_render)

    with pytest.raises(PromptDiaryError, match=r"report\.notion\.json"):
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory_must_not_run)
    assert not stale.exists()


def test_render_workspace_report_to_notion_passes_through_structured_render_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)

    def raise_structured(*, workspace_path: Path) -> Path:
        del workspace_path
        raise PromptDiaryError(_RENDER_STRUCTURED)

    def factory_must_not_run(*, token: str) -> _FakeNotionClient:
        del token
        raise AssertionError(_CLIENT_SHOULD_NOT_RUN_WITHOUT_OUTPUT)

    monkeypatch.setattr(render_api, "render_notion_artifact", raise_structured)

    with pytest.raises(PromptDiaryError, match=_RENDER_STRUCTURED):
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory_must_not_run)


def test_render_workspace_report_to_notion_wraps_unexpected_render_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)

    def raise_unexpected(*, workspace_path: Path) -> Path:
        del workspace_path
        raise ValueError(_RENDER_UNEXPECTED)

    def factory_must_not_run(*, token: str) -> _FakeNotionClient:
        del token
        raise AssertionError(_CLIENT_SHOULD_NOT_RUN_WITHOUT_OUTPUT)

    monkeypatch.setattr(render_api, "render_notion_artifact", raise_unexpected)

    with pytest.raises(PromptDiaryError, match="failed to render the report to Notion"):
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory_must_not_run)


def test_render_workspace_report_to_notion_uses_configured_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)
    client = _FakeNotionClient()
    tokens: list[str] = []

    def factory(*, token: str) -> _FakeNotionClient:
        tokens.append(token)
        return client

    render_api.render_workspace_report_to_notion(workspace, client_factory=factory)

    assert tokens == ["token-from-env"]
    assert ("retrieve", "database-from-env") in client.calls


def test_render_workspace_report_to_notion_passes_through_structured_publish_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)

    def raise_structured(
        *, workspace_path: Path, client: object, database_id: str, reporter: object
    ) -> object:
        del workspace_path, client, database_id, reporter
        raise PromptDiaryError(_PARTIAL_ROW)

    monkeypatch.setattr(render_api, "publish_workspace_report", raise_structured)

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    with pytest.raises(PromptDiaryError, match=_PARTIAL_ROW):
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory)


def test_render_workspace_report_to_notion_wraps_unexpected_publish_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)

    def raise_unexpected(
        *, workspace_path: Path, client: object, database_id: str, reporter: object
    ) -> object:
        del workspace_path, client, database_id, reporter
        raise ValueError(_LOW_LEVEL_FAILURE)

    monkeypatch.setattr(render_api, "publish_workspace_report", raise_unexpected)

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    with pytest.raises(PromptDiaryError, match="failed to publish the report to Notion"):
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory)


def test_render_workspace_report_to_notion_passes_the_configured_reporter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)
    save_config(StoredConfig(notion_reporter="Wei Hu"))
    captured: dict[str, object] = {}

    def capture(
        *, workspace_path: Path, client: object, database_id: str, reporter: object
    ) -> PublishResult:
        del workspace_path, client, database_id
        captured["reporter"] = reporter
        return PublishResult(page_id="page-1", url="https://notion.so/x")

    monkeypatch.setattr(render_api, "publish_workspace_report", capture)

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    render_api.render_workspace_report_to_notion(workspace, client_factory=factory)

    # The reporter resolves from config (name + default column) and is handed to the publisher.
    assert captured["reporter"] == ReporterTarget(column="汇报人", name="Wei Hu")


def test_render_workspace_report_to_notion_surfaces_publish_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)

    def warn(
        *, workspace_path: Path, client: object, database_id: str, reporter: object
    ) -> PublishResult:
        del workspace_path, client, database_id, reporter
        return PublishResult(
            page_id="page-1", url="https://notion.so/x", warnings=("汇报人 was left empty",)
        )

    monkeypatch.setattr(render_api, "publish_workspace_report", warn)

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    result = render_api.render_workspace_report_to_notion(workspace, client_factory=factory)

    # A publish warning (e.g. the reporter column could not be filled) reaches the caller to echo.
    assert result.warnings == ("汇报人 was left empty",)


def test_render_workspace_report_to_notion_redacts_the_token_from_publish_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)  # token is "token-from-env"

    def raise_with_token(
        *, workspace_path: Path, client: object, database_id: str, reporter: object
    ) -> object:
        del workspace_path, client, database_id, reporter
        raise RuntimeError(_TOKEN_IN_ERROR)

    monkeypatch.setattr(render_api, "publish_workspace_report", raise_with_token)

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    with pytest.raises(PromptDiaryError) as exc_info:
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory)
    error = exc_info.value
    assert "token-from-env" not in str(error)  # the token is scrubbed from the surfaced error
    assert "***" in str(error)
    # The raw, token-bearing cause must not survive in the chain either: a traceback or an exc_info
    # logger walks __cause__/__context__, so both must be clear of the token.
    assert error.__cause__ is None
    assert error.__context__ is None


def test_render_workspace_report_to_notion_redacts_the_token_from_structured_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)  # token is "token-from-env"

    def raise_structured_with_token(
        *, workspace_path: Path, client: object, database_id: str, reporter: object
    ) -> object:
        del workspace_path, client, database_id, reporter
        raise PromptDiaryError(_TOKEN_IN_ERROR)  # even our own structured errors are scrubbed

    monkeypatch.setattr(render_api, "publish_workspace_report", raise_structured_with_token)

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    with pytest.raises(PromptDiaryError) as exc_info:
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory)
    error = exc_info.value
    assert "token-from-env" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_render_workspace_report_to_notion_keeps_the_token_out_of_traceback_locals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _complete_daily_workspace(tmp_path)
    _configure_notion(monkeypatch, tmp_path)  # token is "token-from-env"

    def raise_with_token(
        *, workspace_path: Path, client: object, database_id: str, reporter: object
    ) -> object:
        del workspace_path, client, database_id, reporter
        raise RuntimeError(_TOKEN_IN_ERROR)

    monkeypatch.setattr(render_api, "publish_workspace_report", raise_with_token)

    def factory(*, token: str) -> _FakeNotionClient:
        del token
        return _FakeNotionClient()

    with pytest.raises(PromptDiaryError) as exc_info:
        render_api.render_workspace_report_to_notion(workspace, client_factory=factory)
    error = exc_info.value
    # A locals-capturing traceback renderer (some observability tools) must not surface the token:
    # it stays wrapped in a Secret throughout the publish frame, so locals render it as ``***``.
    rendered = "".join(
        traceback.TracebackException(
            type(error), error, error.__traceback__, capture_locals=True
        ).format()
    )
    assert "token-from-env" not in rendered
