from __future__ import annotations

from importlib.metadata import metadata


def test_distribution_requires_codex_sdk_without_extra_marker() -> None:
    requires_dist = metadata("prompt-diary").get_all("Requires-Dist") or []
    codex_sdk_requirements = [
        requirement for requirement in requires_dist if requirement.startswith("openai-codex")
    ]

    assert codex_sdk_requirements == ["openai-codex==0.1.0b3"]


def test_distribution_does_not_declare_codex_cli_runtime_directly() -> None:
    requires_dist = metadata("prompt-diary").get_all("Requires-Dist") or []

    assert not any(requirement.startswith("openai-codex-cli-bin") for requirement in requires_dist)


def test_distribution_does_not_provide_codex_extra() -> None:
    package_metadata = metadata("prompt-diary")

    assert "codex" not in (package_metadata.get_all("Provides-Extra") or [])
