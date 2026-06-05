"""Render and publish a workspace report to Notion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_diary.config import resolve_notion_credentials, resolve_notion_reporter
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.notion_client_adapter import build_notion_client
from prompt_diary.generate.daily_synthesis.notion_publish import publish_workspace_report
from prompt_diary.generate.daily_synthesis.render_notion import render_notion_artifact
from prompt_diary.secret import REDACTED

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from prompt_diary.generate.daily_synthesis.notion_publish import NotionClientProtocol
    from prompt_diary.secret import Secret

__all__ = ["NotionRenderResult", "render_workspace_report_to_notion"]

_DAILY_REPORT_NAME = "daily-report.json"
_NOTION_REPORT_NAME = "report.notion.json"


@dataclass(frozen=True)
class NotionRenderResult:
    """Rendered Notion artifact and the newly-created Notion page location."""

    artifact_path: Path
    page_id: str
    url: str


def render_workspace_report_to_notion(
    workspace_path: Path,
    *,
    client_factory: Callable[..., NotionClientProtocol] = build_notion_client,
    credentials: tuple[Secret, str] | None = None,
) -> NotionRenderResult:
    """Render ``report.notion.json`` from ``daily-report.json`` and publish a new Notion row."""
    daily_report_path = workspace_path / _DAILY_REPORT_NAME
    if not daily_report_path.exists():
        raise PromptDiaryError(_missing_daily_report_message(daily_report_path))

    output_path = workspace_path / _NOTION_REPORT_NAME
    try:
        output_path.unlink(missing_ok=True)
        render_notion_artifact(workspace_path=workspace_path)
    except PromptDiaryError:
        raise
    except Exception as exc:
        raise PromptDiaryError(_render_failed_message(exc)) from exc
    if not output_path.exists():
        raise PromptDiaryError(_missing_output_message(output_path))

    secret, database_id = credentials if credentials is not None else resolve_notion_credentials()
    # The reporter is a cosmetic display name, not a publish target, so it is resolved here at
    # publish time rather than frozen before the pipeline like the credentials.
    reporter = resolve_notion_reporter()
    try:
        client = client_factory(token=secret.reveal())
        publish_result = publish_workspace_report(
            workspace_path=workspace_path,
            client=client,
            database_id=database_id,
            reporter=reporter,
        )
    except Exception as exc:  # noqa: BLE001 - a security boundary: scrub the token from ANY failure
        # The token is in scope here, so scrub it from the surfaced message — structured errors and
        # unexpected SDK failures alike. Capture the redacted text and raise *below*, outside this
        # handler, rather than `from exc`: that keeps the raw, possibly token-bearing cause out of
        # the new error's __cause__/__context__, where a traceback or an exc_info logger could
        # otherwise surface it. (The token itself stays wrapped in ``secret`` — never a bare str
        # local — so even a locals-capturing traceback renders it as ``***``. The render step above
        # has no token in scope, so it needs none.)
        redacted = _redact(
            str(exc) if isinstance(exc, PromptDiaryError) else _publish_failed_message(exc),
            secret.reveal(),
        )
    else:
        return NotionRenderResult(
            artifact_path=output_path,
            page_id=publish_result.page_id,
            url=publish_result.url,
        )
    raise PromptDiaryError(redacted)


def _missing_daily_report_message(path: Path) -> str:
    return f"daily report artifact is missing: {path}; run report generate daily first"


def _missing_output_message(path: Path) -> str:
    return f"Notion report payload was not rendered at {path}; refusing to publish"


def _render_failed_message(cause: object) -> str:
    return f"failed to render the report to Notion: {cause}"


def _publish_failed_message(cause: object) -> str:
    return f"failed to publish the report to Notion: {cause}"


def _redact(text: str, secret: str) -> str:
    """Scrub ``secret`` (the integration token) from a user-facing message, if present."""
    return text.replace(secret, REDACTED) if secret else text
