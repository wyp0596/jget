<div align="center">

# 🎫 jget

**终端里一键读完 Jira 票，Cursor / Claude 也能直接读。**

[English](README.md) · 简体中文

[![License](https://img.shields.io/github/license/wyp0596/jget?color=blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)](jget.py)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](#-安装)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill-D97757?logo=claude&logoColor=white)](#-配合-ai-使用)
[![Cursor](https://img.shields.io/badge/Cursor-skill-000000?logo=cursor&logoColor=white)](#-配合-ai-使用)

[为什么选 jget](#-为什么选-jget) · [安装](#-安装) · [配置](#-配置) · [用法](#-用法) · [AI](#-配合-ai-使用)

</div>

---

> **定位一句话**：不是「功能更全的 Jira CLI」，而是「给人和 AI 用的极简读票工具」。

```text
$ jget PROJ-123 -n 2
================================================================================
[PROJ-123] 修复登录页 JWT 校验报错
================================================================================
Status:   In Progress
Assignee: Alex Mercer

--------------------------------------------------------------------------------
[ Description ]
--------------------------------------------------------------------------------
长时间未操作后重新提交，后端返回 500。
需要检查 JWT 过期逻辑，并返回正确的 401。

[image: login-error.png]

--------------------------------------------------------------------------------
[ Attachments (1) ]
--------------------------------------------------------------------------------
[1] login-error.png (84.2 KB)
    https://your-domain.atlassian.net/rest/api/2/attachment/content/10001

--------------------------------------------------------------------------------
[ Comments (latest 2 of 5) ]
--------------------------------------------------------------------------------
[1] 张三 (2026-03-20 14:30)
已复现，前端需要配合做无感刷新 token。

[2] 李四 (2026-03-21 09:15)
PR 已提交，等待 CI。
```

## 🎯 为什么选 jget

市面上已有完整的 Jira CLI（建票、转状态、看板 TUI、批量管理）。`jget` **刻意不做这些**，只把「接到票号 → 看懂上下文」做到极致：

| 对比点 | jget | 常见完整 Jira CLI |
|--------|------|-------------------|
| 核心能力 | **读票**：描述 / 评论 / 附件 | 读 + 写 + 搜索 + 看板 |
| 安装成本 | 一行 `curl \| sh`，零依赖 | 往往要装二进制、配多套命令 |
| AI 集成 | **安装时自动写入 Cursor / Claude Code skill** | 通常只给人用，要自己教 Agent |
| 截图 / 附件 | 占位符可读 + 一键下载，方便 Agent 看图 | 多数只给链接或原始 wiki 标记 |
| 认证 | Cloud Token / Server PAT / 账号密码 | 多数认真做 Cloud，Server 体验参差 |
| 体积 | 单文件 Python，标准库即可 | 功能多，心智负担也更重 |

**最适合这些人：**

- 日常用 **Cursor / Claude Code** 写代码，经常对着票号开工
- 只想快速看描述、最新讨论和截图，不想在终端里「管理 Jira」
- 团队需要一个**低摩擦、可共享**的读票命令，而不是再推一套重型工具

## ✨ 功能亮点

- 📝 **一张票一屏看完** — 标题、状态、经办人、描述、评论、附件
- 💬 **默认看最新讨论** — `-n` 控制条数，`-n -1` 看全部
- 🖼️ **截图可读** — `!shot.png|width=300!` 显示为 `[image: shot.png]`
- 📎 **附件可下** — `-d <目录>` 一键下载截图、视频、文件
- 🤖 **人和 AI 都能用** — 装完后直接对 Agent 说「看下 PROJ-123」
- 🔐 **Cloud / Server 都覆盖** — API Token、PAT、账号密码
- 🪶 **零依赖** — 一个 `jget.py`，Python 3.8+ 标准库

## 🚀 三分钟上手

```bash
# 1. 安装（同时装好 Cursor / Claude skill）
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh

# 2. 配置（以 Jira Cloud 为例）
export JIRA_URL=https://your-domain.atlassian.net
export JIRA_USER=you@example.com
export JIRA_TOKEN=<api-token>

# 3. 使用
jget PROJ-123
```

## 📦 安装

> **环境要求：** macOS / Linux / Windows，Python 3.8+。

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh
```

**Windows（PowerShell）**

```powershell
irm https://raw.githubusercontent.com/wyp0596/jget/main/install.ps1 | iex
```

安装脚本会：

1. 把 `jget` 命令放到 `~/.local/bin`（Windows 为 `%USERPROFILE%\.local\bin`，含 `jget.py` + `jget.cmd`）
2. 为检测到的 AI 工具安装 skill：
   - Claude Code → `~/.claude/skills/jget/SKILL.md`
   - Cursor → `~/.cursor/skills/jget/SKILL.md`

若安装目录不在 `PATH` 里，脚本会打印要加的那一行。重复执行安装即可升级。

<details>
<summary><b>安装参数</b></summary>

<br>

| 参数 | 说明 |
|------|------|
| `--claude` | 只装 Claude Code 的 skill |
| `--cursor` | 只装 Cursor 的 skill |
| `--bin-dir DIR` | 指定可执行文件目录（默认 `~/.local/bin`） |
| `--skill-only` | 只装 skill，不装命令 |

macOS / Linux 在 `sh -s --` 后传参：

```bash
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh -s -- --cursor
```

Windows 先设环境变量再安装：

```powershell
$env:JGET_INSTALL_ARGS = "--cursor"
irm https://raw.githubusercontent.com/wyp0596/jget/main/install.ps1 | iex
```

</details>

<details>
<summary><b>从源码安装</b></summary>

<br>

```bash
git clone https://github.com/wyp0596/jget.git
cd jget
python3 jget.py install      # Windows: py jget.py install
```

</details>

## 🔧 配置

通过环境变量读取配置。macOS / Linux 写入 `~/.zshrc` 或 `~/.bashrc`；Windows 用 `setx NAME "value"` 后开新终端。

| 变量 | 是否必填 | 说明 |
|------|----------|------|
| `JIRA_URL` | ✅ | Jira 地址 |
| `JIRA_TOKEN` | ✅（未设密码时） | Cloud API Token，或 Server / DC 的 PAT |
| `JIRA_USER` | Basic 认证时 | Cloud 邮箱，或 Server 用户名 |
| `JIRA_PASSWORD` | — | 账号密码（仅 Server / DC；`JIRA_TOKEN` 为空时才用） |
| `JIRA_AUTH` | — | `basic`（默认）或 `bearer` |

### ☁️ Jira Cloud（默认）

邮箱 + API Token。申请地址：<https://id.atlassian.com/manage-profile/security/api-tokens>

```bash
export JIRA_URL=https://your-domain.atlassian.net
export JIRA_USER=you@example.com
export JIRA_TOKEN=<api-token>
```

### 🏢 Jira Server / Data Center — Personal Access Token

Jira 8.14+ 的 PAT 用 Bearer 发送。在 **个人资料 → Personal Access Tokens** 创建。不需要 `JIRA_USER`。

```bash
export JIRA_URL=https://jira.company.com
export JIRA_AUTH=bearer
export JIRA_TOKEN=<personal-access-token>
```

### 🔑 Jira Server / Data Center — 账号密码

实例允许 Basic 认证时：

```bash
export JIRA_URL=https://jira.company.com
export JIRA_USER=your.username
export JIRA_PASSWORD='<password>'
```

> [!NOTE]
> - Jira Cloud 的 API **不接受**账号密码，请用 API Token。
> - 密码含 `$`、`!` 或空格时请用单引号。
> - 多次登录失败后 Server 可能要求验证码；浏览器登录一次即可解除，工具会提示 `authentication blocked by CAPTCHA`。
> - 能用 PAT 就优先用 PAT，可单独吊销，不必改密码。

## 💻 用法

```bash
jget <ISSUE-KEY> [flags]
```

| 参数 | 说明 |
|------|------|
| `-n <int>` | 显示最新评论条数（默认 `5`，`-1` = 全部，`0` = 不显示） |
| `-d <dir>` | 下载全部附件到指定目录 |
| `--json` | 输出原始 JSON |
| `--plain` | 关闭颜色（适合重定向或给 AI 读） |

```bash
jget PROJ-123                         # 最新 5 条评论
jget PROJ-123 -n -1                   # 全部评论
jget PROJ-123 -n 0 -d ./PROJ-123      # 下载截图等附件
jget PROJ-123 --json | jq -r '.fields.status.name'
jget PROJ-123 --plain > PROJ-123.txt
```

## 🤖 配合 AI 使用

安装后，**Claude Code** 和 **Cursor** 会自动学会调用 `jget`。你可以直接说：

> 按 PROJ-123 修这个 bug  
> 总结一下 https://your-domain.atlassian.net/browse/PROJ-123 的讨论

Agent 会拉取票内容和评论；需要看截图时，会下载附件再打开图片。

> [!TIP]
> Agent 也需要能读到 `JIRA_*` 环境变量。请写在 shell 配置里，并重启 Cursor / Claude。

## 🗑️ 卸载

```bash
jget uninstall
```

会删除可执行文件，以及 Claude Code / Cursor 里的 skill。参数与 `install` 相同（`--claude` / `--cursor` / `--bin-dir` / `--skill-only`）。

## 🩺 常见问题

| 报错 | 处理 |
|------|------|
| `missing environment variable(s)` | 配置 `JIRA_URL`、`JIRA_USER`、`JIRA_TOKEN`（或 `JIRA_PASSWORD`） |
| `authentication failed (HTTP 401/403)` | 检查账号与 Token；Server PAT 请设 `JIRA_AUTH=bearer` |
| `authentication blocked by CAPTCHA` | 浏览器登录 Jira 一次后再试 |
| `issue not found or not visible to you (HTTP 404)` | 检查票号与权限 |
| `request timed out after 10s` | 检查网络 / VPN 与 `JIRA_URL` |
| `JIRA_AUTH must be one of` | 只能是 `basic` 或 `bearer` |

## 📄 License

[Apache License 2.0](LICENSE)
