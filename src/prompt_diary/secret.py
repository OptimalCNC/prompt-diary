"""A redacting wrapper for secret strings (the Notion integration token).

Wrapping the token in :class:`Secret` keeps the raw value out of the usual accidental-disclosure
surfaces: ``str(secret)`` and ``repr(secret)`` render ``"***"``, so the token never appears in an
f-string, a log line, an error message, or a traceback's captured frame locals. The raw value is
reachable only through the explicit :meth:`Secret.reveal`, which marks the few call sites that
genuinely need it — building the SDK client and scrubbing the token from an error message.
(Deliberate field introspection — ``dataclasses.asdict``/``vars`` — still exposes it; this guards
accidental leaks, not a determined caller.)
"""

from __future__ import annotations

from dataclasses import dataclass

REDACTED = "***"  # the redaction marker, shared with the error-message scrubber in render.notion


@dataclass(frozen=True)
class Secret:
    """A secret string whose ``str``/``repr`` are redacted; the raw value is only via ``reveal``."""

    _value: str

    def reveal(self) -> str:
        """Return the raw secret. Use only where the value is genuinely needed (e.g. the SDK)."""
        return self._value

    def __str__(self) -> str:
        return REDACTED

    def __repr__(self) -> str:
        return f"Secret({REDACTED})"
