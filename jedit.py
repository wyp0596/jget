#!/usr/bin/env python3
"""jedit: comment on a Jira ticket, change its status, or change its assignee."""

import argparse
import importlib.machinery
import importlib.util
import os
import re
import shutil
import sys
import urllib.parse

__version__ = "1.0.0"

SCRIPT_MARKER = "jedit: comment on a Jira ticket"
JGET_MARKER = "jget: fetch and display a Jira ticket"
SKILL_NAME = "jedit"
EDIT_FIELDS = "summary,status,assignee"
UNASSIGN = {"none", "unassigned", "-"}
USER_SEARCH_LIMIT = 50
CLOUD_HOST_SUFFIXES = (".atlassian.net", ".jira.com")

_lib = None


def is_jget_source(path):
    """The real jget script, not a Windows launcher that only carries its marker."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return False
    return JGET_MARKER in head and "(launcher)" not in head


def find_jget():
    """jget source next to this script: jget.py (source tree, Windows) or the `jget` command (POSIX)."""
    here = os.path.dirname(os.path.realpath(__file__))
    for name in ("jget.py", "jget"):
        path = os.path.join(here, name)
        if os.path.isfile(path) and is_jget_source(path):
            return path
    return None


def load_jget():
    path = find_jget()
    if not path:
        sys.stderr.write("jedit: jget not found next to this script\n"
                         "  Reinstall with `python3 jedit.py install` from the jget source directory.\n")
        sys.exit(1)
    existing = sys.modules.get("jget")
    existing_file = getattr(existing, "__file__", None)
    if existing_file and os.path.exists(existing_file) and os.path.samefile(existing_file, path):
        mod = existing
    else:
        # SourceFileLoader also reads the extensionless `jget`; skip bytecode so ~/.local/bin stays clean.
        loader = importlib.machinery.SourceFileLoader("jget", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("jget", loader))
        sys.modules["jget"] = mod
        saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = saved
    if not hasattr(mod, "make_client") or not hasattr(getattr(mod, "Client", None), "request"):
        sys.stderr.write(f"jedit: {path} is too old for jedit; reinstall jedit\n")
        sys.exit(1)
    mod.PROG = "jedit"
    return mod


def lib():
    global _lib
    if _lib is None:
        _lib = load_jget()
    return _lib


def quote_key(key):
    return urllib.parse.quote(key, safe="")


def plain_to_adf(text):
    """Turn plain text into an Atlassian Document (REST API v3). Blank lines split paragraphs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    blocks = []
    for para in re.split(r"\n[ \t]*\n", text):
        inline = []
        for i, line in enumerate(para.split("\n")):
            if i:
                inline.append({"type": "hardBreak"})
            if line:
                inline.append({"type": "text", "text": line})
        if inline:
            blocks.append({"type": "paragraph", "content": inline})
    if not blocks:
        raise ValueError("empty comment")
    return {"type": "doc", "version": 1, "content": blocks}


def comment_document(client, text):
    """Wiki string on API v2, ADF on v3 (Jira Cloud)."""
    if client.api == "2":
        return text
    return plain_to_adf(text)


def user_idents(user):
    return [user.get("accountId"), user.get("emailAddress"), user.get("name"), user.get("key"), user.get("displayName")]


def format_user(user):
    name = user.get("displayName") or user.get("name") or user.get("accountId") or "?"
    email, username, account = user.get("emailAddress"), user.get("name"), user.get("accountId")
    bits = []
    for value in (email, username, account):
        if value and value != name and value not in bits:
            bits.append(value)
    return f"{name} ({', '.join(bits)})" if bits else name


def format_user_list(users, limit=15):
    lines = [f"  {format_user(u)}" for u in users[:limit]]
    if len(users) > limit:
        lines.append(f"  ... {len(users) - limit} more")
    return "\n".join(lines)


def pick_user(users, query):
    """The user whose name, email, username, or accountId equals query; otherwise raise with candidates.
    A partial name never assigns, even with a single search hit."""
    jget = lib()
    q = query.strip()
    qf = q.casefold()
    exact = [u for u in users if any((v or "").casefold() == qf for v in user_idents(u))]
    if len(exact) > 1:
        active = [u for u in exact if u.get("active", True)]
        if len(active) == 1:
            return active[0]
    if len(exact) == 1:
        return exact[0]
    # Jira Cloud hides emailAddress from search results, but an email query only matches that account.
    if not exact and "@" in q and len(users) == 1:
        return users[0]
    if not users:
        raise jget.JiraError(f"no assignable user matches {q!r}")
    if exact:
        head = f"assignee {q!r} matches {len(exact)} users"
    else:
        head = f"assignee {q!r} is not an exact match; candidates"
    raise jget.JiraError(f"{head}:\n{format_user_list(exact or users)}\n"
                         "pass the full display name, email, username, or accountId")


