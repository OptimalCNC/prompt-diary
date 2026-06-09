"""Persistent configuration store and setting resolution.

Settings are persisted in a single JSON file under the per-user config directory (overridable with
``PROMPT_DIARY_CONFIG``). This module owns reading and writing that file and resolving each setting
from all layers: an explicit CLI value, then the environment, then the stored config, then the
built-in default. The Notion token lives in the same file, which is written ``0600``; it is read
only from the environment or that file, never logged.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import msgspec
import platformdirs
from msgspec import structs

from prompt_diary import paths
from prompt_diary.errors import PromptDiaryError
from prompt_diary.language import (
    CONTENT_LANGUAGE_ENV,
    LanguageNorm,
    resolve_content_language_setting,
)
from prompt_diary.secret import REDACTED, Secret

CONFIG_PATH_ENV = "PROMPT_DIARY_CONFIG"
NOTION_TOKEN_ENV = "NOTION_API_KEY"  # noqa: S105 - env var name to read, not a credential
NOTION_DATABASE_ENV = "NOTION_PAGE_ID"

_CONFIG_FILE_MODE = 0o600

# Default Notion text column the reporter name is written into. The wizard prompts only for the
# name; this is the write target unless ``notion_reporter_property`` is overridden by hand. A
# literal value is fine here (CJK is not a RUF-flagged confusable).
_DEFAULT_REPORTER_PROPERTY = "汇报人"


class StoredConfig(msgspec.Struct, omit_defaults=True):
    """User configuration persisted to disk. A new integration adds a field here."""

    reports_root: str | None = None
    content_language: str | None = None
    notion_api_key: str | None = None
    notion_page_id: str | None = None
    notion_reporter: str | None = None
    notion_reporter_property: str | None = None

    def __repr__(self) -> str:
        # Redact the token in the *repr* so a loaded config can be logged or captured as a frame
        # local (e.g. by a locals-capturing traceback renderer) without surfacing the stored secret;
        # only this one field is sensitive. Built from asdict so new fields render automatically — a
        # future *secret* field is the only thing that would need adding to the redaction set below.
        fields = structs.asdict(self)
        if fields["notion_api_key"] is not None:
            fields["notion_api_key"] = REDACTED
        rendered = ", ".join(f"{name}={value!r}" for name, value in fields.items())
        return f"{type(self).__name__}({rendered})"


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
        decoded = _decode_config(path.read_bytes())
    except FileNotFoundError:
        return StoredConfig()
    if decoded is None:
        raise PromptDiaryError(_corrupt_config_message(path))
    return decoded


def _decode_config(raw: bytes) -> StoredConfig | None:
    """Decode config bytes, returning ``None`` when malformed (never raising).

    Returning instead of raising keeps the token-bearing ``raw`` (and the decode error, which can
    quote the input) out of every frame that survives a failure: ``load_config`` raises from a frame
    holding only ``path``, so a locals-capturing traceback never sees the stored token.
    """
    try:
        return _CONFIG_DECODER.decode(raw)
    except msgspec.MsgspecError:
        return None


def save_config(config: StoredConfig) -> Path:
    """Write the config atomically with ``0600`` permissions and return its path."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a 0600 temp file (mkstemp creates it owner-only), then atomically replace: the token
    # is never written to a looser-permissioned inode, and a crash cannot leave a partial config.
    descriptor, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            # Encode inline so the token-bearing JSON bytes stay an anonymous argument to write(),
            # never a named local that a locals-capturing traceback could surface if a later step
            # fails. (The only surviving local, the StoredConfig, has a redacted repr, so even an
            # mkstemp failure outside this try leaks nothing.)
            handle.write(msgspec.json.encode(config))
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.chmod(_CONFIG_FILE_MODE)
        tmp_path.replace(path)
    except OSError as exc:
        raise PromptDiaryError(_save_failed_message(path, exc)) from exc
    finally:
        # Best-effort temp cleanup on any post-mkstemp failure (a successful replace already
        # renamed it away). Suppress unlink errors so a failed cleanup can never mask the
        # in-flight exception nor fail an otherwise-successful save — an orphaned temp file
        # beats a hidden error.
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
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


def resolve_content_language() -> LanguageNorm:
    """Resolve the typed content language: env, then config, then Simplified Chinese."""
    return resolve_content_language_setting(
        env_value=_env(CONTENT_LANGUAGE_ENV),
        config_value=load_config().content_language,
    )


def notion_is_configured() -> bool:
    """Return whether both a Notion token and database id resolve (from env or stored config)."""
    token, database_id = _notion_credentials()
    return bool(token and database_id)


def resolve_notion_credentials() -> tuple[Secret, str]:
    """Return the Notion ``(token, database_id)`` from env, then config; raise if missing.

    The token arrives already wrapped in :class:`Secret`, so even this function's missing-credential
    raise carries no bare token in its frame locals; reveal it only at the point of use.
    """
    token, database_id = _notion_credentials()
    if token is None or not database_id:
        raise PromptDiaryError(_missing_notion_credentials_message())
    return token, database_id


def _notion_credentials() -> tuple[Secret | None, str | None]:
    """Resolve the Notion token (wrapped) and database id (env, then config); either may be None.

    The token is wrapped in :class:`Secret` *here*, in the one frame that binds the raw string and
    that never raises, so no caller — including the resolvers that raise on missing credentials —
    ever holds a bare token local that a locals-capturing traceback could surface.
    """
    config = load_config()
    raw_token = _env(NOTION_TOKEN_ENV) or config.notion_api_key
    database_id = _env(NOTION_DATABASE_ENV) or config.notion_page_id
    return (Secret(raw_token) if raw_token else None), database_id


@dataclass(frozen=True)
class ReporterTarget:
    """The reporter to write at publish time: the target column, and the configured name (or None).

    The column is resolved unconditionally (defaulting to the 汇报人 column) so the publisher can
    distinguish "the database has a reporter column but no name is configured" (worth a warning)
    from "this database has no reporter column at all" (nothing to do, stay silent).
    """

    column: str
    name: str | None


def resolve_notion_reporter() -> ReporterTarget:
    """Resolve the reporter column and the optional name to write into it, from the stored config.

    The reporter is a free-form display name (like ``git config user.name``), not a credential, so
    it is read only from the stored config (no environment layer). The column defaults to
    :data:`_DEFAULT_REPORTER_PROPERTY` unless ``notion_reporter_property`` is set; ``name`` is
    ``None`` when unset, which the publisher surfaces as a warning rather than silently skipping.
    """
    config = load_config()
    column = config.notion_reporter_property or _DEFAULT_REPORTER_PROPERTY
    return ReporterTarget(column=column, name=config.notion_reporter or None)


def _corrupt_config_message(path: Path) -> str:
    # Deliberately omits the decoder's error detail: it can quote the malformed input, which may
    # include the stored token. The path is enough to act on (fix or remove the file).
    return f"the config file at {path} is invalid; fix or remove it."


def _save_failed_message(path: Path, exc: OSError) -> str:
    return f"failed to write the config file at {path}: {exc}"


def _missing_notion_credentials_message() -> str:
    return (
        f"no Notion credentials configured; set {NOTION_TOKEN_ENV} (integration token) and "
        f"{NOTION_DATABASE_ENV} (database id), or store them in {config_path()}."
    )
