#!/usr/bin/env python3
"""jget: fetch and display a Jira ticket's description and comments in the terminal."""

import argparse
import base64
import http.client
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

__version__ = "1.1.0"

LINE_WIDTH = 80
TIMEOUT = 10
PAGE_SIZE = 100
USER_LOOKUP_CHUNK = 50
USER_AGENT = f"jget/{__version__}"
IS_WINDOWS = os.name == "nt"
ISSUE_FIELDS = ("summary,status,assignee,reporter,issuetype,priority,labels,components,fixVersions,"
                "created,updated,parent,subtasks,issuelinks,description,comment,attachment")

ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
EMBED_RE = re.compile(r"!([^!|\s][^!|\n]*\.([A-Za-z0-9]{2,5}))(\|[^!\n]*)?!")
# Jira wiki markup that is noise (or unreadable) in a terminal / for an AI agent.
COLOR_RE = re.compile(r"\{color(?::[^}]*)?\}")
MENTION_RE = re.compile(r"\[~(accountid:)?([^\]\s]+)\]")
ATTACH_REF_RE = re.compile(r"\[\^([^\]|]+)(?:\|[^\]]*)?\]")
LINK_RE = re.compile(r"\[(?:([^\]|]*)\|)?((?:https?://|mailto:|/|#)[^\]|\s]+)(?:\|[^\]]*)?\]")
MEDIA_KINDS = {
    **dict.fromkeys(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "heic"], "image"),
    **dict.fromkeys(["mp4", "mov", "webm", "avi", "mkv", "m4v"], "video"),
}
UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class JiraError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def fail(msg):
    print(f"jget: {msg}", file=sys.stderr)
    sys.exit(1)


