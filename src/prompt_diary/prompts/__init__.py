"""Generation prompt templates for Prompt Diary."""

from __future__ import annotations

from importlib.resources import files

from jinja2 import Environment, StrictUndefined


def _load(name: str) -> str:
    return files("prompt_diary.prompts").joinpath(name).read_text(encoding="utf-8")


def _render(name: str, **variables: str) -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False)  # noqa: S701 — plain-text prompts, not HTML
    return env.from_string(_load(name)).render(**variables)


def evidence_extractor_prompt(*, working_dir: str, session_ref: str) -> str:
    """Return the evidence extractor prompt with substituted workspace values."""
    return _render(
        "evidence-extractor.md",
        working_dir=working_dir,
        session_ref=session_ref,
    )


def project_synthesizer_prompt() -> str:
    """Return the project synthesizer prompt."""
    return _render("project-synthesizer.md")


def daily_synthesizer_prompt() -> str:
    """Return the daily synthesizer prompt."""
    return _render("daily-synthesizer.md")
