ARG UV_VERSION=0.11.14

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ubuntu:24.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG CONTAINER_USER=ubuntu
ARG USER_UID=1000
ARG USER_GID=${USER_UID}

ENV DEBIAN_FRONTEND=noninteractive
ENV CONTAINER_USER=${CONTAINER_USER}

RUN if ! getent group ${CONTAINER_USER} > /dev/null; then \
        groupadd --gid ${USER_GID} ${CONTAINER_USER}; \
    fi \
    && if ! id -u ${CONTAINER_USER} > /dev/null 2>&1; then \
        useradd --uid ${USER_UID} --gid ${CONTAINER_USER} --create-home --shell /bin/bash ${CONTAINER_USER}; \
    fi \
    && mkdir -p /home/${CONTAINER_USER} \
    && mkdir -p /etc/sudoers.d \
    && chown ${CONTAINER_USER}:${CONTAINER_USER} /home/${CONTAINER_USER} \
    && echo "${CONTAINER_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${CONTAINER_USER} \
    && chmod 0440 /etc/sudoers.d/${CONTAINER_USER}

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        bash-completion \
        build-essential \
        ca-certificates \
        curl \
        fd-find \
        git \
        gnupg \
        jq \
        less \
        locales \
        lsb-release \
        openssh-client \
        pkg-config \
        procps \
        python3 \
        python3-venv \
        rsync \
        sudo \
        tmux \
        tree \
        unzip \
        vim \
        wget \
        zip \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

COPY --from=uv /uv /uvx /usr/local/bin/

ENV CACHE_DIR=/opt/cache
ENV UV_CACHE_DIR=${CACHE_DIR}/uv
ENV UV_TOOL_BIN_DIR=/opt/uv/bin
ENV UV_TOOL_DIR=/opt/uv/venv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=automatic
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python

ENV BUN_INSTALL=/opt/bun
ENV CLAUDE_CONFIG_DIR=/home/${CONTAINER_USER}/.claude
ENV CODEX_HOME=/home/${CONTAINER_USER}/.codex
ENV GH_CONFIG_DIR=/home/${CONTAINER_USER}/.config/gh
ENV PATH=/home/${CONTAINER_USER}/.local/bin:${UV_TOOL_BIN_DIR}:${BUN_INSTALL}/bin:${PATH}

RUN mkdir -p \
        ${CACHE_DIR} \
        ${UV_TOOL_BIN_DIR} \
        ${UV_TOOL_DIR} \
        ${UV_PYTHON_INSTALL_DIR} \
        ${BUN_INSTALL} \
        ${CLAUDE_CONFIG_DIR} \
        ${CODEX_HOME} \
        ${GH_CONFIG_DIR} \
    && chown -R ${CONTAINER_USER}:${CONTAINER_USER} \
        ${CACHE_DIR} \
        /opt/uv \
        ${BUN_INSTALL} \
        /home/${CONTAINER_USER}/.config \
        ${CLAUDE_CONFIG_DIR} \
        ${CODEX_HOME}

ENV RG_VERSION=15.1.0
ENV GH_VERSION=2.89.0
RUN arch="$(dpkg --print-architecture)" \
    && gh_cli_filename="gh_${GH_VERSION}_linux_${arch}.deb" \
    && rg_cli_filename="ripgrep_${RG_VERSION}-1_${arch}.deb" \
    && curl -fsSLO "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${gh_cli_filename}" \
    && curl -fsSLO "https://github.com/BurntSushi/ripgrep/releases/download/${RG_VERSION}/${rg_cli_filename}" \
    && dpkg -i "${gh_cli_filename}" "${rg_cli_filename}" \
    && rm "${gh_cli_filename}" "${rg_cli_filename}"

RUN curl -fsSL https://bun.sh/install | bash \
    && printf '%s\n' \
        '#!/bin/sh' \
        'if [ "$1" = "--version" ] || [ "$1" = "-v" ]; then' \
        '    exec /opt/bun/bin/bun --version' \
        'fi' \
        'exec /opt/bun/bin/bun "$@"' \
        > /usr/local/bin/node \
    && chmod 0755 /usr/local/bin/node \
    && chown -R ${CONTAINER_USER}:${CONTAINER_USER} ${BUN_INSTALL}

USER ${CONTAINER_USER}

RUN curl -fsSL https://claude.ai/install.sh | bash \
    && bun install -g @openai/codex

RUN command -v uv \
    && command -v bun \
    && command -v node \
    && command -v gh \
    && command -v rg \
    && command -v fd \
    && command -v claude \
    && command -v codex \
    && uv --version \
    && bun --version \
    && node --version \
    && gh --version \
    && rg --version \
    && fd --version \
    && claude --version \
    && codex --version

WORKDIR /ws/src
