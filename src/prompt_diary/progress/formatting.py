"""Formatting helpers for progress display."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Return a compact human-readable duration."""
    nonnegative = max(0.0, seconds)
    if nonnegative < 60:
        return f"{nonnegative:.1f}s"

    rounded = round(nonnegative)
    minutes, remainder = divmod(rounded, 60)
    if minutes < 60:
        return f"{minutes}m{remainder:02d}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{remainder:02d}s"