def transition_target(transition):
    if not transition:
        return ""
    return ((transition.get("to") or {}).get("name")) or ""


def format_transitions(transitions):
    if not transitions:
        return "  (no transitions available)"
    return "\n".join(
        f"  {t.get('id')}  {t.get('name') or ''} -> {transition_target(t) or '?'}" for t in transitions)


def match_transition(transitions, want):
    """Match a transition id, transition name, or target status name. Case-insensitive."""
    jget = lib()
    want = want.strip()
    folded = want.casefold()
    by_id = [t for t in transitions if str(t.get("id")) == want]
    if len(by_id) == 1:
        return by_id[0]
    by_name = [t for t in transitions if (t.get("name") or "").casefold() == folded]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise jget.JiraError(f"status {want!r} matches {len(by_name)} transitions:\n{format_transitions(by_name)}")
    by_target = [t for t in transitions if transition_target(t).casefold() == folded]
    if len(by_target) == 1:
        return by_target[0]
    if len(by_target) > 1:
        raise jget.JiraError(f"status {want!r} matches {len(by_target)} transitions:\n{format_transitions(by_target)}")
    raise jget.JiraError(f"no transition to {want!r}\navailable:\n{format_transitions(transitions)}")


def same_user(current, user):
    if not current or not user:
        return False
    for key in ("accountId", "key", "name"):
        if current.get(key) and user.get(key) and current[key] == user[key]:
            return True
    return False


def api_get(client, path, params=None):
    """GET a REST path, switching v2 -> v3 once if Cloud has retired the endpoint."""
    jget = lib()
    try:
        return client.get_json(client.api_path(path), params)
    except jget.JiraError as e:
        if e.code != 410 or client.api != "2":
            raise
        client.api = "3"
        return client.get_json(client.api_path(path), params)


def is_cloud(client):
    """Jira Cloud identifies users by accountId; Server / Data Center by username."""
    cached = getattr(client, "jedit_cloud", None)
    if cached is not None:
        return cached
    host = (urllib.parse.urlsplit(client.base_url).hostname or "").lower()
    if host.endswith(CLOUD_HOST_SUFFIXES):
        cloud = True
    else:
        # Custom domains can front Cloud too, so ask the instance.
        try:
            info = api_get(client, "/serverInfo")
            cloud = str((info or {}).get("deploymentType") or "").lower() == "cloud"
        except lib().JiraError:
            cloud = client.api == "3"
    client.jedit_cloud = cloud
    return cloud


def search_assignable(client, key, query):
    # Cloud takes `query`; Server / DC ignores it and wants `username` (which also matches name and email).
    param = "query" if is_cloud(client) else "username"
    data = api_get(client, "/user/assignable/search",
                   {"issueKey": key, "maxResults": USER_SEARCH_LIMIT, param: query})
    if isinstance(data, dict):
        data = data.get("values") or data.get("users") or []
    return [u for u in (data or []) if isinstance(u, dict)]


def lookup_account(client, account_id):
    jget = lib()
    try:
        data = api_get(client, "/user", {"accountId": account_id})
    except jget.JiraError as e:
        if e.code in (400, 404):
            return None
        raise
    if isinstance(data, dict) and (data.get("accountId") or data.get("name")):
        return data
    return None


def resolve_assignee(client, key, query):
    users = search_assignable(client, key, query)
    if not users and " " not in query.strip() and is_cloud(client):
        found = lookup_account(client, query)
        if found:
            return found
    return pick_user(users, query)


def fetch_myself(client):
    jget = lib()
    data = api_get(client, "/myself")
    if not isinstance(data, dict) or not (data.get("accountId") or data.get("name")):
        raise jget.JiraError("could not resolve the authenticated user from /myself")
    return data


def assignee_payload(client, user=None, unassign=False):
    jget = lib()
    if unassign:
        return {"accountId": None} if is_cloud(client) else {"name": None}
    if user.get("accountId"):
        return {"accountId": user["accountId"]}
    if user.get("name"):
        return {"name": user["name"]}
    raise jget.JiraError("resolved user has neither accountId nor name")


