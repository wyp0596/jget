---
name: jget
description: Fetch a Jira ticket's summary, status, assignee, description, comments and attachments from the terminal with the `jget` CLI. Use when the user mentions a Jira issue key (e.g. PROJ-123), pastes a Jira ticket URL, or asks to read, summarize or work on a Jira ticket.
---

# jget

`jget` is a CLI on the user's PATH that reads a Jira ticket via the REST API.

## Usage

```bash
jget <ISSUE-KEY> --plain -n -1      # full ticket with all comments
jget <ISSUE-KEY> --plain            # latest 5 comments only
jget <ISSUE-KEY> --json | jq '.fields.status.name'
jget <ISSUE-KEY> --plain -n 0 -d <tmp-dir>/<ISSUE-KEY>   # download attachments
```

- Always pass `--plain` so the output has no ANSI color codes.
- If the user gives a URL like `https://your-domain.atlassian.net/browse/PROJ-123`, the key is `PROJ-123`.
- Use `-n -1` when you need the full discussion; comments are listed oldest to newest.
- `--json` returns the raw API response (fields: `summary`, `status`, `assignee`, `description`, `comment`, `attachment`).

## Images and attachments

In the description and comments, Jira embeds appear as `[image: name.png]`, `[video: name.mp4]` or `[file: name]`, and the `[ Attachments ]` section lists every file.
When a screenshot matters for the task, download with `-d` into a temp directory (e.g. `/tmp/jget/<ISSUE-KEY>`, or `$env:TEMP\jget\<ISSUE-KEY>` on Windows) rather than the user's project, then open the image files with your file-reading tool to view them.

## Errors

| Message | Meaning |
|---------|---------|
| `missing environment variable(s)` | Ask the user to export `JIRA_URL`, `JIRA_USER` and `JIRA_TOKEN` (or `JIRA_PASSWORD`) |
| `authentication failed (HTTP 401/403)` | Credentials are wrong or expired; Server/DC Personal Access Tokens need `JIRA_AUTH=bearer` |
| `authentication blocked by CAPTCHA` | Ask the user to log in to Jira in a browser once; do not retry in a loop |
| `issue not found or not visible to you (HTTP 404)` | Wrong key or no permission |
| `request timed out` | Network/VPN issue or wrong `JIRA_URL` |

Never print or echo the value of `JIRA_TOKEN` or `JIRA_PASSWORD`.
