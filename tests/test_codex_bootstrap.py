from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

import prompt_diary.codex_bootstrap as codex_bootstrap
from prompt_diary.codex_bootstrap import (
    CODEX_SDK_PACKAGE_SPEC,
    CodexBootstrapError,
    bootstrap_codex_sdk,
    resolve_codex_bootstrap_target,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def test_resolve_bootstrap_target_reports_current_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_root = tmp_path / "tool-env"
    env_root.mkdir()
    (env_root / "pyvenv.cfg").write_text("home = /usr/bin\nuv = 0.7.8\n", encoding="utf-8")
    python = env_root / "bin" / "python"
    site_packages = env_root / "lib" / "python3.12" / "site-packages"
    monkeypatch.setattr(codex_bootstrap.sys, "executable", str(python))
    monkeypatch.setattr(codex_bootstrap.sys, "prefix", str(env_root))
    monkeypatch.setattr(codex_bootstrap.sys, "base_prefix", "/usr")
    monkeypatch.setattr(
        codex_bootstrap.sysconfig,
        "get_paths",
        lambda: {"purelib": str(site_packages)},
    )

    target = resolve_codex_bootstrap_target()

    assert target.python_executable == str(python)
    assert target.environment_root == env_root
    assert target.site_packages == site_packages
    assert target.uv_marker == "0.7.8"
    assert not target.is_system_python


def test_resolve_bootstrap_target_reports_non_uv_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_root = tmp_path / "tool-env"
    env_root.mkdir()
    (env_root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    _patch_runtime(
        monkeypatch,
        env_root=env_root,
        python=env_root / "bin" / "python",
        site_packages=env_root / "site-packages",
    )

    target = resolve_codex_bootstrap_target()

    assert target.uv_marker is None


def test_bootstrap_uses_uv_pip_install_and_verifies_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_root = tmp_path / "tool-env"
    site_packages = env_root / "site-packages"
    python = env_root / "bin" / "python"
    _patch_runtime(monkeypatch, env_root=env_root, python=python, site_packages=site_packages)
    checked_tools: list[str] = []
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        checked_tools.append(name)
        return "/usr/bin/uv" if name == "uv" else None

    def fake_run(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs == {"check": False, "capture_output": True, "text": True}
        command_list = list(command)
        commands.append(command_list)
        if command_list[0] == str(python):
            return subprocess.CompletedProcess(
                command_list,
                0,
                stdout="/env/openai_codex\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command_list, 0, stdout="installed\n", stderr="")

    monkeypatch.setattr(codex_bootstrap.shutil, "which", fake_which)
    monkeypatch.setattr(codex_bootstrap.subprocess, "run", fake_run)

    result = bootstrap_codex_sdk()

    assert checked_tools == ["uv"]
    assert commands[0] == [
        "/usr/bin/uv",
        "pip",
        "install",
        "--python",
        str(python),
        "--no-deps",
        CODEX_SDK_PACKAGE_SPEC,
    ]
    assert commands[1][0:2] == [str(python), "-c"]
    assert "import openai_codex" in commands[1][2]
    assert result.import_path == "/env/openai_codex"
    assert any(message == f"site-packages: {site_packages}" for message in result.messages)
    assert any(message == "uv environment: not detected" for message in result.messages)


def test_bootstrap_refuses_system_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_runtime(
        monkeypatch,
        env_root=tmp_path / "system",
        python=tmp_path / "system" / "bin" / "python",
        site_packages=tmp_path / "system" / "site-packages",
        base_prefix=tmp_path / "system",
    )
    monkeypatch.setattr(codex_bootstrap.shutil, "which", _which_uv)

    with pytest.raises(CodexBootstrapError, match="Refusing to install"):
        bootstrap_codex_sdk()


def test_bootstrap_requires_uv_on_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_runtime(
        monkeypatch,
        env_root=tmp_path / "tool-env",
        python=tmp_path / "tool-env" / "bin" / "python",
        site_packages=tmp_path / "tool-env" / "site-packages",
    )
    monkeypatch.setattr(codex_bootstrap.shutil, "which", _which_missing)

    with pytest.raises(CodexBootstrapError, match="requires `uv` on PATH"):
        bootstrap_codex_sdk()


def test_bootstrap_reports_install_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_runtime(
        monkeypatch,
        env_root=tmp_path / "tool-env",
        python=tmp_path / "tool-env" / "bin" / "python",
        site_packages=tmp_path / "tool-env" / "site-packages",
    )
    monkeypatch.setattr(codex_bootstrap.shutil, "which", _which_uv)
    monkeypatch.setattr(
        codex_bootstrap.subprocess,
        "run",
        _failed_install_run,
    )

    with pytest.raises(CodexBootstrapError) as exc_info:
        bootstrap_codex_sdk()

    message = str(exc_info.value)
    assert "install failed with exit code 2" in message
    assert "stdout:\ndownload failed" in message
    assert "stderr:\nnetwork unavailable" in message


def test_bootstrap_reports_post_install_import_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_root = tmp_path / "tool-env"
    python = env_root / "bin" / "python"
    _patch_runtime(
        monkeypatch,
        env_root=env_root,
        python=python,
        site_packages=env_root / "site-packages",
    )
    commands: list[list[str]] = []

    def fake_run(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        commands.append(command_list)
        if command_list[0] == str(python):
            return subprocess.CompletedProcess(
                command_list,
                1,
                stdout="",
                stderr="No module named openai_codex",
            )
        return subprocess.CompletedProcess(command_list, 0, stdout="", stderr="")

    monkeypatch.setattr(codex_bootstrap.shutil, "which", _which_uv)
    monkeypatch.setattr(codex_bootstrap.subprocess, "run", fake_run)

    with pytest.raises(CodexBootstrapError) as exc_info:
        bootstrap_codex_sdk()

    assert len(commands) == 2
    assert "import verification failed with exit code 1" in str(exc_info.value)
    assert "No module named openai_codex" in str(exc_info.value)


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    env_root: Path,
    python: Path,
    site_packages: Path,
    base_prefix: Path | None = None,
) -> None:
    monkeypatch.setattr(codex_bootstrap.sys, "executable", str(python))
    monkeypatch.setattr(codex_bootstrap.sys, "prefix", str(env_root))
    monkeypatch.setattr(
        codex_bootstrap.sys,
        "base_prefix",
        str(base_prefix if base_prefix is not None else env_root.parent),
    )
    monkeypatch.setattr(
        codex_bootstrap.sysconfig,
        "get_paths",
        lambda: {"purelib": str(site_packages)},
    )


def _which_uv(name: str) -> str | None:
    return "/usr/bin/uv" if name == "uv" else None


def _which_missing(name: str) -> str | None:
    del name
    return None


def _failed_install_run(
    command: Sequence[str],
    **_kwargs: object,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        list(command),
        2,
        stdout="download failed",
        stderr="network unavailable",
    )
