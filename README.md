<div align="center">

# 🎫 jget

**Read any Jira ticket from your terminal — and let your AI coding agent read it too.**

[![License](https://img.shields.io/github/license/wyp0596/jget?color=blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)](jget.py)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](#-install)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill-D97757?logo=claude&logoColor=white)](#-use-with-ai-agents)
[![Cursor](https://img.shields.io/badge/Cursor-skill-000000?logo=cursor&logoColor=white)](#-use-with-ai-agents)
[![Stars](https://img.shields.io/github/stars/wyp0596/jget?style=social)](https://github.com/wyp0596/jget/stargazers)

[Install](#-install) · [Configuration](#-configuration) · [Usage](#-usage) · [AI Agents](#-use-with-ai-agents) · [Troubleshooting](#-troubleshooting)

</div>

---

```text
$ jget PROJ-123 -n 2
================================================================================
[PROJ-123] Fix JWT validation error on the login page
================================================================================
Status:   In Progress
Assignee: Alex Mercer

--------------------------------------------------------------------------------
[ Description ]
--------------------------------------------------------------------------------
After being idle for a long time, submitting the form returns a 500 error.
Check the JWT expiry logic and return a proper 401 instead.

[image: login-error.png]

--------------------------------------------------------------------------------
[ Attachments (1) ]
--------------------------------------------------------------------------------
[1] login-error.png (84.2 KB)
    https://your-domain.atlassian.net/rest/api/2/attachment/content/10001

--------------------------------------------------------------------------------
[ Comments (latest 2 of 5) ]
--------------------------------------------------------------------------------
[1] Zhang San (2026-03-20 14:30)
Reproduced. The frontend needs to refresh the token silently.

[2] Li Si (2026-03-21 09:15)
PR is up, waiting for CI.
```

## ✨ Features

- 📝 **Everything that matters** — summary, status, assignee, description, comments and attachments in one screen
- 💬 **Latest comments first** — show the newest N comments with `-n`, or all of them with `-n -1`
- 🖼️ **Readable embeds** — Jira markup like `!shot.png|width=300!` becomes `[image: shot.png]`
- 📎 **Attachment download** — grab every screenshot, video and file with `-d <dir>`
- 🧰 **Script friendly** — raw JSON with `--json`, colors off automatically when piped
- 🔐 **Every Jira flavor** — Jira Cloud API tokens, Server / Data Center Personal Access Tokens, or username + password
- 🤖 **AI agent skill** — one command teaches Claude Code and Cursor to read your tickets
- 🪶 **Zero dependencies** — a single Python file using only the standard library

## 🚀 Quick start

```bash
# 1. Install
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh

# 2. Configure (Jira Cloud)
export JIRA_URL=https://your-domain.atlassian.net
export JIRA_USER=you@example.com
export JIRA_TOKEN=<api-token>

# 3. Go
jget PROJ-123
```

## 📦 Install

> **Requirements:** macOS, Linux or Windows, with Python 3.8+.

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/wyp0596/jget/main/install.ps1 | iex
```

The installer:

1. Puts the `jget` command in `~/.local/bin` (Windows: `%USERPROFILE%\.local\bin`, as `jget.py` plus a `jget.cmd` launcher)
2. Installs the agent skill for every AI tool it finds:
   - Claude Code → `~/.claude/skills/jget/SKILL.md`
   - Cursor → `~/.cursor/skills/jget/SKILL.md`

If the install directory is not on your `PATH`, it prints the exact command to add it. Re-run the installer at any time to upgrade.

<details>
<summary><b>Install options</b></summary>

<br>

| Option | Description |
|--------|-------------|
| `--claude` | Only install the skill for Claude Code |
| `--cursor` | Only install the skill for Cursor |
| `--bin-dir DIR` | Where to put the executable (default `~/.local/bin`) |
| `--skill-only` | Only install the skill, skip the executable |

On macOS / Linux, pass them after `sh -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh -s -- --cursor
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh -s -- --bin-dir /usr/local/bin
```

On Windows, set them before running the installer:

```powershell
$env:JGET_INSTALL_ARGS = "--cursor"
irm https://raw.githubusercontent.com/wyp0596/jget/main/install.ps1 | iex
```

</details>

<details>
<summary><b>Install from source</b></summary>

<br>

```bash
git clone https://github.com/wyp0596/jget.git
cd jget
python3 jget.py install      # Windows: py jget.py install
```

</details>

## 🔧 Configuration

`jget` reads its settings from environment variables. On macOS / Linux, add them to `~/.zshrc` or `~/.bashrc`; on Windows, run `setx NAME "value"` once and open a new terminal.

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_URL` | ✅ | Base URL of your Jira instance |
| `JIRA_TOKEN` | ✅ unless `JIRA_PASSWORD` is set | API token (Jira Cloud) or Personal Access Token (Server / Data Center) |
| `JIRA_USER` | with `basic` auth | Login email (Jira Cloud) or username (Server / Data Center) |
| `JIRA_PASSWORD` | — | Account password (Server / Data Center), used only when `JIRA_TOKEN` is empty |
| `JIRA_AUTH` | — | `basic` (default) or `bearer` |

### ☁️ Jira Cloud (default)

Basic auth with your email and an API token. Create a token at <https://id.atlassian.com/manage-profile/security/api-tokens>.

```bash
export JIRA_URL=https://your-domain.atlassian.net
export JIRA_USER=you@example.com
export JIRA_TOKEN=<api-token>
```

### 🏢 Jira Server / Data Center — Personal Access Token

Personal Access Tokens (Jira 8.14+) are sent as a Bearer token. Create one in Jira under **Profile → Personal Access Tokens**. `JIRA_USER` is not needed.

```bash
export JIRA_URL=https://jira.company.com
export JIRA_AUTH=bearer
export JIRA_TOKEN=<personal-access-token>
```

### 🔑 Jira Server / Data Center — username and password

If your instance allows Basic auth, keep the default `JIRA_AUTH` and set your password:

```bash
export JIRA_URL=https://jira.company.com
export JIRA_USER=your.username
export JIRA_PASSWORD='<password>'
```

> [!NOTE]
> - Jira Cloud does not accept account passwords for the API; use an API token instead.
> - Quote the password in single quotes if it contains `$`, `!` or spaces.
> - After several failed logins Jira Server may require a CAPTCHA. Log in once in the browser to clear it.
> - Prefer a Personal Access Token where available: it can be revoked without changing your password.

## 💻 Usage

```bash
jget <ISSUE-KEY> [flags]
```

| Flag | Description |
|------|-------------|
| `-n <int>` | Number of latest comments to show (default `5`, `-1` = all, `0` = none) |
| `-d <dir>` | Download all attachments into `<dir>` |
| `--json` | Print the raw JSON response |
| `--plain` | Disable ANSI colors |

```bash
jget PROJ-123                         # latest 5 comments
jget PROJ-123 -n -1                   # all comments
jget PROJ-123 -n 0 -d ./PROJ-123      # download screenshots and other attachments
jget PROJ-123 --json | jq -r '.fields.status.name'
jget PROJ-123 --plain > PROJ-123.txt
```

## 🤖 Use with AI agents

After installing, **Claude Code** and **Cursor** know how to use `jget`. Just mention a ticket key or paste a Jira link:

> Fix the bug described in PROJ-123
>
> Summarize the discussion on https://your-domain.atlassian.net/browse/PROJ-123

The agent reads the ticket and all its comments, and downloads the screenshots when it needs to look at them.

> [!TIP]
> The agent needs the `JIRA_*` variables too. Export them in your shell rc file and restart the tool.

## 🗑️ Uninstall

```bash
jget uninstall
```

Removes the executable and the skill from Claude Code and Cursor. Accepts the same `--claude`, `--cursor`, `--bin-dir` and `--skill-only` options as install.

## 🩺 Troubleshooting

| Error | Fix |
|-------|-----|
| `missing environment variable(s)` | Export `JIRA_URL`, `JIRA_USER` and `JIRA_TOKEN` (or `JIRA_PASSWORD`) |
| `authentication failed (HTTP 401/403)` | Check your credentials; the token may have expired. For Server / Data Center PATs, set `JIRA_AUTH=bearer` |
| `authentication blocked by CAPTCHA` | Log in to Jira once in the browser, then retry |
| `issue not found or not visible to you (HTTP 404)` | Check the issue key and your permissions |
| `request timed out after 10s` | Check your network / VPN and `JIRA_URL` |
| `JIRA_AUTH must be one of` | Use `basic` or `bearer` |

## 📄 License

[Apache License 2.0](LICENSE)
