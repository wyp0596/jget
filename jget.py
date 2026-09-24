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
from datetime import datetime

LINE_WIDTH = 80
TIMEOUT = 10
PAGE_SIZE = 100
USER_AGENT = "jget/1.0"
IS_WINDOWS = os.name == "nt"

EMBED_RE = re.compile(r"!([^!|\s][^!|\n]*\.([A-Za-z0-9]{2,5}))(\|[^!\n]*)?!")
MEDIA_KINDS = {
    **dict.fromkeys(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "heic"], "image"),
    **dict.fromkeys(["mp4", "mov", "webm", "avi", "mkv", "m4v"], "video"),
}
UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class JiraError(Exception):
    pass


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
    def __init__(self, base_url, user, secret, auth_type="basic", secret_var="JIRA_TOKEN"):
        self.base_url = base_url.rstrip("/")
        self.auth_type = auth_type
        self.secret_var = secret_var
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
                raise JiraError("issue not found or not visible to you (HTTP 404)")
            raise JiraError(f"request failed (HTTP {e.code}): {jira_error_message(e.read())}")
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

    def fetch_comments(self, key, start_at, total):
        """Page through the comment endpoint from start_at up to total."""
        out = []
        path = f"/rest/api/2/issue/{urllib.parse.quote(key, safe='')}/comment"
        while start_at < total:
            page = self.get_json(path, {"startAt": start_at, "maxResults": PAGE_SIZE})
            comments = page.get("comments") or []
            if not comments:
                break
            out.extend(comments)
            start_at += len(comments)
        return out

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


def replace_embeds(s, p):
    def sub(m):
        kind = MEDIA_KINDS.get(m.group(2).lower(), "file")
        return p.cyan(f"[{kind}: {m.group(1).strip()}]")

    return EMBED_RE.sub(sub, s)


def render(issue, comments, total, n, p):
    heavy = p.dim("=" * LINE_WIDTH)
    light = p.dim("-" * LINE_WIDTH)
    f = issue.get("fields") or {}
    status = (f.get("status") or {}).get("name") or "Unknown"
    assignee = user_name(f.get("assignee")) or "Unassigned"
    out = []

    def section(title):
        out.extend(["", light, p.bold(f"[ {title} ]"), light])

    out += [heavy, f"{p.yellow('[' + issue['key'] + ']')} {p.bold(f.get('summary') or '')}", heavy]
    out.append(f"{p.bold('Status:')}   {p.green(status)}")
    out.append(f"{p.bold('Assignee:')} {assignee}")

    section("Description")
    desc = (f.get("description") or "").strip()
    out.append(replace_embeds(desc, p) if desc else p.dim("(no description)"))

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
            out.append(replace_embeds((c.get("body") or "").strip(), p))

    print("\n".join(out))


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="jget",
        usage="jget <ISSUE-KEY> [flags]\n       jget install|uninstall [--claude] [--cursor] [--bin-dir DIR]",
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
            "  JIRA_AUTH      basic (default, Jira Cloud) or bearer (Server/DC Personal Access Token)"
        ),
    )
    parser.add_argument("key", metavar="ISSUE-KEY", help="issue key, e.g. PROJ-123")
    parser.add_argument("-n", type=int, default=5, metavar="<int>",
                        help="number of latest comments to show (default 5, -1 = all, 0 = none)")
    parser.add_argument("-d", metavar="<dir>", dest="dir",
                        help="download all attachments (images, videos, files) into <dir>")
    parser.add_argument("--json", action="store_true", help="print raw JSON (for piping into jq)")
    parser.add_argument("--plain", action="store_true", help="disable ANSI colors")
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
    key = args.key.strip()

    auth_type = os.environ.get("JIRA_AUTH", "").strip().lower() or "basic"
    if auth_type not in AUTH_TYPES:
        fail(f"JIRA_AUTH must be one of: {', '.join(AUTH_TYPES)} (got: {auth_type})")
    env = {k: os.environ.get(k, "").strip() for k in ("JIRA_URL", "JIRA_USER", "JIRA_TOKEN")}
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

    client = Client(env["JIRA_URL"], env["JIRA_USER"], secret, auth_type, secret_var)
    try:
        issue = client.get_json(
            f"/rest/api/2/issue/{urllib.parse.quote(key, safe='')}",
            {"fields": "summary,status,assignee,description,comment,attachment"},
        )
    except JiraError as e:
        fail(f"{key}: {e}")
    issue["key"] = issue.get("key") or key
    attachments = (issue.get("fields") or {}).get("attachment") or []

    if args.json:
        print(json.dumps(issue, indent=2, ensure_ascii=False))
    else:
        cp = (issue.get("fields") or {}).get("comment") or {}
        comments = cp.get("comments") or []
        total = max(cp.get("total") or 0, len(comments))

        want = total if args.n == -1 else min(args.n, total)
        # The embedded comment list may be truncated to the oldest page; fetch the tail if so.
        if want > 0 and len(comments) < total:
            try:
                comments = client.fetch_comments(issue["key"], total - want, total)
            except JiraError as e:
                fail(f"failed to fetch comments: {e}")
        comments = comments[-want:] if want > 0 else []

        color = not args.plain and not os.environ.get("NO_COLOR") and sys.stdout.isatty() and enable_ansi()
        render(issue, comments, total, args.n, Palette(color))

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
