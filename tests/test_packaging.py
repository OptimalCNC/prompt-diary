from __future__ import annotations

from importlib.metadata import metadata


def test_base_distribution_metadata_does_not_require_codex_prereleases() -> None:
    requires_dist = metadata("prompt-diary").get_all("Requires-Dist") or []
    codex_requirements = [
        requirement
        for requirement in requires_dist
        if requirement.startswith(("openai-codex", "openai-codex-cli-bin"))
    ]

    assert codex_requirements
    assert all("extra == 'codex'" in requirement for requirement in codex_requirements)


def test_codex_extra_pins_sdk_and_runtime_versions() -> None:
    package_metadata = metadata("prompt-diary")
    requires_dist = package_metadata.get_all("Requires-Dist") or []

    assert "codex" in (package_metadata.get_all("Provides-Extra") or [])
    assert any(requirement.startswith("openai-codex==0.1.0b3") for requirement in requires_dist)
    assert any(
        requirement.startswith("openai-codex-cli-bin==0.137.0a4") for requirement in requires_dist
    )