def _write(client, method, path, body):
    """POST/PUT, retrying once as API v3 after HTTP 410. `body` is a zero-arg function."""
    jget = lib()
    try:
        return client.request(method, client.api_path(path), body=body())
    except jget.JiraError as e:
        if e.code != 410 or client.api != "2":
            raise
        client.api = "3"
        return client.request(method, client.api_path(path), body=body())


def post_comment(client, key, text):
    path = f"/issue/{quote_key(key)}/comment"

    def body():
        return {"body": comment_document(client, text)}

    _, data = _write(client, "POST", path, body)
    return data or {}


def post_transition(client, key, transition, resolution=None):
    # The comment is posted separately: Jira only accepts update.comment on transitions with a screen.
    path = f"/issue/{quote_key(key)}/transitions"

    def body():
        payload = {"transition": {"id": str(transition.get("id"))}}
        if resolution:
            payload["fields"] = {"resolution": {"name": resolution}}
        return payload

    _write(client, "POST", path, body)


def transition_hint(error, resolution):
    text = str(error).lower()
    if "resolution" not in text:
        return ""
    if resolution:
        return ("\nhint: this transition may not let you set a resolution (no screen); "
                "retry without --resolution, or check the resolution name")
    return "\nhint: this transition needs a resolution; retry with --resolution <name> (often Done)"


def put_assignee(client, key, user=None, unassign=False):
    path = f"/issue/{quote_key(key)}/assignee"
    _write(client, "PUT", path, lambda: assignee_payload(client, user, unassign))


def palette(plain):
    jget = lib()
    enabled = not plain and not os.environ.get("NO_COLOR") and sys.stdout.isatty() and jget.enable_ansi()
    return jget.Palette(enabled)


def print_header(p, issue):
    fields = issue.get("fields") or {}
    print(f"{p.yellow('[' + issue['key'] + ']')} {p.bold(fields.get('summary') or '')}".rstrip())


def status_name_of(fields):
    return ((fields.get("status") or {}).get("name")) or "Unknown"


def person_name(user):
    return lib().user_name(user) or "Unassigned"


def list_transitions(client, key, p):
    jget = lib()
    try:
        issue = client.get_issue(key, EDIT_FIELDS)
        transitions = api_get(client, f"/issue/{quote_key(key)}/transitions").get("transitions") or []
    except jget.JiraError as e:
        jget.fail(f"{key}: {e}")
    issue["key"] = issue.get("key") or key
    fields = issue.get("fields") or {}
    print_header(p, issue)
    print(f"status: {status_name_of(fields)}")
    print(format_transitions(transitions))


def edit(client, key, args, p):
    """Resolve every change first, then write. A resolution failure writes nothing."""
    jget = lib()
    try:
        issue = client.get_issue(key, EDIT_FIELDS)
    except jget.JiraError as e:
        jget.fail(f"{key}: {e}")
    issue["key"] = issue.get("key") or key
    fields = issue.get("fields") or {}
    current_status = status_name_of(fields)
    current_assignee = fields.get("assignee")

    print_header(p, issue)
    comment = args.comment
    user = None
    unassign = False
    transition = None
    try:
        if args.assignee is not None:
            token = args.assignee.strip()
            if token.casefold() in UNASSIGN:
                unassign = True
            elif token.casefold() == "me":
                user = fetch_myself(client)
            else:
                user = resolve_assignee(client, key, token)
    except jget.JiraError as e:
        jget.fail(f"{key}: {e}")
    try:
        if args.status and args.status.casefold() != current_status.casefold():
            transitions = api_get(client, f"/issue/{quote_key(key)}/transitions").get("transitions") or []
            transition = match_transition(transitions, args.status)
        elif args.resolution:
            jget.fail(f"{key}: already {current_status}; --resolution was not applied")
    except jget.JiraError as e:
        jget.fail(f"{key}: {e}\ncurrent status: {current_status}")
    target_status = transition_target(transition) or (transition.get("name") if transition else "")

    if args.dry_run:
        if args.status:
            if transition:
                print(f"status: {current_status} -> {target_status}")
            else:
                print(f"status: {current_status} (unchanged)")
        if args.resolution and transition:
            print(f"resolution: {args.resolution}")
        if args.assignee is not None:
            print(_assignee_line(current_assignee, user, unassign, changed=not _assignee_unchanged(
                current_assignee, user, unassign)))
        if comment is not None:
            print("comment: would add")
        print(p.dim("(dry-run, no changes written)"))
        return

    # Order: status, assignee, comment. Each step is its own request; report what already landed on failure.
    applied = []
    step = None
    try:
        if transition:
            step = "status"
            post_transition(client, key, transition, args.resolution)
            applied.append(f"status: {current_status} -> {target_status}")
            print(applied[-1])
            if args.resolution:
                print(f"resolution: {args.resolution}")
        elif args.status:
            print(f"status: {current_status} (unchanged)")

        if args.assignee is not None:
            if _assignee_unchanged(current_assignee, user, unassign):
                print(_assignee_line(current_assignee, user, unassign, changed=False))
            else:
                step = "assignee"
                put_assignee(client, key, user, unassign)
                applied.append(_assignee_line(current_assignee, user, unassign, changed=True))
                print(applied[-1])

        if comment is not None:
            step = "comment"
            data = post_comment(client, key, comment)
            suffix = f" ({data.get('id')})" if data.get("id") else ""
            print(f"comment: added{suffix}")
    except jget.JiraError as e:
        msg = f"{key}: {step} failed: {e}"
        if step == "status":
            msg += transition_hint(e, args.resolution)
        if applied:
            msg += "\nalready applied: " + "; ".join(applied)
        jget.fail(msg)


