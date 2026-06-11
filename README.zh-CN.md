# Prompt Diary

语言：[English](README.md) | 简体中文

文档：[GitHub Pages](https://optimalcnc.github.io/prompt-diary/)

[![CI](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml)
[![Publish](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml)
[![PyPI](https://img.shields.io/pypi/v/prompt-diary.svg)](https://pypi.org/project/prompt-diary/)
![Coverage budget](https://img.shields.io/badge/coverage%20budget-100%25-brightgreen.svg)

Prompt Diary 会从本地助手会话历史准备有边界的工作区，并生成带证据的 Prompt Diary
报告，帮助用户回顾和改进与 AI 编码代理协作的方式。

该工具支持 Python 3.10 及以上版本。安装后会提供 `prompt-diary` 作为主要控制台命令；
同时安装 `report` 作为兼容别名。

## 用法

### 快速开始

通过 PyPI 把 Prompt Diary 安装为隔离的 `uv` 工具：

```bash
uv tool install --prerelease=allow prompt-diary
```

Prompt Diary 依赖 Codex Python SDK 来生成由代理支持的报告。当前 Codex SDK
打包使用预发布包，因此 `uv` 解析工具环境时需要显式允许预发布版本。

如果希望把报告发布到 Notion，先配置发布信息：

```bash
prompt-diary config init
```

生成最近一个已完成日期的报告：

```bash
prompt-diary generate
```

如果只需要本地报告，可以跳过配置并运行 `prompt-diary generate --no-notion`。该命令会在需要时准备工作区，
并把报告写入默认报告根目录。

### 配置

生成本地报告不要求配置。需要发布到 Notion 时，运行 `prompt-diary config init`；
该命令也允许接受或覆盖数据目录，并记录 `汇报人` 列所需的汇报人姓名：

```bash
prompt-diary config init
```

设置过程会询问 Notion 集成令牌、可选数据目录、目标数据库 ID，以及 `汇报人`
列所需的汇报人姓名。凭据保存前会先校验，随后写入权限为 `0600` 的单个配置文件，
且绝不会通过命令行传递。使用以下命令查看已保存的配置：

```bash
prompt-diary config show   # 打印配置（遮蔽令牌）和配置文件位置
prompt-diary config path   # 打印配置文件位置
```

设置 Codex 生成的自然语言报告内容所使用的语言：

```bash
prompt-diary config language zh-Hans
PROMPT_DIARY_CONTENT_LANGUAGE=zh-Hans prompt-diary generate
```

配置文件位于当前用户的配置目录下（Linux 上是
`~/.config/prompt-diary/config.json`；macOS 和 Windows 使用对应的平台目录），
可通过 `PROMPT_DIARY_CONFIG` 覆盖。环境变量仍会覆盖已保存的凭据和设置，包括
`NOTION_API_KEY`、`NOTION_PAGE_ID`、`PROMPT_DIARY_HOME` 和
`PROMPT_DIARY_CONTENT_LANGUAGE`。内容语言支持 `en`、`zh-Hans` 和 `zh-Hant`。
它只作用于生成的自然语言内容值；确定性渲染器负责的标签、标题、兜底文本和
Notion 元数据横幅在此版本中仍保持英文。

### 生成报告

直接运行完整报告流程：

```bash
prompt-diary generate
```

默认情况下，`prompt-diary generate` 会先解析时区，然后目标设为该时区里的前一个日历日：
也就是最近一个已完成的日期。如果工作区不存在，它会先准备工作区，然后生成报告，并渲染
`report.md` 和 `report.notion.json`。时区解析顺序依次为 `--timezone`、
`PROMPT_DIARY_TIMEZONE`、`TZ`、系统时区，最后是 UTC。当 Notion 令牌和数据库 ID
能够从已保存配置或环境变量解析出来时，默认会启用 Notion 发布。传入 `--no-notion`
可跳过发布；传入 `--notion` 则要求发布，并在 Notion 未配置时失败。

需要时可以显式指定目标：

```bash
prompt-diary generate --date 2026-05-12 --timezone Asia/Shanghai
prompt-diary generate --today
prompt-diary generate --date 2026-05-12 --timezone Asia/Shanghai --notion
```

报告根目录是准备工作区和生成报告文件的基准目录。默认使用当前用户的数据目录（Linux 上是
`~/.local/share/prompt-diary/`；macOS 和 Windows 使用对应的平台目录）。每次运行都会写入
`<reports-root>/work/<YYYY-MM-DD>/`。可通过 `--reports-root <path>`、
`PROMPT_DIARY_HOME` 或已保存的数据目录覆盖该位置。优先级依次为 `--reports-root`、
`PROMPT_DIARY_HOME`、已保存的数据目录，最后是默认的用户数据目录。早期版本会写入当前目录下的
`./.reports`；如果要继续使用已有本地目录，请传入 `--reports-root .reports`。

在交互式终端中，`prompt-diary generate` 会显示实时进度。输出被重定向或在 CI
中运行时，它会打印纯文本日志。传入 `--quiet` 可关闭实时输出，只打印最终摘要。

## 开发

本项目使用 [`uv`](https://docs.astral.sh/uv/) 管理 Python 版本、环境、依赖、构建和发布流程。

在设计新功能、修改报告内容或调整生成流水线之前，请先阅读
[`docs/src/product.md`](docs/src/product.md)。该文档定义了工具的目的和原则，下游设计必须满足这些约束。

环境设置、构建命令、类型检查、测试、覆盖率、代码检查和提交前检查（包括可选的 Ubuntu 24.04
devcontainer）都在 [开发指南](docs/src/dev/guide.md) 中说明。代码库架构和 API
设计请参阅 [架构](docs/src/dev/architecture.md)。
