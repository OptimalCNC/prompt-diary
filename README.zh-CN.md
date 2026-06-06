# Prompt Diary

语言：[English](README.md) | 简体中文

[![CI](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/ci.yml)
[![Publish](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml/badge.svg)](https://github.com/OptimalCNC/prompt-diary/actions/workflows/publish.yml)
[![PyPI](https://img.shields.io/pypi/v/prompt-diary.svg)](https://pypi.org/project/prompt-diary/)
![Coverage budget](https://img.shields.io/badge/coverage%20budget-100%25-brightgreen.svg)

Prompt Diary 会把本地 AI 助手的会话历史整理成受控工作区，并生成带证据的协作复盘报告，帮助你回顾并改进与 AI 编码代理一起工作的方式。

该工具支持 Python 3.10 及以上版本。安装后会提供 `report` 和 `prompt-diary`
两个命令。

## 用法

可以通过 uv 从 PyPI 安装 Prompt Diary，并把它作为隔离的命令行工具使用：

```bash
uv tool install prompt-diary
```

安装后可以先查看帮助，再准备工作区并生成报告：

```bash
report --help
prompt-diary --help
report prepare --date 2026-05-12 --timezone Asia/Shanghai
report generate --date 2026-05-12 --timezone Asia/Shanghai
report generate render --notion --date 2026-05-12 --timezone Asia/Shanghai
```

生成报告时，Prompt Diary 会先把每个阶段的产物写到磁盘，再进入下一步；各阶段也可以单独运行：

```bash
report generate evidence --date 2026-05-12 --timezone Asia/Shanghai --project-key <project> --session-ref S0001
report generate project --date 2026-05-12 --timezone Asia/Shanghai --project-key <project>
report generate daily --date 2026-05-12 --timezone Asia/Shanghai
```

配置好 Notion 后，`report generate render --notion` 可以把已经生成的报告作为新记录发布到 Notion 数据库。它会解析
已有工作区，要求其中存在 `daily-report.json`，从该模型重新生成 `report.md` 旁边结构稳定、可复现的
`report.notion.json`，然后发布该载荷。`report generate` 会把渲染作为流水线的最后一个阶段运行，并在
Notion 发布启用时执行同样的发布步骤：默认会在 Notion 已配置时发布；用 `--no-notion` 可跳过发布，用
`--notion` 可强制发布（若 Notion 未配置则报错）。
先用 `prompt-diary config init` 配置 Notion 集成令牌和目标数据库 ID（见[配置](#配置)）；凭据不会出现在命令行里。
每次发布都会追加一条新的日期记录；重复发布不会改写或删除已有记录。

实际生成由 Codex CLI 驱动，流程分为证据抽取、项目汇总、日报汇总和渲染四个阶段。前三个阶段产出
`daily-report.json`；确定性、无代理的渲染阶段再把该模型投影为 `report.md` 和 `report.notion.json`
两个视图。运行前需要安装并认证 `codex` CLI；上面的子命令用于在已准备好的工作区中单独执行某个阶段。

默认情况下，准备好的工作区和生成出来的报告都会放在当前用户的数据目录中（Linux 上是
`~/.local/share/prompt-diary/`；macOS 和 Windows 使用各自的平台目录），并按日期放在
`<reports-root>/work/<YYYY-MM-DD>/` 下。如果需要改位置，可以使用 `prepare` 和 `generate`（含
`generate render`）的 `--reports-root <path>`、`PROMPT_DIARY_HOME`，或已保存的配置（见[配置](#配置)）。优先级依次是
`--reports-root`、`PROMPT_DIARY_HOME`、已保存的配置、默认数据目录。（早期版本会写到当前目录下的
`./.reports`；如果想继续使用已有的本地目录，请传入 `--reports-root .reports`。）

在 TTY 中运行时，`prepare` 和 `generate` 会显示实时进度面板；如果通过管道或重定向输出，或者在 CI
中运行，它们会改为写入追加式日志。给任一命令加上 `--quiet` 可以关闭实时输出，只打印最终摘要。

## 配置

首次使用 Notion 发布前，运行 `prompt-diary config init` 进行交互式配置。命令会依次询问 Notion
集成令牌、数据目录（可选）、Notion 数据库 ID，以及可选的汇报人姓名（自由填写，类似 `git config user.name`，
发布时写入「汇报人」列）。这些凭据都会通过 Notion API 实时校验，校验通过后立刻保存：
令牌校验会显示已认证的集成和工作区，数据库校验会显示已连接数据库的名称。因此，即使中途退出，也会保留已经校验成功的设置。
配置会写入一个权限为 `0600` 的文件。令牌只会从该文件或环境变量读取，不会作为命令行参数传入，也不会明文打印；`config show` 会自动遮蔽令牌。

```bash
prompt-diary config init   # 交互式初始化；实时验证令牌和数据库
prompt-diary config show   # 打印配置（遮蔽令牌）和配置文件位置
prompt-diary config path   # 打印配置文件位置
```

配置文件默认放在当前用户的配置目录下（Linux 上是 `~/.config/prompt-diary/config.json`；macOS 和 Windows 使用各自的平台目录），
也可以用 `PROMPT_DIARY_CONFIG` 指定其他位置。每项设置都会按以下顺序取第一个可用值：CLI 参数（如果有）、环境变量、已保存的配置、内置默认值（如果有）。
因此，`NOTION_API_KEY` / `NOTION_PAGE_ID` 和 `--reports-root` / `PROMPT_DIARY_HOME` 仍然可以覆盖已保存的配置，这在 CI 中很有用。
配置完成后（包括通过上述环境变量配置），直接运行 `report generate` 就会默认发布到 Notion；在不应发布的 CI 或其他流水线中，请传入 `--no-notion`。

## 开发

本项目使用 [`uv`](https://docs.astral.sh/uv/) 管理 Python 版本、环境、依赖、构建和发布流程。

在设计新功能、修改报告内容或调整生成流水线之前，请先阅读 [`docs/src/product.md`](docs/src/product.md)。该文档定义了工具的目标和原则，后续设计需要遵守这些约束。

环境设置、构建命令、类型检查、测试、覆盖率、代码检查和提交前检查（包括可选的 Ubuntu 24.04 devcontainer）都在
[开发指南](docs/src/dev/guide.md) 中说明。代码库架构和 API 设计请参阅
[架构](docs/src/dev/architecture.md)。
