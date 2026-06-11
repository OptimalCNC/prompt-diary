from __future__ import annotations

from importlib.metadata import metadata


def test_distribution_metadata_pins_installable_codex_runtime() -> None:
    requires_dist = metadata("prompt-diary").get_all("Requires-Dist") or []

    assert "openai-codex==0.1.0b3" in requires_dist
    assert "openai-codex-cli-bin==0.137.0a4" in requires_dist
