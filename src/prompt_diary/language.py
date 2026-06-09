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


@dataclass(frozen=True)
class _GeneratedContentRule:
    tag: ContentLanguageTag
    rule: str


_GENERATED_CONTENT_RULES = {
    ContentLanguageTag.EN: _GeneratedContentRule(
        tag=ContentLanguageTag.EN,
        rule=(
            "Write generated natural-language content values in English ({tag}). This includes "
            "summaries, assessments, risks, next actions, team-learning prose, and other "
            "free-text report values produced by the agent."
        ),
    ),
    ContentLanguageTag.ZH_HANS: _GeneratedContentRule(
        tag=ContentLanguageTag.ZH_HANS,
        rule=(
            "用简体中文 ({tag}) 撰写生成的自然语言内容值, 包括摘要、评估、风险、后续行动、"
            "团队学习文字, 以及代理生成的其他自由文本报告值。"
        ),
    ),
    ContentLanguageTag.ZH_HANT: _GeneratedContentRule(
        tag=ContentLanguageTag.ZH_HANT,
        rule=(
            "用繁體中文 ({tag}) 撰寫生成的自然語言內容值, 包括摘要、評估、風險、後續行動、"
            "團隊學習文字, 以及代理生成的其他自由文字報告值。"
        ),
    ),
}

_PRESERVATION_ROWS = (
    (
        "Schema and control tokens",
        "Preserve JSON keys, MCP tool names, enum values, IDs, citations, paths, commands, and "
        "code identifiers exactly as required by the schema or tool contract. Do not translate "
        "or rewrite them.",
    ),
    (
        "Source material",
        "Preserve verbatim source text exactly. Do not translate quoted user messages, "
        "transcript excerpts, logs, code, command output, or cited source snippets.",
    ),
    (
        "Renderer-owned text",
        "Do not change renderer-owned labels, headings, fallbacks, and Notion metadata banners; "
        "they are deterministic renderer output, not generated content values.",
    ),
)


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
    """Resolve content language from env, stored config, then the Simplified Chinese default."""
    if env_value is not None and env_value.strip():
        return parse_content_language(env_value)
    if config_value is not None and config_value.strip():
        return parse_content_language(config_value)
    return LanguageNorm(tag=ContentLanguageTag.ZH_HANS)


def render_language_instructions(language: LanguageNorm) -> str:
    """Render deterministic runtime instructions for the selected content language."""
    selected_tag = f"`{language.tag.value}`"
    content_rule = _GENERATED_CONTENT_RULES[language.tag]
    row_items = (
        (
            "Generated natural-language content values",
            content_rule.rule.format(tag=f"`{content_rule.tag.value}`"),
        ),
        *_PRESERVATION_ROWS,
    )
    rendered_rows = "\n".join(
        f"| {content_class} | {rule} |" for content_class, rule in row_items
    )
    return "\n".join(
        (
            "Prompt Diary content language norm",
            "",
            f"Selected content language tag: {selected_tag}.",
            "",
            "| Content class | Rule |",
            "| --- | --- |",
            rendered_rows,
        )
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
