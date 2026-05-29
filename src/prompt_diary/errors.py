"""User-facing exceptions for Prompt Diary."""

from __future__ import annotations


class PromptDiaryError(Exception):
    """Base class for actionable Prompt Diary failures."""


class ReportValidationError(PromptDiaryError):
    """Raised when a generated report does not satisfy the report contract."""
