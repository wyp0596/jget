# jget

A tiny command-line tool to read a Jira ticket in your terminal: summary, status, assignee, description, latest comments and attachments.

It is a single Python file with zero dependencies, and ships with a skill so AI coding agents (Claude Code, Cursor) can read Jira tickets for you.

```text
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

## Features

- Shows the latest N comments (`-n`), or all of them
- Raw JSON output for scripting with `jq` (`--json`)
- Colors in the terminal, plain text automatically when piped or redirected (`--plain` to force)
- Jira image/video embeds rendered as readable placeholders like `[image: shot.png]`
- Downloads all attachments (screenshots, videos, files) with `-d <dir>`
- One-command install of the executable plus a skill for Claude Code and Cursor

## Requirements

- macOS, Linux or Windows
- Python 3.8+ (standard library only)

## Install

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/wyp0596/jget/main/install.ps1 | iex
```

This will:

1. Install the `jget` command into `~/.local/bin` (on Windows: `%USERPROFILE%\.local\bin`, as `jget.py` plus a `jget.cmd` launcher)
2. Install the agent skill for every AI tool found on your machine:
   - Claude Code: `~/.claude/skills/jget/SKILL.md`
   - Cursor: `~/.cursor/skills/jget/SKILL.md`

On macOS / Linux, options can be passed after `sh -s --`:

```bash
# Only install the skill for Cursor
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh -s -- --cursor

# Install the executable somewhere else
curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh -s -- --bin-dir /usr/local/bin
```

| Option | Description |
|--------|-------------|
| `--claude` | Only install the skill for Claude Code |
| `--cursor` | Only install the skill for Cursor |
| `--bin-dir DIR` | Where to put the executable (default `~/.local/bin`) |
| `--skill-only` | Only install the skill, skip the executable |

On Windows, set them first with `$env:JGET_INSTALL_ARGS = "--cursor"`, then run the `irm ... | iex` line.

If the install directory is not on your `PATH`, the installer prints the exact command to add it.

### Install from source

```bash
git clone https://github.com/wyp0596/jget.git
cd jget
python3 jget.py install      # Windows: py jget.py install
```

Re-running the install command upgrades to the latest version.

## Configuration

`jget` reads its settings from environment variables. On macOS / Linux, add them to `~/.zshrc` or `~/.bashrc`; on Windows, use `setx NAME "value"` once and open a new terminal.

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_URL` | yes | Base URL of your Jira instance |
| `JIRA_TOKEN` | yes, unless `JIRA_PASSWORD` is set | API token (Jira Cloud) or Personal Access Token (Server / Data Center) |
| `JIRA_PASSWORD` | no | Account password (Server / Data Center); used only when `JIRA_TOKEN` is empty |
| `JIRA_USER` | with `basic` auth | Your login email (Jira Cloud) or username (Server / Data Center) |
| `JIRA_AUTH` | no | `basic` (default) or `bearer` |

### Jira Cloud (default)

Jira Cloud uses HTTP Basic auth with your email and an API token. Create a token at <https://id.atlassian.com/manage-profile/security/api-tokens>.

```bash
export JIRA_URL=https://your-domain.atlassian.net
export JIRA_USER=you@example.com
export JIRA_TOKEN=<api-token>
```

### Jira Server / Data Center with a Personal Access Token

Personal Access Tokens (Jira 8.14+) are sent as a Bearer token, so set `JIRA_AUTH=bearer`. `JIRA_USER` is not needed. Create a token in Jira under **Profile → Personal Access Tokens**.

```bash
export JIRA_URL=https://jira.company.com
export JIRA_AUTH=bearer
export JIRA_TOKEN=<personal-access-token>
```

### Jira Server / Data Center with username and password

If your instance allows Basic auth, keep the default `JIRA_AUTH` and set your password:

```bash
export JIRA_URL=https://jira.company.com
export JIRA_USER=your.username
export JIRA_PASSWORD='<password>'
```

Notes:

- Jira Cloud does not accept account passwords for the API; use an API token instead.
- Quote the password in single quotes if it contains `$`, `!` or spaces.
- After several failed logins Jira Server may require a CAPTCHA, and `jget` reports `authentication blocked by CAPTCHA`. Log in once in the browser to clear it.
- Prefer a Personal Access Token where available: it can be revoked without changing your password.

All requests go to the Jira REST API v2.

## Usage

```bash
jget <ISSUE-KEY> [flags]
```

| Flag | Description |
|------|-------------|
| `-n <int>` | Number of latest comments to show (default `5`, `-1` = all, `0` = none) |
| `-d <dir>` | Download all attachments into `<dir>` |
| `--json` | Print the raw JSON response |
| `--plain` | Disable ANSI colors |

Examples:

```bash
jget PROJ-123                         # latest 5 comments
jget PROJ-123 -n -1                   # all comments
jget PROJ-123 -n 0 -d ./PROJ-123      # download screenshots and other attachments
jget PROJ-123 --json | jq -r '.fields.status.name'
jget PROJ-123 --plain > PROJ-123.txt
```

## Using with AI agents

After installing, Claude Code and Cursor know how to use `jget`. Mention a ticket key or paste a Jira URL and the agent will read the ticket, including its comments. When screenshots matter, the agent downloads the attachments and looks at them.

Make sure the `JIRA_*` variables are available to the agent, e.g. by exporting them in your shell rc file and restarting the tool.

## Uninstall

```bash
jget uninstall
```

This removes the executable and the skill from Claude Code and Cursor. It accepts the same `--claude`, `--cursor`, `--bin-dir` and `--skill-only` options as install.

## Troubleshooting

| Error | Fix |
|-------|-----|
| `missing environment variable(s)` | Export `JIRA_URL`, `JIRA_USER` and `JIRA_TOKEN` |
| `authentication failed (HTTP 401/403)` | Check `JIRA_USER` and `JIRA_TOKEN`; the token may have expired. For Server / Data Center PATs, set `JIRA_AUTH=bearer` |
| `authentication blocked by CAPTCHA` | Log in to Jira once in the browser, then retry |
| `JIRA_AUTH must be one of` | Use `basic` or `bearer` |
| `issue not found or not visible to you (HTTP 404)` | Check the issue key and your permissions |
| `request timed out after 10s` | Check your network / VPN and `JIRA_URL` |

## License

[Apache License 2.0](LICENSE)
