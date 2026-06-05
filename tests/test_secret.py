"""Tests for the redacting Secret wrapper."""

from __future__ import annotations

from prompt_diary.secret import Secret


def test_secret_reveal_returns_the_raw_value() -> None:
    assert Secret("ntn_abc123").reveal() == "ntn_abc123"


def test_secret_str_and_repr_redact_the_value() -> None:
    secret = Secret("ntn_abc123")
    assert str(secret) == "***"
    assert repr(secret) == "Secret(***)"
    # The raw value never leaks through string formatting, the common accidental-disclosure path.
    assert "ntn_abc123" not in f"token={secret} repr={secret!r}"


def test_secret_equality_is_by_value() -> None:
    assert Secret("a") == Secret("a")
    assert Secret("a") != Secret("b")
