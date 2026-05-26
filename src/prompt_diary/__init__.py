"""Prompt Diary package."""

from prompt_diary.api import generate_prompt_diary, prepare_prompt_diary
from prompt_diary.prompts import (
    daily_synthesizer_prompt,
    evidence_extractor_prompt,
    project_synthesizer_prompt,
)

__all__ = [
    "__version__",
    "daily_synthesizer_prompt",
    "evidence_extractor_prompt",
    "generate_prompt_diary",
    "prepare_prompt_diary",
    "project_synthesizer_prompt",
]

__version__ = "0.1.0a2"
