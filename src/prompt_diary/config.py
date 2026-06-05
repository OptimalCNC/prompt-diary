"""Persistent configuration store and setting resolution.

Settings are persisted in a single JSON file under the per-user config directory (overridable with
``PROMPT_DIARY_CONFIG``). This module owns reading and writing that file and resolving each setting
from all layers: an explicit CLI value, then the environment, then the stored config, then the
built-in default. The Notion token lives in the same file, which is written ``0600``; it is read
only from the environment or that file, never logged.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import msgspec
import platformdirs

from prompt_diary import paths
from prompt_diary.errors import PromptDiaryError

CONFIG_PATH_ENV = "PROMPT_DIARY_CONFIG"
NOTION_TOKEN_ENV = "NOTION_API_KEY"  # noqa: S105 - env var name to read, not a credential
NOTION_DATABASE_ENV = "NOTION_PAGE_ID"

_CONFIG_FILE_MODE = 0o600


class StoredConfig(msgspec.Struct, omit_defaults=True):
    """User configuration persisted to disk. A new integration adds a field here."""

    reports_root: str | None = None
    notion_api_key: str | None = None
    notion_page_id: str | None = None


_CONFIG_DECODER = msgspec.json.Decoder(StoredConfig)


def _env(name: str) -> str | None:
    """Return a stripped, non-empty environment value, or ``None`` when unset or blank."""
    value = os.environ.get(name)
    if value and (stripped := value.strip()):
        return stripped
    return None


def config_path() -> Path:
    """Return the config file path: ``$PROMPT_DIARY_CONFIG`` if set, else the user config dir."""
    override = _env(CONFIG_PATH_ENV)
    if override is not None:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir("prompt-diary", appauthor=False)) / "config.json"


def load_config() -> StoredConfig:
    """Load the stored config, returning an empty config when the file is absent."""
    path = config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return StoredConfig()
    try:
        return _CONFIG_DECODER.decode(raw)
    except msgspec.MsgspecError as exc:
        raise PromptDiaryError(_corrupt_config_message(path, exc)) from exc


def save_config(config: StoredConfig) -> Path:
    """Write the config atomically with ``0600`` permissions and return its path."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = msgspec.json.encode(config)
    # Write to a 0600 temp file (mkstemp creates it owner-only), then atomically replace: the token
    # is never written to a looser-permissioned inode, and a crash cannot leave a partial config.
    descriptor, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.chmod(_CONFIG_FILE_MODE)
        tmp_path.replace(path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise PromptDiaryError(_save_failed_message(path, exc)) from exc
    return path


def resolve_reports_root(explicit: Path | None) -> Path:
    """Resolve the reports root: explicit flag, then env, then config, then platform data dir."""
    if explicit is not None:
        return explicit.expanduser()
    env = _env(paths.REPORTS_HOME_ENV)
    if env is not None:
        return Path(env).expanduser()
    stored = load_config().reports_root
    if stored:
        return Path(stored).expanduser()
    return paths.platform_data_dir()


def notion_is_configured() -> bool:
    """Return whether both a Notion token and database id resolve (from env or stored config)."""
    token, database_id = _notion_credentials()
    return bool(token and database_id)


def resolve_notion_credentials() -> tuple[str, str]:
    """Return the Notion ``(token, database_id)`` from env, then config; raise if missing."""
    token, database_id = _notion_credentials()
    if not token or not database_id:
        raise PromptDiaryError(_missing_notion_credentials_message())
    return token, database_id


def _notion_credentials() -> tuple[str | None, str | None]:
    """Resolve the Notion token and database id (env, then config); either may be None."""
    config = load_config()
    token = _env(NOTION_TOKEN_ENV) or config.notion_api_key
    database_id = _env(NOTION_DATABASE_ENV) or config.notion_page_id
    return token, database_id


def _corrupt_config_message(path: Path, exc: msgspec.MsgspecError) -> str:
    return f"the config file at {path} is invalid ({exc}); fix or remove it."


def _save_failed_message(path: Path, exc: OSError) -> str:
    return f"failed to write the config file at {path}: {exc}"


def _missing_notion_credentials_message() -> str:
    return (
        f"no Notion credentials configured; set {NOTION_TOKEN_ENV} (integration token) and "
        f"{NOTION_DATABASE_ENV} (database id), or store them in {config_path()}."
    )