def _assignee_unchanged(current, user, unassign):
    if unassign:
        return not current
    return same_user(current, user)


def _assignee_line(current, user, unassign, changed):
    before = person_name(current)
    if not changed:
        who = "Unassigned" if unassign or not current else before
        return f"assignee: {who} (unchanged)"
    after = "Unassigned" if unassign else person_name(user)
    return f"assignee: {before} -> {after}"


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="jedit",
        usage="jedit <ISSUE-KEY|URL> [-c TEXT] [--status NAME] [-a USER] [flags]\n"
              "       jedit install|uninstall [--claude] [--cursor] [--bin-dir DIR]",
        description="Comment on a Jira ticket, change its status, or change its assignee.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  jedit PROJ-123 -c \"Shipped in the next build.\"\n"
            "  jedit PROJ-123 --status \"In Progress\"\n"
            "  jedit PROJ-123 --status Done --resolution Done\n"
            "  jedit PROJ-123 -a \"Alex Mercer\"\n"
            "  jedit PROJ-123 -a me -c \"Taking this.\" --status \"In Progress\"\n"
            "  jedit PROJ-123 -a none\n"
            "  jedit PROJ-123 --transitions\n"
            "  jedit PROJ-123 --status Done --dry-run\n"
            "\n"
            "status matches a transition name, a target status name, or a transition id.\n"
            "assignee matches a display name, email, username, or accountId.\n"
            "  A partial name is never assigned; jedit lists the candidates instead.\n"
            "  me = the authenticated user; none / unassigned / - clears the assignee.\n"
            "changes run in order: status, assignee, comment.\n"
            "\n"
            "environment: same variables as jget (JIRA_URL, JIRA_USER, JIRA_TOKEN, JIRA_AUTH, JIRA_API)"
        ),
    )
    parser.add_argument("key", metavar="ISSUE-KEY|URL",
                        help="issue key (PROJ-123) or a Jira issue URL")
    parser.add_argument("-c", "--comment", metavar="TEXT",
                        help="comment text; '-' reads stdin")
    parser.add_argument("--status", metavar="NAME",
                        help="transition name, target status name, or transition id")
    parser.add_argument("--resolution", metavar="NAME",
                        help="resolution name when the transition requires one (for example Done)")
    parser.add_argument("-a", "--assignee", metavar="USER",
                        help="display name, email, username, accountId, 'me', or 'none'")
    parser.add_argument("--transitions", action="store_true",
                        help="list transitions from the current status and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve users and transitions, then print the plan without writing")
    parser.add_argument("--plain", action="store_true", help="disable ANSI colors")
    parser.add_argument("--version", action="version", version=f"jedit {__version__}")
    return parser.parse_args(argv)


def prepare(args):
    """Validate the flag combination and read a stdin comment. No network."""
    jget = lib()
    editing = args.status or args.assignee is not None or args.comment is not None or args.resolution
    if args.transitions and editing:
        jget.fail("--transitions only lists transitions; remove -c, --status, --resolution, and -a")
    if args.resolution and not args.status:
        jget.fail("--resolution requires --status")
    if not args.transitions and not (args.status or args.assignee is not None or args.comment is not None):
        jget.fail("nothing to do; pass -c/--comment, --status, -a/--assignee, or --transitions")
    if args.status is not None:
        args.status = args.status.strip()
        if not args.status:
            jget.fail("status is empty")
    if args.resolution is not None:
        args.resolution = args.resolution.strip()
        if not args.resolution:
            jget.fail("resolution is empty")
    if args.assignee is not None and not args.assignee.strip():
        jget.fail("assignee is empty")
    if args.comment == "-":
        args.comment = sys.stdin.read()
    if args.comment is not None and not args.comment.strip():
        jget.fail("comment is empty")
    return args


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------

