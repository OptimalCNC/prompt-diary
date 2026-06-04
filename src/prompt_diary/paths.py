"""The per-user platform data directory for report workspaces.

The reports root — where ``prepare``/``generate`` read and write workspaces — is resolved in
:mod:`prompt_diary.config` from an explicit flag, the ``PROMPT_DIARY_HOME`` env var, the stored
config, and, as the built-in default, the per-user data directory returned here.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs

from prompt_diary.errors import PromptDiaryError

REPORTS_HOME_ENV = "PROMPT_DIARY_HOME"


def _relative_data_dir_message(data_dir: Path) -> str:
    return (
        f"the per-user data directory resolved to a relative path ({data_dir}); "
        f"set {REPORTS_HOME_ENV} or XDG_DATA_HOME to an absolute path."
    )


def platform_data_dir() -> Path:
    """Return the absolute per-user data directory for report workspaces.

    This is the built-in default reports root. It must be absolute, so a misconfigured relative
    ``XDG_DATA_HOME`` fails loud rather than silently scattering workspaces under the current
    directory. (An explicit ``--reports-root``/``PROMPT_DIARY_HOME``/config value may be relative —
    that is an opt-in cwd-relative root, handled by the resolver in :mod:`prompt_diary.config`.)
    """
    data_dir = Path(platformdirs.user_data_dir("prompt-diary", appauthor=False)).expanduser()
    if not data_dir.is_absolute():
        raise PromptDiaryError(_relative_data_dir_message(data_dir))
    return data_dir
