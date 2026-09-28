---
name: jget
description: Fetch a Jira ticket via the `jget` CLI (summary, status, assignee, description, comments, attachments). Use when the user mentions an issue key, Jira URL, or asks to read a ticket; also when jget reports missing JIRA_* env vars.
---

# jget

```bash
jget <ISSUE-KEY> --plain -n -1
jget <ISSUE-KEY> --plain -n 0 -d <tmp>/<ISSUE-KEY>   # then open images if needed
```

Always use `--plain`. From a browse URL, the key is the last path segment (e.g. `PROJ-123`).

## Setup (only if env missing)

Do not invent credentials. Ask Cloud vs Server, then have the user set vars and **restart the IDE** so the agent inherits them.

| | Cloud (`*.atlassian.net`) | Server / DC |
|--|---------------------------|-------------|
| Token | https://id.atlassian.com/manage-profile/security/api-tokens | Profile → Personal Access Tokens |
| Env | `JIRA_URL` + `JIRA_USER` (email) + `JIRA_TOKEN` | `JIRA_URL` + `JIRA_AUTH=bearer` + `JIRA_TOKEN` |

Persist in `~/.zshrc` / `~/.bashrc` (or Windows `setx`), never echo token/password values.

## Errors

| Message | Action |
|---------|--------|
| `missing environment variable(s)` | Setup above |
| `authentication failed` | Bad creds; Server PAT needs `JIRA_AUTH=bearer` |
| `authentication blocked by CAPTCHA` | User logs in once in browser; do not retry-loop |
| `404` / `timed out` | Wrong key/permission, or network/`JIRA_URL` |