def setup_stdio():
    for stream in (sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        # Windows pipes/files default to the ANSI code page, which can't encode most non-Latin text.
        if IS_WINDOWS and not stream.isatty() and not os.environ.get("PYTHONIOENCODING"):
            stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            stream.reconfigure(errors="replace")


def enable_ansi():
    """Return True if the terminal can render ANSI escape codes."""
    if not IS_WINDOWS:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


class Palette:
    def __init__(self, enabled):
        self.enabled = enabled

    def _wrap(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def bold(self, s):
        return self._wrap("1", s)

    def dim(self, s):
        return self._wrap("2", s)

    def cyan(self, s):
        return self._wrap("36", s)

    def green(self, s):
        return self._wrap("32", s)

    def yellow(self, s):
        return self._wrap("1;33", s)


class AuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep credentials on same-host redirects, drop them when redirected elsewhere
    (Jira Cloud sends attachment downloads to a separate media host)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        auth = req.get_header("Authorization")
        if new is not None and auth:
            src, dst = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
            if src.hostname == dst.hostname and not (src.scheme == "https" and dst.scheme == "http"):
                new.add_unredirected_header("Authorization", auth)
        return new


AUTH_TYPES = ("basic", "bearer")


class Client:
    def __init__(self, base_url, user, secret, auth_type="basic", secret_var="JIRA_TOKEN", api="2"):
        self.base_url = base_url.rstrip("/")
        self.auth_type = auth_type
        self.secret_var = secret_var
        # REST API version. v2 returns wiki markup text; v3 (Cloud only) returns ADF JSON,
        # which we render ourselves. Cloud is retiring v2 endpoints one by one (HTTP 410),
        # so get_issue() falls back to v3 automatically.
        self.api = api
        if auth_type == "bearer":
            self.auth = f"Bearer {secret}"
        else:
            cred = base64.b64encode(f"{user}:{secret}".encode()).decode()
            self.auth = f"Basic {cred}"
        self.opener = urllib.request.build_opener(AuthRedirectHandler)

    def _auth_error(self, e):
        # Jira Server/DC locks password logins behind a CAPTCHA after repeated failures.
        if "CAPTCHA" in (e.headers.get("X-Authentication-Denied-Reason") or "").upper():
            return JiraError(f"authentication blocked by CAPTCHA (HTTP {e.code}) after failed logins; "
                             f"log in to {self.base_url} once in a browser, then retry")
        if self.auth_type == "bearer":
            hint = "check JIRA_TOKEN (Personal Access Token)"
        elif self.secret_var == "JIRA_PASSWORD":
            hint = "check JIRA_USER and JIRA_PASSWORD"
        else:
            hint = "check JIRA_USER and JIRA_TOKEN (set JIRA_AUTH=bearer for Server/DC PATs)"
        return JiraError(f"authentication failed (HTTP {e.code}), {hint}")

    def _timeout_error(self):
        return JiraError(f"request timed out after {TIMEOUT}s, check your network or JIRA_URL: {self.base_url}")

    def open(self, url, accept=None):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        if accept:
            req.add_header("Accept", accept)
        req.add_unredirected_header("Authorization", self.auth)
        try:
            return self.opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise self._auth_error(e)
            if e.code == 404:
                raise JiraError("issue not found or not visible to you (HTTP 404)", 404)
            raise JiraError(f"request failed (HTTP {e.code}): {jira_error_message(e.read())}", e.code)
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise self._timeout_error()
            raise JiraError(f"network error: {e.reason}")
        except (socket.timeout, TimeoutError):
            raise self._timeout_error()
        except (OSError, http.client.HTTPException) as e:
            raise JiraError(f"network error: {e!r}")

    def get(self, path, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with self.open(url, accept="application/json") as resp:
            try:
                return resp.read()
            except (socket.timeout, TimeoutError):
                raise self._timeout_error()
            except (OSError, http.client.HTTPException) as e:
                raise JiraError(f"network error while reading response: {e!r}")

    def get_json(self, path, params=None):
        raw = self.get(path, params)
        try:
            return json.loads(raw)
        except ValueError as e:
            raise JiraError(f"invalid JSON response: {e}")

    def api_path(self, path):
        return f"/rest/api/{self.api}{path}"

    def get_issue(self, key, fields=ISSUE_FIELDS):
        path = f"/issue/{urllib.parse.quote(key, safe='')}"
        try:
            return self.get_json(self.api_path(path), {"fields": fields})
        except JiraError as e:
            if e.code != 410 or self.api != "2":
                raise
            self.api = "3"
            return self.get_json(self.api_path(path), {"fields": fields})

    def fetch_comments(self, key, start_at, total):
        """Page through the comment endpoint from start_at up to total."""
        out = []
        path = self.api_path(f"/issue/{urllib.parse.quote(key, safe='')}/comment")
        while start_at < total:
            page = self.get_json(path, {"startAt": start_at, "maxResults": PAGE_SIZE})
            comments = page.get("comments") or []
            if not comments:
                break
            out.extend(comments)
            start_at += len(comments)
        return out

    def lookup_users(self, account_ids):
        """Resolve Jira Cloud account IDs to display names. Best effort: unknown IDs are skipped."""
        names = {}
        ids = sorted(set(account_ids))
        for i in range(0, len(ids), USER_LOOKUP_CHUNK):
            chunk = ids[i:i + USER_LOOKUP_CHUNK]
            query = urllib.parse.urlencode([("accountId", a) for a in chunk] + [("maxResults", len(chunk))])
            try:
                data = self.get_json(self.api_path("/user/bulk") + "?" + query)
            except JiraError:
                continue
            for u in data.get("values") or []:
                if u.get("accountId") and u.get("displayName"):
                    names[u["accountId"]] = u["displayName"]
        return names

    def download(self, url, dest):
        with self.open(url) as resp:
            fd, tmp = tempfile.mkstemp(prefix=".jget-", dir=os.path.dirname(dest) or ".")
            try:
                with os.fdopen(fd, "wb") as f:
                    shutil.copyfileobj(resp, f)
                os.chmod(tmp, 0o644)
                os.replace(tmp, dest)
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise

    def download_all(self, attachments, directory):
        if not attachments:
            print("jget: no attachments to download", file=sys.stderr)
            return True
        os.makedirs(directory, exist_ok=True)
        used, failed = set(), 0
        for i, a in enumerate(attachments, 1):
            name = safe_filename(a.get("filename"), f"attachment-{a.get('id')}")
            if name.lower() in used:
                name = f"{a.get('id')}-{name}"
            used.add(name.lower())
            print(f"[{i}/{len(attachments)}] {name} ({human_size(a.get('size', 0))}) ... ", end="", file=sys.stderr, flush=True)
            try:
                if not a.get("content"):
                    raise JiraError("no download URL")
                self.download(a["content"], os.path.join(directory, name))
                print("ok", file=sys.stderr)
            except (JiraError, OSError, http.client.HTTPException) as e:
                print(f"failed: {e}", file=sys.stderr)
                failed += 1
        print(f"jget: saved {len(attachments) - failed}/{len(attachments)} attachment(s) to {directory}", file=sys.stderr)
        return failed == 0


def safe_filename(name, fallback):
    """Make an attachment name safe to write on any OS, and never a path outside the target dir."""
    name = UNSAFE_FILENAME_RE.sub("_", name or "").strip().rstrip(".")
    if not name or name in (".", "..") or name.split(".")[0].upper() in WINDOWS_RESERVED:
        name = fallback + (f"-{name}" if name.strip(".") else "")
    return name


def jira_error_message(body):
    try:
        data = json.loads(body)
        msgs = list(data.get("errorMessages") or [])
        msgs += [f"{k}: {v}" for k, v in (data.get("errors") or {}).items()]
        if msgs:
            return "; ".join(msgs)
    except (ValueError, AttributeError):
        pass
    s = body.decode("utf-8", "replace").strip()
    return s[:200] + "..." if len(s) > 200 else s


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def format_time(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt).astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            pass
    return s or ""


def user_name(u):
    if not u:
        return ""
    return u.get("displayName") or u.get("name") or ""


def media_tag(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return f"[{MEDIA_KINDS.get(ext, 'file')}: {name.strip()}]"


def parse_issue_arg(arg):
    """Accept an issue key or a Jira URL. Returns (KEY, base_url_or_None); (None, None) if unrecognised."""
    arg = arg.strip()
    if ISSUE_KEY_RE.match(arg):
        return arg.upper(), None
    parts = urllib.parse.urlsplit(arg)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, None
    segments = [s for s in parts.path.split("/") if s]
    key = next(iter(urllib.parse.parse_qs(parts.query).get("selectedIssue") or []), None)
    if not key:
        key = next((s for s in reversed(segments) if ISSUE_KEY_RE.match(s)), None)
    if not key or not ISSUE_KEY_RE.match(key):
        return None, None
    base = f"{parts.scheme}://{parts.netloc}"
    if "browse" in segments:  # keep a context path such as https://company.com/jira
        base += "".join("/" + s for s in segments[:segments.index("browse")])
    return key.upper(), base


def find_account_ids(texts):
    ids = set()
    for s in texts:
        if isinstance(s, str):
            ids.update(m.group(2) for m in MENTION_RE.finditer(s) if m.group(1))
    return ids


def known_users(issue, comments):
    """accountId -> displayName for users already present in the issue payload (no extra requests)."""
    f = issue.get("fields") or {}
    users = [f.get("assignee"), f.get("reporter")]
    for c in comments:
        users += [c.get("author"), c.get("updateAuthor")]
    return {u["accountId"]: u["displayName"] for u in users if u and u.get("accountId") and u.get("displayName")}


def clean_markup(s, p, names=None):
    """Make Jira wiki markup readable: drop {color}, flatten links, name @mentions, tag embeds."""
    names = names or {}

    def mention(m):
        if m.group(1):
            who = names.get(m.group(2)) or f"user:{m.group(2)}"
        else:
            who = m.group(2)
        return p.cyan(f"@{who}")

    def link(m):
        text, url = (m.group(1) or "").strip(), m.group(2)
        return url if not text or text == url else f"{text} ({url})"

    s = COLOR_RE.sub("", s)
    s = ATTACH_REF_RE.sub(lambda m: p.cyan(media_tag(m.group(1))), s)
    s = EMBED_RE.sub(lambda m: p.cyan(media_tag(m.group(1))), s)
    s = LINK_RE.sub(link, s)
    s = MENTION_RE.sub(mention, s)
    return s


# --- Atlassian Document Format (Jira Cloud REST v3) -> wiki-flavoured plain text -----------------

def adf_to_text(node):
    return _adf(node, 0).strip("\n")


def _adf_inline(nodes, depth):
    return "".join(_adf(n, depth) for n in nodes or [])


def _adf_blocks(nodes, depth, sep="\n\n"):
    return sep.join(t for t in (_adf(n, depth) for n in nodes or []) if t.strip())


def _adf_list(node, depth):
    marker = "*" if node.get("type") == "bulletList" else "#"
    items = []
    for item in node.get("content") or []:
        parts = []
        for child in item.get("content") or []:
            if child.get("type") in ("bulletList", "orderedList"):
                parts.append(_adf_list(child, depth + 1))
            else:
                text = _adf(child, depth)
                if text.strip():
                    parts.append(f"{marker * (depth + 1)} {text}")
        items.append("\n".join(parts))
    return "\n".join(items)


def _adf_table(node, depth):
    rows = []
    for row in node.get("content") or []:
        cells = row.get("content") or []
        sep = "||" if cells and all(c.get("type") == "tableHeader" for c in cells) else "|"
        texts = [" ".join(_adf_blocks(c.get("content"), depth, "\n").split("\n")).strip() or " " for c in cells]
        rows.append(sep + sep.join(texts) + sep)
    return "\n".join(rows)


def _adf(node, depth):
    if not isinstance(node, dict):
        return ""
    t, attrs, content = node.get("type"), node.get("attrs") or {}, node.get("content")
    if t == "text":
        s = node.get("text") or ""
        for m in node.get("marks") or []:
            kind = m.get("type")
            if kind == "strong":
                s = f"*{s}*"
            elif kind == "em":
                s = f"_{s}_"
            elif kind == "strike":
                s = f"-{s}-"
            elif kind == "code":
                s = f"{{{{{s}}}}}"
            elif kind == "link":
                href = (m.get("attrs") or {}).get("href") or ""
                if href and href != s:
                    s = f"{s} ({href})"
        return s
    if t == "hardBreak":
        return "\n"
    if t == "mention":
        return attrs.get("text") or f"@user:{attrs.get('id', '')}"
    if t == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""
    if t in ("inlineCard", "blockCard", "embedCard"):
        return attrs.get("url") or ""
    if t == "status":
        return f"[{attrs.get('text') or ''}]"
    if t == "date":
        try:
            return datetime.fromtimestamp(int(attrs.get("timestamp")) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            return ""
    if t in ("media", "mediaInline"):
        return media_tag(attrs.get("alt") or attrs.get("id") or "media")
    if t == "paragraph":
        return _adf_inline(content, depth)
    if t == "heading":
        return f"h{attrs.get('level') or 1}. {_adf_inline(content, depth)}"
    if t == "codeBlock":
        lang = attrs.get("language")
        return f"{{code{':' + lang if lang else ''}}}\n{_adf_inline(content, depth)}\n{{code}}"
    if t == "blockquote":
        return f"{{quote}}\n{_adf_blocks(content, depth)}\n{{quote}}"
    if t == "rule":
        return "----"
    if t in ("bulletList", "orderedList"):
        return _adf_list(node, depth)
    if t == "taskList":
        return "\n".join(
            f"[{'x' if (i.get('attrs') or {}).get('state') == 'DONE' else ' '}] {_adf_inline(i.get('content'), depth)}"
            for i in content or [])
    if t == "decisionList":
        return "\n".join(f"<> {_adf_inline(i.get('content'), depth)}" for i in content or [])
    if t == "table":
        return _adf_table(node, depth)
    if t in ("expand", "nestedExpand"):
        title = attrs.get("title")
        body = _adf_blocks(content, depth)
        return f"*{title}*\n{body}" if title else body
    if t == "panel":
        kind = attrs.get("panelType")
        body = _adf_blocks(content, depth)
        return f"({kind}) {body}" if kind and kind != "info" else body
    # doc, mediaSingle, mediaGroup, layoutSection, layoutColumn, bodiedExtension, listItem, ...
    return _adf_blocks(content, depth)


def body_text(value, p, names=None):
    """Render a description/comment body, which is wiki text (API v2) or ADF (API v3)."""
    if isinstance(value, dict):
        return adf_to_text(value)
    return clean_markup((value or "").strip(), p, names)


def issue_ref(i, p):
    f = i.get("fields") or {}
    s = f"{p.yellow('[' + (i.get('key') or '') + ']')} {f.get('summary') or ''}".rstrip()
    status = (f.get("status") or {}).get("name")
    return f"{s} {p.dim('(' + status + ')')}" if status else s


def names_of(items):
    return ", ".join(x.get("name") for x in items or [] if x.get("name"))


def render(issue, comments, total, n, p, names=None):
    heavy = p.dim("=" * LINE_WIDTH)
    light = p.dim("-" * LINE_WIDTH)
    f = issue.get("fields") or {}
    status = (f.get("status") or {}).get("name") or "Unknown"
    out = []

    def section(title):
        out.extend(["", light, p.bold(f"[ {title} ]"), light])

    out += [heavy, f"{p.yellow('[' + issue['key'] + ']')} {p.bold(f.get('summary') or '')}", heavy]
    rows = [
        ("Type", (f.get("issuetype") or {}).get("name")),
        ("Status", p.green(status)),
        ("Priority", (f.get("priority") or {}).get("name")),
        ("Assignee", user_name(f.get("assignee")) or "Unassigned"),
        ("Reporter", user_name(f.get("reporter"))),
        ("Labels", ", ".join(f.get("labels") or [])),
        ("Components", names_of(f.get("components"))),
        ("Fix Version", names_of(f.get("fixVersions"))),
        ("Created", format_time(f.get("created"))),
        ("Updated", format_time(f.get("updated"))),
        ("Parent", issue_ref(f["parent"], p) if f.get("parent") else ""),
    ]
    rows = [(k, v) for k, v in rows if v]
    width = max(len(k) for k, _ in rows) + 1
    for k, v in rows:
        out.append(f"{p.bold((k + ':').ljust(width))} {v}")

    subtasks = f.get("subtasks") or []
    if subtasks:
        section(f"Subtasks ({len(subtasks)})")
        out += [f"  {issue_ref(s, p)}" for s in subtasks]

    links = f.get("issuelinks") or []
    if links:
        section(f"Linked issues ({len(links)})")
        for link in links:
            kind = link.get("type") or {}
            if link.get("outwardIssue"):
                out.append(f"  {kind.get('outward') or 'links to'} {issue_ref(link['outwardIssue'], p)}")
            elif link.get("inwardIssue"):
                out.append(f"  {kind.get('inward') or 'linked from'} {issue_ref(link['inwardIssue'], p)}")

    section("Description")
    desc = body_text(f.get("description"), p, names)
    out.append(desc if desc else p.dim("(no description)"))

    attachments = f.get("attachment") or []
    if attachments:
        section(f"Attachments ({len(attachments)})")
        for i, a in enumerate(attachments, 1):
            out.append(f"{p.yellow(f'[{i}]')} {a.get('filename')} {p.dim('(' + human_size(a.get('size', 0)) + ')')}")
            out.append("    " + p.dim(a.get("content") or ""))

    if n != 0:
        if total == 0:
            section("Comments (none)")
        elif len(comments) == total:
            section(f"Comments (all {total})")
        else:
            section(f"Comments (latest {len(comments)} of {total})")
        for i, c in enumerate(comments, 1):
            if i > 1:
                out.append("")
            author = user_name(c.get("author")) or "Anonymous"
            out.append(f"{p.yellow(f'[{i}]')} {p.cyan(author)} {p.dim('(' + format_time(c.get('created')) + ')')}")
            out.append(body_text(c.get("body"), p, names))

    print("\n".join(out))


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="jget",
        usage="jget <ISSUE-KEY|URL> [flags]\n       jget install|uninstall [--claude] [--cursor] [--bin-dir DIR]",
        description="Show a Jira ticket's description and comments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "commands:\n"
            "  install     copy jget to ~/.local/bin and add its skill to Claude Code / Cursor\n"
            "  uninstall   remove the executable and the skill (see `jget install -h`)\n"
            "\n"
            "environment:\n"
            "  JIRA_URL       Jira base URL, e.g. https://your-domain.atlassian.net\n"
            "  JIRA_USER      login email or username (not needed with JIRA_AUTH=bearer)\n"
            "  JIRA_TOKEN     API token (Cloud) or Personal Access Token (Server/DC)\n"
            "  JIRA_PASSWORD  account password (Server/DC), used when JIRA_TOKEN is not set\n"
            "  JIRA_AUTH      basic (default, Jira Cloud) or bearer (Server/DC Personal Access Token)\n"
            "  JIRA_API       REST API version, 2 (default) or 3 (Cloud only; used automatically\n"
            "                 when v2 is retired)"
        ),
    )
    parser.add_argument("key", metavar="ISSUE-KEY|URL",
                        help="issue key (PROJ-123) or a Jira issue URL (https://.../browse/PROJ-123)")
    parser.add_argument("-n", type=int, default=5, metavar="<int>",
                        help="number of latest comments to show (default 5, -1 = all, 0 = none)")
    parser.add_argument("-d", metavar="<dir>", dest="dir",
                        help="download all attachments (images, videos, files) into <dir>")
    parser.add_argument("--json", action="store_true",
                        help="print the raw issue JSON with all comments (for piping into jq)")
    parser.add_argument("--plain", action="store_true", help="disable ANSI colors")
    parser.add_argument("--version", action="version", version=f"jget {__version__}")
    args = parser.parse_args(argv)
    if args.n < -1:
        parser.error("-n must be >= -1")
    return args


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------

SCRIPT_MARKER = "jget: fetch and display a Jira ticket"
SKILL_NAME = "jget"


def ai_tools():
    """Map of supported AI tools to (label, config dir, skill dir)."""
    home = os.path.expanduser("~")
    claude_home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    cursor_home = os.path.join(home, ".cursor")
    return {
        "claude": ("Claude Code", claude_home, os.path.join(claude_home, "skills", SKILL_NAME)),
        "cursor": ("Cursor", cursor_home, os.path.join(cursor_home, "skills", SKILL_NAME)),
    }


def parse_manage_args(action, argv):
    parser = argparse.ArgumentParser(
        prog=f"jget {action}",
        description=f"{action.capitalize()} the jget command and its SKILL.md for Claude Code / Cursor.",
    )
    parser.add_argument("--claude", action="store_true", help="only Claude Code (~/.claude/skills)")
    parser.add_argument("--cursor", action="store_true", help="only Cursor (~/.cursor/skills)")
    parser.add_argument("--bin-dir", default=os.path.join("~", ".local", "bin"),
                        help="directory for the jget executable (default: ~/.local/bin)")
    parser.add_argument("--skill-only", action="store_true", help="skip the executable, only handle SKILL.md")
    args = parser.parse_args(argv)
    args.bin_dir = os.path.abspath(os.path.expanduser(args.bin_dir))
    return args


def selected_tools(args, detect):
    tools = ai_tools()
    chosen = [k for k in tools if getattr(args, k)]
    if chosen:
        return {k: tools[k] for k in chosen}
    if detect:
        return {k: v for k, v in tools.items() if os.path.isdir(v[1])}
    return tools


def write_file(path, content, mode=0o644):
    if os.path.islink(path):
        os.remove(path)
    with open(path, "w", encoding="utf-8", newline="\r\n" if path.endswith(".cmd") else "\n") as f:
        f.write(content)
    os.chmod(path, mode)


def install_executable(src, bin_dir):
    """Copy the script into bin_dir as a `jget` command. Returns the installed paths."""
    os.makedirs(bin_dir, exist_ok=True)
    if not IS_WINDOWS:
        dest = os.path.join(bin_dir, "jget")
        if os.path.exists(dest) and os.path.samefile(src, dest):
            return [dest]
        if os.path.islink(dest):
            os.remove(dest)
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o755)
        return [dest]

    # Windows can't run extensionless scripts: keep jget.py and add launchers for
    # cmd/PowerShell (jget.cmd) and Git Bash (jget).
    py_dest = os.path.join(bin_dir, "jget.py")
    if not (os.path.exists(py_dest) and os.path.samefile(src, py_dest)):
        shutil.copyfile(src, py_dest)
    python = sys.executable
    write_file(os.path.join(bin_dir, "jget.cmd"),
               f'@rem {SCRIPT_MARKER} (launcher)\n@"{python}" "%~dp0jget.py" %* & exit /b\n')
    write_file(os.path.join(bin_dir, "jget"),
               f'#!/bin/sh\n# {SCRIPT_MARKER} (launcher)\n'
               f'exec "{python.replace(os.sep, "/")}" "$(dirname "$0")/jget.py" "$@"\n', 0o755)
    return [py_dest, os.path.join(bin_dir, "jget.cmd"), os.path.join(bin_dir, "jget")]


def path_hint(bin_dir):
    if IS_WINDOWS:
        return ("  ! {0} is not in PATH; run this in PowerShell, then open a new terminal:\n"
                "      [Environment]::SetEnvironmentVariable('Path', '{0};' + "
                "[Environment]::GetEnvironmentVariable('Path', 'User'), 'User')").format(bin_dir)
    shell = os.path.basename(os.environ.get("SHELL", ""))
    if shell == "fish":
        return f"  ! {bin_dir} is not in PATH; run:\n      fish_add_path {bin_dir}"
    rc = {"zsh": "~/.zshrc", "bash": "~/.bashrc"}.get(shell, "your shell rc file")
    return f"  ! {bin_dir} is not in PATH; add this to {rc}:\n      export PATH=\"{bin_dir}:$PATH\""


def in_path(directory):
    norm = lambda p: os.path.normcase(os.path.realpath(os.path.expanduser(p)))
    return norm(directory) in {norm(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p}


def shell_rc_hint():
    shell = os.path.basename(os.environ.get("SHELL", ""))
    return {"zsh": "~/.zshrc", "bash": "~/.bashrc", "fish": "~/.config/fish/config.fish"}.get(
        shell, "your shell startup file (e.g. ~/.zshrc)"
    )


def env_configured():
    """Return (ok, missing_names) for a usable Jira config in the current process."""
    url = os.environ.get("JIRA_URL", "").strip()
    user = os.environ.get("JIRA_USER", "").strip()
    token = os.environ.get("JIRA_TOKEN", "").strip()
    password = os.environ.get("JIRA_PASSWORD", "")
    auth = (os.environ.get("JIRA_AUTH", "").strip().lower() or "basic")
    missing = []
    if not url:
        missing.append("JIRA_URL")
    if auth == "bearer":
        if not token:
            missing.append("JIRA_TOKEN")
    else:
        if not user:
            missing.append("JIRA_USER")
        if not token and not password:
            missing.append("JIRA_TOKEN")
    return not missing, missing


def print_setup_guide():
    """Guide the user to configure credentials after install."""
    print()
    ok, missing = env_configured()
    if ok:
        print("Jira credentials look set in this shell.")
        print("Try:  jget PROJ-123")
        print("(If Cursor / Claude was already open, restart it so it picks up the env.)")
        return

    print("Next: configure Jira credentials (still missing: " + ", ".join(missing) + ")")
    print()
    if IS_WINDOWS:
        print("1) Get a token")
        print("   Jira Cloud:  https://id.atlassian.com/manage-profile/security/api-tokens")
        print("                Create API token → copy it (shown once)")
        print("   Jira Server: avatar → Profile → Personal Access Tokens")
        print()
        print("2) Save them for new terminals (PowerShell):")
        print('     setx JIRA_URL "https://your-domain.atlassian.net"')
        print('     setx JIRA_USER "you@example.com"')
        print('     setx JIRA_TOKEN "<paste-token-here>"')
        print("   Server/DC PAT instead:")
        print('     setx JIRA_URL "https://jira.company.com"')
        print('     setx JIRA_AUTH "bearer"')
        print('     setx JIRA_TOKEN "<paste-pat-here>"')
        print()
        print("3) Close this terminal and open a new one, then run:  jget PROJ-123")
    else:
        rc = shell_rc_hint()
        print("1) Get a token")
        print("   Jira Cloud:  https://id.atlassian.com/manage-profile/security/api-tokens")
        print("                Create API token → copy it (shown once)")
        print("   Jira Server: avatar → Profile → Personal Access Tokens")
        print()
        print(f"2) Add these lines to {rc}:")
        print("     export JIRA_URL=https://your-domain.atlassian.net")
        print("     export JIRA_USER=you@example.com")
        print("     export JIRA_TOKEN='<paste-token-here>'")
        print("   Server/DC PAT instead:")
        print("     export JIRA_URL=https://jira.company.com")
        print("     export JIRA_AUTH=bearer")
        print("     export JIRA_TOKEN='<paste-pat-here>'")
        print()
        print(f"3) Reload the shell:  source {rc}")
        print("   then try:           jget PROJ-123")
        print("   (Restart Cursor / Claude too, so the agent sees the same env.)")
    print()
    print("Docs: https://github.com/wyp0596/jget#where-to-get-an-api-token")
    print("      https://github.com/wyp0596/jget/blob/main/README.zh-CN.md#api-token-去哪里申请")


def install(argv):
    args = parse_manage_args("install", argv)
    src = os.path.realpath(__file__)
    skill_src = os.path.join(os.path.dirname(src), "SKILL.md")
    if not os.path.isfile(skill_src):
        fail(f"SKILL.md not found next to {src}; run install from the jget source directory")

    if not args.skill_only:
        for path in install_executable(src, args.bin_dir):
            print(f"  + {path}")
        if not in_path(args.bin_dir):
            print(path_hint(args.bin_dir))

    tools = selected_tools(args, detect=True)
    if not tools:
        print("  ! neither ~/.claude nor ~/.cursor exists; use --claude or --cursor to install the skill anyway")
    for label, _, skill_dir in tools.values():
        os.makedirs(skill_dir, exist_ok=True)
        dest = os.path.join(skill_dir, "SKILL.md")
        shutil.copyfile(skill_src, dest)
        print(f"  + {dest} ({label})")
    print("jget installed.")
    print_setup_guide()


def is_jget_file(path):
    if os.path.islink(path):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return SCRIPT_MARKER in f.read(4096)
    except OSError:
        return False


def uninstall(argv):
    args = parse_manage_args("uninstall", argv)
    if not args.skill_only:
        found = False
        for name in ("jget", "jget.py", "jget.cmd"):
            path = os.path.join(args.bin_dir, name)
            if not os.path.lexists(path):
                continue
            found = True
            if is_jget_file(path):
                os.remove(path)
                print(f"  - {path}")
            else:
                print(f"  ! {path} does not look like jget, left untouched")
        if not found:
            print(f"  = {os.path.join(args.bin_dir, 'jget')} (not installed)")

    for label, _, skill_dir in selected_tools(args, detect=False).values():
        skill_file = os.path.join(skill_dir, "SKILL.md")
        if os.path.exists(skill_file):
            os.remove(skill_file)
            print(f"  - {skill_file} ({label})")
        else:
            print(f"  = {skill_file} ({label}, not installed)")
        if os.path.isdir(skill_dir) and not os.listdir(skill_dir):
            os.rmdir(skill_dir)
    print("jget uninstalled.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    setup_stdio()
    argv = sys.argv[1:]
    if argv and argv[0] in ("install", "uninstall"):
        (install if argv[0] == "install" else uninstall)(argv[1:])
        return

    args = parse_args(argv)
    key, url_base = parse_issue_arg(args.key)
    if not key:
        fail(f"not an issue key or Jira issue URL: {args.key}")

    auth_type = os.environ.get("JIRA_AUTH", "").strip().lower() or "basic"
    if auth_type not in AUTH_TYPES:
        fail(f"JIRA_AUTH must be one of: {', '.join(AUTH_TYPES)} (got: {auth_type})")
    api = os.environ.get("JIRA_API", "").strip() or "2"
    if api not in ("2", "3"):
        fail(f"JIRA_API must be 2 or 3 (got: {api})")
    env = {k: os.environ.get(k, "").strip() for k in ("JIRA_URL", "JIRA_USER", "JIRA_TOKEN")}
    if url_base:
        if not env["JIRA_URL"]:
            env["JIRA_URL"] = url_base
        elif urllib.parse.urlsplit(url_base).netloc.lower() != urllib.parse.urlsplit(env["JIRA_URL"]).netloc.lower():
            print(f"jget: warning: URL host differs from JIRA_URL, querying {env['JIRA_URL']}", file=sys.stderr)
    password = os.environ.get("JIRA_PASSWORD", "")  # not stripped: spaces may be part of it
    secret_var = "JIRA_PASSWORD" if auth_type == "basic" and not env["JIRA_TOKEN"] and password else "JIRA_TOKEN"
    secret = password if secret_var == "JIRA_PASSWORD" else env["JIRA_TOKEN"]

    missing = [k for k in ("JIRA_URL",) if not env[k]]
    if auth_type == "basic" and not env["JIRA_USER"]:
        missing.append("JIRA_USER")
    if not secret:
        missing.append("JIRA_TOKEN" if auth_type == "bearer" else "JIRA_TOKEN (or JIRA_PASSWORD)")
    if missing:
        example = [("JIRA_URL", "https://your-domain.atlassian.net")]
        if auth_type == "basic":
            example.append(("JIRA_USER", "you@example.com"))
        example.append(("JIRA_TOKEN", "<api-token>"))
        fmt = '    setx {0} "{1}"' if IS_WINDOWS else "    export {0}={1}"
        fail(f"missing environment variable(s): {', '.join(missing)}\n  Example:\n"
             + "\n".join(fmt.format(k, v) for k, v in example))
    if not re.match(r"https?://", env["JIRA_URL"], re.I):
        fail(f"JIRA_URL must start with http:// or https://, got: {env['JIRA_URL']}")

    client = Client(env["JIRA_URL"], env["JIRA_USER"], secret, auth_type, secret_var, api)
    try:
        issue = client.get_issue(key)
    except JiraError as e:
        fail(f"{key}: {e}")
    issue["key"] = issue.get("key") or key
    fields = issue.get("fields") or {}
    attachments = fields.get("attachment") or []
    cp = fields.get("comment") or {}
    comments = cp.get("comments") or []
    total = max(cp.get("total") or 0, len(comments))

    if args.json:
        # The embedded comment list is only the first page; make --json complete.
        if len(comments) < total:
            try:
                cp["comments"] = client.fetch_comments(issue["key"], 0, total)
            except JiraError as e:
                fail(f"failed to fetch comments: {e}")
        print(json.dumps(issue, indent=2, ensure_ascii=False))
    else:
        want = total if args.n == -1 else min(args.n, total)
        # The embedded comment list may be truncated to the oldest page; fetch the tail if so.
        if want > 0 and len(comments) < total:
            try:
                comments = client.fetch_comments(issue["key"], total - want, total)
            except JiraError as e:
                fail(f"failed to fetch comments: {e}")
        comments = comments[-want:] if want > 0 else []

        # Jira Cloud wiki text writes mentions as [~accountid:...]; turn them into names.
        names = known_users(issue, comments)
        unknown = find_account_ids([fields.get("description")] + [c.get("body") for c in comments]) - set(names)
        if unknown:
            names.update(client.lookup_users(unknown))

        color = not args.plain and not os.environ.get("NO_COLOR") and sys.stdout.isatty() and enable_ansi()
        render(issue, comments, total, args.n, Palette(color), names)

    if args.dir:
        sys.stdout.flush()
        if not client.download_all(attachments, args.dir):
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # Output was piped into something like `head` that closed early.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
