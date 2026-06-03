"""Resolution of the reports root directory.

The reports root is where ``prepare``/``generate`` read and write report workspaces. It is
resolved once at each CLI boundary; everything downstream receives an explicit ``Path``.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

from prompt_diary.errors import PromptDiaryError

REPORTS_HOME_ENV = "PROMPT_DIARY_HOME"


def _relative_data_dir_message(data_dir: Path) -> str:
    return (
        f"the per-user data directory resolved to a relative path ({data_dir}); "
        f"set {REPORTS_HOME_ENV} or XDG_DATA_HOME to an absolute path."
    )


def default_reports_root() -> Path:
    """Return the reports root from ``PROMPT_DIARY_HOME``, else the per-user data dir.

    An explicit ``PROMPT_DIARY_HOME`` may be relative (an opt-in cwd-relative root). The
    platform default must be absolute, so a misconfigured relative ``XDG_DATA_HOME`` fails
    loud rather than silently scattering workspaces under the current directory.
    """
    override = os.environ.get(REPORTS_HOME_ENV)
    if override and (stripped := override.strip()):
        return Path(stripped).expanduser()
    data_dir = Path(platformdirs.user_data_dir("prompt-diary", appauthor=False)).expanduser()
    if not data_dir.is_absolute():
        raise PromptDiaryError(_relative_data_dir_message(data_dir))
    return data_dir


def resolve_reports_root(explicit: Path | None) -> Path:
    """Resolve the reports root: an explicit ``--reports-root`` wins, else env/default."""
    if explicit is not None:
        return explicit.expanduser()
    return default_reports_root()
