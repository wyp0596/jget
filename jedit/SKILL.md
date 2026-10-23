---
name: jedit
description: Comment on a Jira ticket, change its status, or change its assignee via the `jedit` CLI. Use when the user asks to comment, transition, close, reopen, assign, or unassign a Jira issue. Do not use jedit to read a ticket; use jget for that.
---

# jedit

Same `JIRA_*` env as jget. Always pass `--plain`. Quote a pasted Jira URL.

```bash
jedit <ISSUE-KEY|URL> --plain -c "comment text"
jedit <ISSUE-KEY> --plain --status "In Progress"
jedit <ISSUE-KEY> --plain --status Done --resolution Done
jedit <ISSUE-KEY> --plain -a "Alex Mercer"          # or an email / accountId
jedit <ISSUE-KEY> --plain -a me                     # authenticated user
jedit <ISSUE-KEY> --plain -a none                   # unassign
jedit <ISSUE-KEY> --plain -a me -c "Taking this." --status "In Progress"
jedit <ISSUE-KEY> --plain --transitions             # list, do not change
jedit <ISSUE-KEY> --plain --status Done --dry-run   # resolve only
```

`-c -` reads the comment from stdin. Changes run in order: status, assignee, comment. If a later step fails, the error lists what was `already applied`; do not re-run those parts. `--resolution` is only for transitions that require one, usually Done.

Run the command when the user asked for the change. Do not stop at `--dry-run` unless they asked to preview, or the previous attempt listed several matches.

## Matching

Status matches a transition name, a target status name, or a transition id (case-insensitive). Assignee must equal a full display name, email, username, or accountId; a partial name is never assigned. `me`, `none`, `unassigned`, and `-` are special.

If a user or transition is ambiguous or inexact, jedit writes nothing and prints the candidates. Re-run with an email, accountId, or the transition id from that list only when exactly one candidate is clearly what the user meant; otherwise ask the user.

## Setup

Same as jget. Do not invent credentials.

| | Cloud (`*.atlassian.net`) | Server / DC |
|--|---------------------------|-------------|
| Env | `JIRA_URL` + `JIRA_USER` (email) + `JIRA_TOKEN` | `JIRA_URL` + `JIRA_AUTH=bearer` + `JIRA_TOKEN` |

## Errors

| Message | Action |
|---------|--------|
| `missing environment variable(s)` | jget setup; user must restart the IDE |
| `no transition to` | Pick an id or name from the printed list |
| `matches N users` / `not an exact match` | Re-run with email or accountId from the list |
| `hint: ... resolution` | Follow the hint: add or drop `--resolution` |
| `permission denied (HTTP 403)` | User lacks that Jira permission; tell them, do not retry |
| `authentication failed` | Bad creds; Server PAT needs `JIRA_AUTH=bearer` |
| `authentication blocked by CAPTCHA` | User logs in once in browser; do not retry-loop |
| `404` / `timed out` | Wrong key/permission, or network/`JIRA_URL` |