def parse_manage_args(action, argv):
    parser = argparse.ArgumentParser(
        prog=f"jedit {action}",
        description=f"{action.capitalize()} the jedit command and its SKILL.md for Claude Code / Cursor.",
    )
    parser.add_argument("--claude", action="store_true", help="only Claude Code (~/.claude/skills)")
    parser.add_argument("--cursor", action="store_true", help="only Cursor (~/.cursor/skills)")
    parser.add_argument("--bin-dir", default=os.path.join("~", ".local", "bin"),
                        help="directory for the jedit executable (default: ~/.local/bin)")
    parser.add_argument("--skill-only", action="store_true", help="skip the executable, only handle SKILL.md")
    args = parser.parse_args(argv)
    args.bin_dir = os.path.abspath(os.path.expanduser(args.bin_dir))
    return args


def chosen_tools(args, detect):
    tools = lib().ai_tools(SKILL_NAME)
    picked = [k for k in tools if getattr(args, k)]
    if picked:
        return {k: tools[k] for k in picked}
    if detect:
        return {k: v for k, v in tools.items() if os.path.isdir(v[1])}
    return tools


def install(argv):
    jget = lib()
    args = parse_manage_args("install", argv)
    src = os.path.realpath(__file__)
    skill_src = os.path.join(os.path.dirname(src), "jedit", "SKILL.md")
    if not os.path.isfile(skill_src):
        jget.fail(f"jedit/SKILL.md not found next to {src}")

    if not args.skill_only:
        for path in jget.install_executable(src, args.bin_dir, command="jedit", marker=SCRIPT_MARKER):
            print(f"  + {path}")
        # jedit runs on the jget sitting next to it; install the same version so they never drift apart.
        for path in jget.install_executable(find_jget(), args.bin_dir):
            print(f"  + {path} (jget, used by jedit)")
        if not jget.in_path(args.bin_dir):
            print(jget.path_hint(args.bin_dir))

    tools = chosen_tools(args, detect=True)
    if not tools:
        print("  ! neither ~/.claude nor ~/.cursor exists; use --claude or --cursor to install the skill anyway")
    for label, _, skill_dir in tools.values():
        os.makedirs(skill_dir, exist_ok=True)
        dest = os.path.join(skill_dir, "SKILL.md")
        shutil.copyfile(skill_src, dest)
        print(f"  + {dest} ({label})")
    print("jedit installed. Uses the same JIRA_* environment as jget.")


def is_jedit_file(path):
    if os.path.islink(path):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return SCRIPT_MARKER in f.read(4096)
    except OSError:
        return False


def uninstall(argv):
    jget = lib()
    args = parse_manage_args("uninstall", argv)
    if not args.skill_only:
        found = False
        names = ["jedit", "jedit.py", "jedit.cmd"]
        for name in names:
            path = os.path.join(args.bin_dir, name)
            if not os.path.lexists(path):
                continue
            found = True
            if is_jedit_file(path):
                os.remove(path)
                print(f"  - {path}")
            else:
                print(f"  ! {path} does not look like jedit, left untouched")
        if not found:
            print(f"  = {os.path.join(args.bin_dir, 'jedit')} (not installed)")
        if any(os.path.lexists(os.path.join(args.bin_dir, n)) for n in ("jget", "jget.py")):
            print("  = jget left installed (`jget uninstall` removes it)")

    for label, _, skill_dir in chosen_tools(args, detect=False).values():
        skill_file = os.path.join(skill_dir, "SKILL.md")
        if os.path.exists(skill_file):
            os.remove(skill_file)
            print(f"  - {skill_file} ({label})")
        else:
            print(f"  = {skill_file} ({label}, not installed)")
        if os.path.isdir(skill_dir) and not os.listdir(skill_dir):
            os.rmdir(skill_dir)
    print("jedit uninstalled.")


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("install", "uninstall"):
        (install if argv[0] == "install" else uninstall)(argv[1:])
        return

    args = parse_args(argv)
    jget = lib()
    jget.setup_stdio()
    args = prepare(args)
    key, client = jget.make_client(args.key)
    p = palette(args.plain)
    if args.transitions:
        list_transitions(client, key, p)
    else:
        edit(client, key, args, p)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
