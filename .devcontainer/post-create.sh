#!/usr/bin/env bash
set -euo pipefail

project_root="${PROJECT_ROOT:-$(pwd)}"
gh_config_dir="${GH_CONFIG_DIR:-${HOME}/.config/gh}"
claude_config_dir="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
codex_home="${CODEX_HOME:-${HOME}/.codex}"
uv_python_install_dir="${UV_PYTHON_INSTALL_DIR:-/opt/uv/python}"

sudo mkdir -p \
    /opt/cache \
    /opt/uv/bin \
    /opt/uv/venv \
    "${uv_python_install_dir}" \
    "${gh_config_dir}" \
    "${claude_config_dir}" \
    "${codex_home}" \
    "${project_root}/.venv"

sudo chown -R "$(id -u):$(id -g)" \
    /opt/cache \
    /opt/uv \
    "${gh_config_dir}" \
    "${claude_config_dir}" \
    "${codex_home}" \
    "${project_root}/.venv"

if ! git config --global --get-all safe.directory | grep -Fxq "${project_root}"; then
    git config --global --add safe.directory "${project_root}"
fi

cd "${project_root}"
uv sync --locked --python 3.10
