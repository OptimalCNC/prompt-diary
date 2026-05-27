"""Install the optional Codex SDK into the current runtime environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from collections.abc import Sequence

CODEX_SDK_PACKAGE_SPEC = (
    "openai-codex @ git+https://github.com/openai/codex.git@rust-v0.134.0#subdirectory=sdk/python"
)
_VERIFY_IMPORT_SCRIPT = (
    "import openai_codex\nprint(getattr(openai_codex, '__file__', 'openai_codex'))\n"
)


class CodexBootstrapError(PromptDiaryError):
    """Raised when the optional Codex SDK bootstrap cannot complete."""


@dataclass(frozen=True)
class CodexBootstrapTarget:
    """Resolved Python environment that will receive the Codex SDK."""

    python_executable: str
    environment_root: Path
    site_packages: Path
    uv_marker: str | None
    is_system_python: bool


@dataclass(frozen=True)
class CodexBootstrapResult:
    """Successful Codex SDK bootstrap details."""

    target: CodexBootstrapTarget
    package_spec: str
    import_path: str
    messages: tuple[str, ...]


def bootstrap_codex_sdk() -> CodexBootstrapResult:
    """Install and verify the pinned Codex SDK in the current Python environment."""
    target = resolve_codex_bootstrap_target()
    if target.is_system_python:
        raise CodexBootstrapError(_system_python_message(target))

    uv_path = shutil.which("uv")
    if uv_path is None:
        raise CodexBootstrapError(_missing_uv_message())

    install_command = [
        uv_path,
        "pip",
        "install",
        "--python",
        target.python_executable,
        "--no-deps",
        CODEX_SDK_PACKAGE_SPEC,
    ]
    install_result = _run_command(install_command)
    if install_result.returncode != 0:
        raise CodexBootstrapError(_failed_process_message("install", install_result))

    verify_command = [target.python_executable, "-c", _VERIFY_IMPORT_SCRIPT]
    verify_result = _run_command(verify_command)
    if verify_result.returncode != 0:
        raise CodexBootstrapError(_failed_process_message("import verification", verify_result))

    import_path = verify_result.stdout.strip() or "openai_codex"
    return CodexBootstrapResult(
        target=target,
        package_spec=CODEX_SDK_PACKAGE_SPEC,
        import_path=import_path,
        messages=_success_messages(target, CODEX_SDK_PACKAGE_SPEC, import_path),
    )


def resolve_codex_bootstrap_target() -> CodexBootstrapTarget:
    """Resolve the current process environment used as the bootstrap target."""
    environment_root = Path(sys.prefix)
    return CodexBootstrapTarget(
        python_executable=sys.executable,
        environment_root=environment_root,
        site_packages=Path(sysconfig.get_paths()["purelib"]),
        uv_marker=_read_uv_marker(environment_root / "pyvenv.cfg"),
        is_system_python=sys.prefix == sys.base_prefix,
    )


def _read_uv_marker(pyvenv_cfg: Path) -> str | None:
    try:
        text = pyvenv_cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "uv":
            marker = value.strip()
            return marker or "present"
    return None


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )


def _success_messages(
    target: CodexBootstrapTarget,
    package_spec: str,
    import_path: str,
) -> tuple[str, ...]:
    uv_message = target.uv_marker if target.uv_marker is not None else "not detected"
    return (
        f"Python: {target.python_executable}",
        f"Environment: {target.environment_root}",
        f"site-packages: {target.site_packages}",
        f"uv environment: {uv_message}",
        f"Installed: {package_spec}",
        f"Verified openai_codex import: {import_path}",
    )


def _system_python_message(target: CodexBootstrapTarget) -> str:
    return (
        "Refusing to install the Codex SDK into system Python at "
        f"{target.environment_root}. Run prompt-diary from a uv tool or virtual environment, "
        "then rerun `prompt-diary codex bootstrap`."
    )


def _missing_uv_message() -> str:
    return (
        "Codex SDK bootstrap requires `uv` on PATH. Install uv, then rerun "
        "`prompt-diary codex bootstrap`."
    )


def _failed_process_message(
    action: str,
    completed: subprocess.CompletedProcess[str],
) -> str:
    message = f"Codex SDK {action} failed with exit code {completed.returncode}."
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if stdout:
        message = f"{message}\nstdout:\n{stdout}"
    if stderr:
        message = f"{message}\nstderr:\n{stderr}"
    return message
