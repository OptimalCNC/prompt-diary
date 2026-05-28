from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from prompt_diary.api import generate_prompt_diary
from prompt_diary.errors import PromptDiaryError, ReportValidationError, ReportWriterError
from prompt_diary.generate.report import write_empty_fallback_report
from prompt_diary.models import SourceSpec

if TYPE_CHECKING:
    from pathlib import Path


GENERATED_NOW = datetime(2026, 5, 13, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


@dataclass
class CapturingWriter:
    workspace_path: Path | None = None
    prompt: str | None = None

    def write_report(self, *, workspace_path: Path, prompt: str) -> Path:
        self.workspace_path = workspace_path
        self.prompt = prompt
        return write_empty_fallback_report(workspace_path)


@dataclass
class WrongPathWriter:
    def write_report(self, *, workspace_path: Path, prompt: str) -> Path:
        del prompt
        wrong_path = workspace_path / "elsewhere.md"
        wrong_path.write_text("not the contract path", encoding="utf-8")
        return wrong_path


@dataclass
class InvalidReportWriter:
    def write_report(self, *, workspace_path: Path, prompt: str) -> Path:
        del prompt
        report_path = workspace_path / "report.md"
        report_path.write_text("# Invalid\n", encoding="utf-8")
        return report_path


@dataclass
class AbsoluteReportPathWriter:
    def write_report(self, *, workspace_path: Path, prompt: str) -> Path:
        del prompt
        return write_empty_fallback_report(workspace_path).resolve()


def test_generate_executes_injected_writer_in_workspace_and_validates(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")
    writer = CapturingWriter()

    result = generate_prompt_diary(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        reports_root=reports_root,
        now=GENERATED_NOW,
        report_writer=writer,
    )

    assert result.validation.ok
    assert writer.workspace_path == workspace
    assert writer.prompt is not None
    assert "prepared evidence boundary" in writer.prompt
    assert any("Reusing existing workspace" in message for message in result.messages)


def test_generate_prepares_missing_workspace_before_injected_writer(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / ".reports"
    writer = CapturingWriter()

    result = generate_prompt_diary(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        reports_root=reports_root,
        source_specs=(),
        now=GENERATED_NOW,
        report_writer=writer,
    )

    assert result.validation.ok
    assert result.workspace_path == reports_root / "work" / "2026-05-12"
    assert writer.workspace_path == result.workspace_path
    assert any(message.startswith("Prepared workspace") for message in result.messages)


def test_generate_without_writer_raises_actionable_error(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(ReportWriterError, match="PROMPT_DIARY_REPORT_WRITER_COMMAND"):
        generate_prompt_diary(
            date="2026-05-12",
            today=False,
            timezone_name="Asia/Shanghai",
            reports_root=reports_root,
            now=GENERATED_NOW,
        )


def test_generate_rejects_existing_workspace_for_different_target(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(PromptDiaryError, match="prepare --date 2026-05-12 --timezone UTC --force"):
        generate_prompt_diary(
            date="2026-05-12",
            today=False,
            timezone_name="UTC",
            reports_root=reports_root,
            source_specs=(SourceSpec(source="codex", root=tmp_path / "sessions"),),
            now=datetime(2026, 5, 13, 1, 0, tzinfo=ZoneInfo("UTC")),
            report_writer=CapturingWriter(),
        )


def test_generate_rejects_writer_returning_wrong_path(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(ReportValidationError, match="Report writer returned"):
        generate_prompt_diary(
            date="2026-05-12",
            today=False,
            timezone_name="Asia/Shanghai",
            reports_root=reports_root,
            now=GENERATED_NOW,
            report_writer=WrongPathWriter(),
        )


def test_generate_accepts_writer_returning_resolved_report_path(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    result = generate_prompt_diary(
        date="2026-05-12",
        today=False,
        timezone_name="Asia/Shanghai",
        reports_root=reports_root,
        now=GENERATED_NOW,
        report_writer=AbsoluteReportPathWriter(),
    )

    assert result.validation.ok
    assert result.report_path == workspace / "report.md"


def test_generate_rejects_invalid_report_with_validation_details(tmp_path: Path) -> None:
    reports_root = tmp_path / ".reports"
    workspace = reports_root / "work" / "2026-05-12"
    _write_workspace_metadata(workspace, timezone_name="Asia/Shanghai")

    with pytest.raises(ReportValidationError) as exc_info:
        generate_prompt_diary(
            date="2026-05-12",
            today=False,
            timezone_name="Asia/Shanghai",
            reports_root=reports_root,
            now=GENERATED_NOW,
            report_writer=InvalidReportWriter(),
        )

    message = str(exc_info.value)
    assert "Report validation failed:" in message
    assert "- report header must start" in message


def _write_workspace_metadata(workspace: Path, *, timezone_name: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    if timezone_name == "Asia/Shanghai":
        local_start = "2026-05-12T00:00:00+08:00"
        local_end = "2026-05-13T00:00:00+08:00"
        utc_start = "2026-05-11T16:00:00Z"
        utc_end = "2026-05-12T16:00:00Z"
    else:
        local_start = "2026-05-12T00:00:00+00:00"
        local_end = "2026-05-13T00:00:00+00:00"
        utc_start = "2026-05-12T00:00:00Z"
        utc_end = "2026-05-13T00:00:00Z"
    (workspace / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "report_date": "2026-05-12",
                "timezone": timezone_name,
                "status": "final",
                "prepared_at": "2026-05-13T09:00:00+08:00",
                "report_window_local": {
                    "start": local_start,
                    "end": local_end,
                },
                "report_window_utc": {
                    "start": utc_start,
                    "end": utc_end,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "projects").mkdir(exist_ok=True)
