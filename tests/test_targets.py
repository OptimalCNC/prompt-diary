from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import prompt_diary.targets as targets_module
from prompt_diary.errors import PromptDiaryError
from prompt_diary.models import serialize_datetime
from prompt_diary.targets import resolve_report_target


def test_resolve_report_target_defaults_to_yesterday_completed_local_day() -> None:
    target = resolve_report_target(
        date=None,
        today=False,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert target.report_date.isoformat() == "2026-05-19"
    assert target.status == "final"
    assert serialize_datetime(target.report_window_local.start) == "2026-05-19T00:00:00+08:00"
    assert serialize_datetime(target.report_window_utc.start) == "2026-05-18T16:00:00Z"
    assert serialize_datetime(target.report_window_utc.end) == "2026-05-19T16:00:00Z"


def test_resolve_report_target_today_is_partial() -> None:
    target = resolve_report_target(
        date=None,
        today=True,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 5, 20, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert target.report_date.isoformat() == "2026-05-20"
    assert target.status == "partial"


def test_resolve_report_target_uses_environment_timezone_default() -> None:
    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={"PROMPT_DIARY_TIMEZONE": "Asia/Shanghai"},
    )

    assert target.timezone == "Asia/Shanghai"
    assert serialize_datetime(target.report_window_utc.start) == "2026-05-18T16:00:00Z"


def test_resolve_report_target_rejects_mutually_exclusive_date_flags() -> None:
    with pytest.raises(PromptDiaryError, match="mutually exclusive"):
        resolve_report_target(
            date="2026-05-19",
            today=True,
            timezone_name="UTC",
            now=datetime(2026, 5, 20, tzinfo=ZoneInfo("UTC")),
        )


def test_resolve_report_target_rejects_future_dates() -> None:
    with pytest.raises(PromptDiaryError, match="Future report dates"):
        resolve_report_target(
            date="2026-05-21",
            today=False,
            timezone_name="UTC",
            now=datetime(2026, 5, 20, tzinfo=ZoneInfo("UTC")),
        )


def test_resolve_report_target_rejects_invalid_timezone_and_date() -> None:
    with pytest.raises(PromptDiaryError, match="Unknown IANA timezone"):
        resolve_report_target(
            date="2026-05-19",
            today=False,
            timezone_name="Not/AZone",
            now=datetime(2026, 5, 20, tzinfo=ZoneInfo("UTC")),
        )

    with pytest.raises(PromptDiaryError, match="Invalid --date value"):
        resolve_report_target(
            date="05/19/2026",
            today=False,
            timezone_name="UTC",
            now=datetime(2026, 5, 20, tzinfo=ZoneInfo("UTC")),
        )


def test_resolve_report_target_converts_aware_now_to_target_timezone() -> None:
    target = resolve_report_target(
        date=None,
        today=True,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 5, 19, 17, 0, tzinfo=ZoneInfo("UTC")),
    )

    assert target.report_date.isoformat() == "2026-05-20"
    assert target.status == "partial"


def test_resolve_report_target_uses_tz_env_after_blank_primary() -> None:
    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={"PROMPT_DIARY_TIMEZONE": " ", "TZ": "Asia/Shanghai"},
    )

    assert target.timezone == "Asia/Shanghai"


def test_resolve_report_target_uses_system_timezone_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(targets_module, "_system_timezone_name", lambda: "Asia/Shanghai")

    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={"PROMPT_DIARY_TIMEZONE": ":posix/UTC", "TZ": ""},
    )

    assert target.timezone == "Asia/Shanghai"


def test_resolve_report_target_defaults_to_utc_without_env_or_system_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(targets_module, "_system_timezone_name", lambda: None)

    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={},
    )

    assert target.timezone == "UTC"


def test_system_timezone_name_reads_timezone_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _FakeEtcPaths(timezone_text="Asia/Shanghai\n", localtime_target=None)
    monkeypatch.setattr(targets_module, "Path", fake_paths.path_factory)

    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={},
    )

    assert target.timezone == "Asia/Shanghai"


def test_system_timezone_name_reads_localtime_symlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _FakeEtcPaths(
        timezone_text=None,
        localtime_target=Path("/usr/share/zoneinfo/Asia/Shanghai"),
    )
    monkeypatch.setattr(targets_module, "Path", fake_paths.path_factory)

    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={},
    )

    assert target.timezone == "Asia/Shanghai"


def test_system_timezone_name_ignores_unknown_or_unusable_system_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _FakeEtcPaths(
        timezone_text="Not/AZone\n",
        localtime_target=Path("/usr/share/not-zoneinfo/UTC"),
    )
    monkeypatch.setattr(targets_module, "Path", fake_paths.path_factory)

    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={},
    )

    assert target.timezone == "UTC"


def test_system_timezone_name_returns_none_without_localtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _FakeEtcPaths(timezone_text=None, localtime_target=None)
    monkeypatch.setattr(targets_module, "Path", fake_paths.path_factory)

    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={},
    )

    assert target.timezone == "UTC"


def test_system_timezone_name_rejects_unknown_zoneinfo_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _FakeEtcPaths(
        timezone_text=None,
        localtime_target=Path("/usr/share/zoneinfo/Not/AZone"),
    )
    monkeypatch.setattr(targets_module, "Path", fake_paths.path_factory)

    target = resolve_report_target(
        date="2026-05-19",
        today=False,
        timezone_name=None,
        now=datetime(2026, 5, 20, 10, 30, tzinfo=ZoneInfo("UTC")),
        env={},
    )

    assert target.timezone == "UTC"


class _FakeEtcPaths:
    def __init__(self, *, timezone_text: str | None, localtime_target: Path | None) -> None:
        self._timezone_text = timezone_text
        self._localtime_target = localtime_target

    def path_factory(self, value: str) -> _FakeEtcPath:
        return _FakeEtcPath(value, owner=self)

    def exists(self, value: str) -> bool:
        if value == "/etc/timezone":
            return self._timezone_text is not None
        if value == "/etc/localtime":
            return self._localtime_target is not None
        return False

    def read_text(self, value: str) -> str:
        if value != "/etc/timezone" or self._timezone_text is None:
            raise AssertionError(value)
        return self._timezone_text

    def resolve(self, value: str) -> Path:
        if value != "/etc/localtime" or self._localtime_target is None:
            raise AssertionError(value)
        return self._localtime_target


class _FakeEtcPath:
    def __init__(self, value: str, *, owner: _FakeEtcPaths) -> None:
        self._value = value
        self._owner = owner

    @property
    def parts(self) -> tuple[str, ...]:
        return Path(self._value).parts

    def exists(self) -> bool:
        return self._owner.exists(self._value)

    def read_text(self, *, encoding: str) -> str:
        assert encoding == "utf-8"
        return self._owner.read_text(self._value)

    def resolve(self) -> Path:
        return self._owner.resolve(self._value)
