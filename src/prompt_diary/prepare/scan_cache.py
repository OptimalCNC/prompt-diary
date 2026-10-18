"""Reuse content-derived timestamp bounds for unchanged source files."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime  # noqa: TC003 - msgspec resolves these annotations at runtime.
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Literal

import msgspec

if TYPE_CHECKING:
    from collections.abc import Callable

    from prompt_diary.models import SourceName, TimeWindow


class TimestampBounds(msgspec.Struct, frozen=True):
    """Inclusive bounds of all valid record timestamps in a scanned file."""

    first: datetime
    last: datetime

    def __post_init__(self) -> None:
        if self.first.tzinfo is None or self.last.tzinfo is None or self.first > self.last:
            message = "Timestamp bounds must be ordered and timezone-aware"
            raise ValueError(message)

    def overlaps(self, window: TimeWindow) -> bool:
        return self.first < window.end and self.last >= window.start


class ProbeResult(msgspec.Struct, frozen=True):
    """A candidate decision, with bounds only when every line could be probed."""

    is_candidate: bool
    timestamps: TimestampBounds | None = None


class _FileStamp(msgspec.Struct, frozen=True):
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int


class _CacheEntry(msgspec.Struct, frozen=True):
    stamp: _FileStamp
    timestamps: TimestampBounds


class _CacheFile(msgspec.Struct):
    version: Literal[1]
    entries: dict[str, _CacheEntry]


class SessionScanCache:
    """Skip unchanged files outside the target window; always probe possible matches."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._stored = msgspec.json.decode(path.read_bytes(), type=_CacheFile).entries
        except (OSError, msgspec.DecodeError):
            self._stored = {}
        self._current: dict[str, _CacheEntry] = {}

    def is_candidate(
        self,
        path: Path,
        source: SourceName,
        window: TimeWindow,
        probe: Callable[[], ProbeResult],
    ) -> bool:
        try:
            before = _file_stamp(path)
        except OSError:
            return probe().is_candidate
        key = f"{source}:{path.absolute()}"
        cached = self._stored.get(key)
        if cached is not None and cached.stamp == before and not cached.timestamps.overlaps(window):
            self._current[key] = cached
            return False
        result = probe()
        if result.timestamps is not None:
            # Do not retain bounds from a file that changed while it was being read.
            with suppress(OSError):
                if before == _file_stamp(path):
                    self._current[key] = _CacheEntry(before, result.timestamps)
        return result.is_candidate

    def save(self) -> None:
        """Atomically replace the disposable cache, pruning sources no longer scanned."""
        if self._current == self._stored:
            return
        temporary: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                dir=self._path.parent, prefix=".session-scan-", delete=False
            ) as f:
                temporary = Path(f.name)
                f.write(msgspec.json.encode(_CacheFile(version=1, entries=self._current)))
            temporary.replace(self._path)
        except OSError:
            # A missing or unwritable cache must not prevent workspace preparation.
            pass
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)


def _file_stamp(path: Path) -> _FileStamp:
    stat = path.stat()
    return _FileStamp(stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino)
