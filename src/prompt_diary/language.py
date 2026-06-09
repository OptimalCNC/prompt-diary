"""Content-language norm for Codex-backed report generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path

CONTENT_LANGUAGE_ENV = "PROMPT_DIARY_CONTENT_LANGUAGE"
GENERATED_AGENTS_MARKER = "<!-- prompt-diary-generated-language-norm-v1 -->"


class ContentLanguageTag(str, Enum):
    """Supported Prompt Diary content-language tags."""

    EN = "en"
    ZH_HANS = "zh-Hans"
    ZH_HANT = "zh-Hant"


SUPPORTED_CONTENT_LANGUAGE_TAGS = tuple(tag.value for tag in ContentLanguageTag)

_SUPPORTED_BY_NORMALIZED = {tag.value.lower(): tag for tag in ContentLanguageTag}
_LANGUAGE_LABELS = {
    ContentLanguageTag.EN: "English",
    ContentLanguageTag.ZH_HANS: "Simplified Chinese",
    ContentLanguageTag.ZH_HANT: "Traditional Chinese",
}


@dataclass(frozen=True)
class LanguageNorm:
    """A parsed, supported content-language norm."""

    tag: ContentLanguageTag

    @classmethod
    def from_tag(cls, value: str) -> LanguageNorm:
        """Parse a supported language tag into a typed norm."""
        return parse_content_language(value)

    @property
    def label(self) -> str:
        """Return the human-readable language name."""
        return _LANGUAGE_LABELS[self.tag]


def parse_content_language(value: str) -> LanguageNorm:
    """Parse a content-language tag, rejecting unsupported values."""
    stripped = value.strip()
    tag = _SUPPORTED_BY_NORMALIZED.get(stripped.lower())
    if tag is None:
        raise PromptDiaryError(_unsupported_content_language_message())
    return LanguageNorm(tag=tag)


def resolve_content_language_setting(
    *, env_value: str | None, config_value: str | None
) -> LanguageNorm:
    """Resolve content language from env, stored config, then the English default."""
    if env_value is not None and env_value.strip():
        return parse_content_language(env_value)
    if config_value is not None and config_value.strip():
        return parse_content_language(config_value)
    return LanguageNorm(tag=ContentLanguageTag.EN)


def render_language_instructions(language: LanguageNorm) -> str:
    """Render compact runtime instructions for the selected content language."""
    return (
        "Prompt Diary content language norm:\n"
        f"- Translate generated natural-language content values into {language.label} "
        f"(`{language.tag.value}`).\n"
        "- Preserve JSON keys, MCP tool names, enum values, IDs, citations, paths, commands, "
        "code identifiers, and verbatim source text."
    )


def render_generated_agents_file(language: LanguageNorm) -> str:
    """Render the generated workspace AGENTS.md content."""
    return (
        "# Prompt Diary Runtime Instructions\n\n"
        f"{GENERATED_AGENTS_MARKER}\n\n"
        f"{render_language_instructions(language)}\n"
    )


def write_generated_agents_file(workspace_path: Path, language: LanguageNorm) -> Path:
    """Write Prompt Diary's generated AGENTS.md, refusing to clobber user-authored files."""
    agents_path = workspace_path / "AGENTS.md"
    if agents_path.exists() and GENERATED_AGENTS_MARKER not in agents_path.read_text(
        encoding="utf-8"
    ):
        raise PromptDiaryError(_unowned_agents_file_message(agents_path))
    agents_path.write_text(render_generated_agents_file(language), encoding="utf-8")
    return agents_path


def _unsupported_content_language_message() -> str:
    return (
        "unsupported content language; expected one of: "
        f"{', '.join(SUPPORTED_CONTENT_LANGUAGE_TAGS)}."
    )


def _unowned_agents_file_message(path: Path) -> str:
    return (
        f"refusing to replace existing AGENTS.md at {path}; it does not contain Prompt Diary's "
        "generated marker."
    )
