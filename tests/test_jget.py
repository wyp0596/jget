"""Unit tests for jget. Run with: python3 -m unittest discover -s tests -v"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jget  # noqa: E402

PLAIN = jget.Palette(False)


def text(s):
    """Render a wiki body without colors."""
    return jget.clean_markup(s, PLAIN)


class ParseIssueArgTest(unittest.TestCase):
    def test_bare_key(self):
        self.assertEqual(jget.parse_issue_arg("PROJ-123"), ("PROJ-123", None))
        self.assertEqual(jget.parse_issue_arg("  proj-1 "), ("PROJ-1", None))
        self.assertEqual(jget.parse_issue_arg("A_B2-9"), ("A_B2-9", None))

    def test_browse_url(self):
        self.assertEqual(jget.parse_issue_arg("https://x.atlassian.net/browse/PROJ-123"),
                         ("PROJ-123", "https://x.atlassian.net"))
        self.assertEqual(jget.parse_issue_arg("https://x.atlassian.net/browse/PROJ-123?focusedCommentId=7#c7"),
                         ("PROJ-123", "https://x.atlassian.net"))

    def test_server_context_path(self):
        self.assertEqual(jget.parse_issue_arg("https://company.com/jira/browse/OPS-4"),
                         ("OPS-4", "https://company.com/jira"))

    def test_board_selected_issue(self):
        url = "https://x.atlassian.net/jira/software/c/projects/PROJ/boards/1?selectedIssue=PROJ-9"
        self.assertEqual(jget.parse_issue_arg(url), ("PROJ-9", "https://x.atlassian.net"))

    def test_project_issue_path(self):
        self.assertEqual(jget.parse_issue_arg("https://x.atlassian.net/projects/PROJ/issues/PROJ-3"),
                         ("PROJ-3", "https://x.atlassian.net"))

    def test_invalid(self):
        for bad in ("PROJ", "123", "https://x.atlassian.net/browse/", "https://x.atlassian.net/browse/nope",
                    "ftp://x/browse/PROJ-1", ""):
            self.assertEqual(jget.parse_issue_arg(bad), (None, None), bad)


class CleanMarkupTest(unittest.TestCase):
    def test_color_removed(self):
        self.assertEqual(text("{color:#4c9aff}*Select HDR*{color} and {color:red}x{color}"), "*Select HDR* and x")

    def test_smart_link_dedup(self):
        self.assertEqual(text("[https://a.io/x|https://a.io/x|smart-link]"), "https://a.io/x")

    def test_link_with_text(self):
        self.assertEqual(text("see [the docs|https://a.io/docs] now"), "see the docs (https://a.io/docs) now")
        self.assertEqual(text("[https://a.io]"), "https://a.io")
        self.assertEqual(text("[mail|mailto:a@b.c]"), "mail (mailto:a@b.c)")

    def test_non_link_brackets_untouched(self):
        self.assertEqual(text("[1] step one [Optional|Required] |a|b|"), "[1] step one [Optional|Required] |a|b|")

    def test_attachment_ref(self):
        self.assertEqual(text("[^report (1).xlsx]"), "[file: report (1).xlsx]")
        self.assertEqual(text("[^shot.png|thumbnail]"), "[image: shot.png]")

    def test_embeds(self):
        self.assertEqual(text("!shot.png|width=300!"), "[image: shot.png]")
        self.assertEqual(text("!demo.mov!"), "[video: demo.mov]")
        self.assertEqual(text("!data.csv!"), "[file: data.csv]")

    def test_mentions(self):
        names = {"557058:abc-def": "Zhang San"}
        self.assertEqual(jget.clean_markup("[~accountid:557058:abc-def] hi [~jdoe]", PLAIN, names),
                         "@Zhang San hi @jdoe")
        self.assertEqual(jget.clean_markup("[~accountid:unknown1]", PLAIN, names), "@user:unknown1")

    def test_find_account_ids(self):
        ids = jget.find_account_ids(["[~accountid:a] [~bob]", None, {"type": "doc"}, "[~accountid:b] [~accountid:a]"])
        self.assertEqual(ids, {"a", "b"})

    def test_known_users(self):
        issue = {"fields": {"assignee": {"accountId": "a", "displayName": "A"},
                            "reporter": {"accountId": "r", "displayName": "R"}}}
        comments = [{"author": {"accountId": "c", "displayName": "C"}, "updateAuthor": {"name": "server-user"}}]
        self.assertEqual(jget.known_users(issue, comments), {"a": "A", "r": "R", "c": "C"})


class AdfTest(unittest.TestCase):
    @staticmethod
    def t(s, *marks):
        return {"type": "text", "text": s, "marks": [{"type": m} for m in marks]}

    def test_paragraphs_and_marks(self):
        doc = {"type": "doc", "content": [
            {"type": "paragraph", "content": [self.t("Hello "), self.t("bold", "strong"), self.t(" "), self.t("code", "code")]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "site", "marks": [{"type": "link", "attrs": {"href": "https://a.io"}}]},
                {"type": "hardBreak"}, self.t("next")]},
        ]}
        self.assertEqual(jget.adf_to_text(doc), "Hello *bold* {{code}}\n\nsite (https://a.io)\nnext")

    def test_lists_nested(self):
        doc = {"type": "bulletList", "content": [
            {"type": "listItem", "content": [
                {"type": "paragraph", "content": [self.t("one")]},
                {"type": "orderedList", "content": [
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [self.t("sub")]}]}]}]},
            {"type": "listItem", "content": [{"type": "paragraph", "content": [self.t("two")]}]},
        ]}
        self.assertEqual(jget.adf_to_text(doc), "* one\n## sub\n* two")

    def test_heading_code_quote_rule(self):
        doc = {"type": "doc", "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [self.t("Title")]},
            {"type": "codeBlock", "attrs": {"language": "python"}, "content": [self.t("print(1)")]},
            {"type": "blockquote", "content": [{"type": "paragraph", "content": [self.t("quoted")]}]},
            {"type": "rule"},
        ]}
        self.assertEqual(jget.adf_to_text(doc),
                         "h2. Title\n\n{code:python}\nprint(1)\n{code}\n\n{quote}\nquoted\n{quote}\n\n----")

    def test_table(self):
        cell = lambda kind, s: {"type": kind, "content": [{"type": "paragraph", "content": [self.t(s)]}]}
        doc = {"type": "table", "content": [
            {"type": "tableRow", "content": [cell("tableHeader", "A"), cell("tableHeader", "B")]},
            {"type": "tableRow", "content": [cell("tableCell", "1"), cell("tableCell", "")]},
        ]}
        self.assertEqual(jget.adf_to_text(doc), "||A||B||\n|1| |")

    def test_inline_nodes(self):
        doc = {"type": "paragraph", "content": [
            {"type": "mention", "attrs": {"id": "x", "text": "@Li Si"}}, self.t(" "),
            {"type": "emoji", "attrs": {"shortName": ":smile:", "text": "😄"}}, self.t(" "),
            {"type": "status", "attrs": {"text": "DONE"}}, self.t(" "),
            {"type": "inlineCard", "attrs": {"url": "https://a.io/p"}}, self.t(" "),
            {"type": "date", "attrs": {"timestamp": "1700000000000"}},
        ]}
        self.assertEqual(jget.adf_to_text(doc), "@Li Si 😄 [DONE] https://a.io/p 2023-11-14")

    def test_media_and_tasks(self):
        doc = {"type": "doc", "content": [
            {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"type": "file", "id": "u1", "alt": "shot.png"}}]},
            {"type": "mediaGroup", "content": [{"type": "media", "attrs": {"type": "file", "id": "u2"}}]},
            {"type": "taskList", "content": [
                {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [self.t("done")]},
                {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [self.t("todo")]}]},
            {"type": "expand", "attrs": {"title": "More"}, "content": [{"type": "paragraph", "content": [self.t("inside")]}]},
            {"type": "panel", "attrs": {"panelType": "warning"}, "content": [{"type": "paragraph", "content": [self.t("careful")]}]},
        ]}
        self.assertEqual(jget.adf_to_text(doc),
                         "[image: shot.png]\n\n[file: u2]\n\n[x] done\n[ ] todo\n\n*More*\ninside\n\n(warning) careful")

    def test_unknown_node_recurses(self):
        doc = {"type": "somethingNew", "content": [{"type": "paragraph", "content": [self.t("kept")]}]}
        self.assertEqual(jget.adf_to_text(doc), "kept")
        self.assertEqual(jget.adf_to_text(None), "")

    def test_body_text_dispatch(self):
        self.assertEqual(jget.body_text({"type": "paragraph", "content": [self.t("adf")]}, PLAIN), "adf")
        self.assertEqual(jget.body_text("  {color:red}wiki{color} ", PLAIN), "wiki")
        self.assertEqual(jget.body_text(None, PLAIN), "")


class HelpersTest(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(jget.safe_filename("a/b\\c:d.png", "fb"), "a_b_c_d.png")
        self.assertEqual(jget.safe_filename("..", "fb"), "fb")
        self.assertEqual(jget.safe_filename("", "fb"), "fb")
        self.assertEqual(jget.safe_filename("CON.txt", "fb"), "fb-CON.txt")
        self.assertEqual(jget.safe_filename("  name. ", "fb"), "name")

    def test_human_size(self):
        self.assertEqual(jget.human_size(0), "0 B")
        self.assertEqual(jget.human_size(1023), "1023 B")
        self.assertEqual(jget.human_size(1536), "1.5 KB")
        self.assertEqual(jget.human_size(5 * 1024 ** 3), "5.0 GB")

    def test_format_time(self):
        self.assertRegex(jget.format_time("2026-03-20T14:30:00.000+0000"), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(jget.format_time("garbage"), "garbage")
        self.assertEqual(jget.format_time(None), "")

    def test_jira_error_message(self):
        self.assertEqual(jget.jira_error_message(b'{"errorMessages":["a"],"errors":{"f":"b"}}'), "a; f: b")
        self.assertEqual(jget.jira_error_message(b"<html>oops</html>"), "<html>oops</html>")

    def test_version_flag(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            jget.parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), f"jget {jget.__version__}")
        self.assertEqual(jget.USER_AGENT, f"jget/{jget.__version__}")


class FakeClient(jget.Client):
    """Client whose HTTP layer is a dict of path -> response (or JiraError)."""

    def __init__(self, routes):
        super().__init__("https://x.atlassian.net", "u", "t")
        self.routes = routes
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        resp = self.routes[path]
        if isinstance(resp, Exception):
            raise resp
        return resp(params) if callable(resp) else resp


class ClientTest(unittest.TestCase):
    def test_v2_then_v3_fallback_on_410(self):
        c = FakeClient({
            "/rest/api/2/issue/P-1": jget.JiraError("gone", 410),
            "/rest/api/3/issue/P-1": {"key": "P-1", "fields": {}},
        })
        self.assertEqual(c.get_issue("P-1"), {"key": "P-1", "fields": {}})
        self.assertEqual(c.api, "3")
        self.assertEqual(c.api_path("/issue/P-1/comment"), "/rest/api/3/issue/P-1/comment")

    def test_no_fallback_on_other_errors(self):
        c = FakeClient({"/rest/api/2/issue/P-1": jget.JiraError("nope", 404)})
        with self.assertRaises(jget.JiraError):
            c.get_issue("P-1")
        self.assertEqual(c.api, "2")

    def test_fetch_comments_pages(self):
        def page(params):
            start = params["startAt"]
            return {"comments": [{"id": i} for i in range(start, min(start + jget.PAGE_SIZE, 250))]}

        c = FakeClient({"/rest/api/2/issue/P-1/comment": page})
        got = c.fetch_comments("P-1", 40, 250)
        self.assertEqual([x["id"] for x in got], list(range(40, 250)))
        self.assertEqual(len(c.calls), 3)

    def test_lookup_users_chunks_and_tolerates_failure(self):
        seen = []

        def bulk(path, params=None):
            seen.append(path)
            return {"values": [{"accountId": "a", "displayName": "A"}, {"accountId": "b"}]}

        c = FakeClient({})
        c.get_json = bulk
        ids = [f"id{i}" for i in range(jget.USER_LOOKUP_CHUNK + 1)] + ["a"]
        self.assertEqual(c.lookup_users(ids), {"a": "A"})
        self.assertEqual(len(seen), 2)
        self.assertIn("/rest/api/2/user/bulk?accountId=", seen[0])
        self.assertIn(f"maxResults={jget.USER_LOOKUP_CHUNK}", seen[0])

        c.get_json = mock.Mock(side_effect=jget.JiraError("boom", 500))
        self.assertEqual(c.lookup_users(["x"]), {})


ISSUE = {
    "key": "PROJ-123",
    "fields": {
        "summary": "Fix login",
        "issuetype": {"name": "Bug"},
        "status": {"name": "In Progress"},
        "priority": {"name": "High"},
        "assignee": {"accountId": "a1", "displayName": "Alex"},
        "reporter": {"accountId": "r1", "displayName": "Rita"},
        "labels": ["auth", "web"],
        "components": [{"name": "backend"}],
        "fixVersions": [{"name": "1.2"}],
        "created": "2026-03-20T14:30:00.000+0000",
        "updated": "2026-03-21T09:15:00.000+0000",
        "parent": {"key": "PROJ-100", "fields": {"summary": "Auth epic", "status": {"name": "Open"}}},
        "subtasks": [{"key": "PROJ-124", "fields": {"summary": "Write tests", "status": {"name": "Done"}}}],
        "issuelinks": [
            {"type": {"outward": "blocks", "inward": "is blocked by"},
             "outwardIssue": {"key": "PROJ-200", "fields": {"summary": "Release", "status": {"name": "To Do"}}}},
            {"type": {"outward": "relates to", "inward": "relates to"},
             "inwardIssue": {"key": "PROJ-50", "fields": {"summary": "Old bug"}}},
        ],
        "description": "{color:red}Broken{color} [~accountid:a1] see !err.png|width=100!",
        "attachment": [{"id": "1", "filename": "err.png", "size": 2048, "content": "https://x/att/1"}],
        "comment": {"total": 2, "comments": [
            {"author": {"displayName": "Zhang San"}, "created": "2026-03-20T14:30:00.000+0000", "body": "Repro [~jdoe]"},
            {"author": {"displayName": "Li Si"}, "created": "2026-03-21T09:15:00.000+0000",
             "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "PR up"}]}]}},
        ]},
    },
}


class RenderTest(unittest.TestCase):
    def render(self, issue, comments, total, n, names=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            jget.render(issue, comments, total, n, PLAIN, names)
        return buf.getvalue()

    def test_full_render(self):
        f = ISSUE["fields"]
        out = self.render(ISSUE, f["comment"]["comments"], 2, 5, {"a1": "Alex"})
        for line in (
            "[PROJ-123] Fix login",
            "Type:        Bug",
            "Status:      In Progress",
            "Priority:    High",
            "Assignee:    Alex",
            "Reporter:    Rita",
            "Labels:      auth, web",
            "Components:  backend",
            "Fix Version: 1.2",
            "Parent:      [PROJ-100] Auth epic (Open)",
            "[ Subtasks (1) ]",
            "  [PROJ-124] Write tests (Done)",
            "[ Linked issues (2) ]",
            "  blocks [PROJ-200] Release (To Do)",
            "  relates to [PROJ-50] Old bug",
            "Broken @Alex see [image: err.png]",
            "[ Attachments (1) ]",
            "[1] err.png (2.0 KB)",
            "[ Comments (all 2) ]",
            "[1] Zhang San",
            "Repro @jdoe",
            "[2] Li Si",
            "PR up",
        ):
            self.assertIn(line, out, line)
        self.assertNotIn("{color", out)

    def test_sparse_issue(self):
        out = self.render({"key": "X-1", "fields": {"summary": "s"}}, [], 0, 5)
        self.assertIn("Status:   Unknown", out)
        self.assertIn("Assignee: Unassigned", out)
        self.assertNotIn("Priority", out)
        self.assertNotIn("Subtasks", out)
        self.assertIn("(no description)", out)
        self.assertIn("[ Comments (none) ]", out)

    def test_comment_headings(self):
        comments = ISSUE["fields"]["comment"]["comments"]
        self.assertIn("Comments (latest 2 of 7)", self.render(ISSUE, comments, 7, 2))
        self.assertNotIn("Comments", self.render(ISSUE, [], 7, 0))


class MainTest(unittest.TestCase):
    """End-to-end through main() with the HTTP layer mocked."""

    def run_main(self, argv, routes, env=None):
        base_env = {"JIRA_URL": "https://x.atlassian.net", "JIRA_USER": "u", "JIRA_TOKEN": "t"}
        base_env.update(env or {})
        calls = []

        def fake_get_json(self, path, params=None):
            calls.append((path, params))
            resp = routes[path]
            if isinstance(resp, Exception):
                raise resp
            return resp(params) if callable(resp) else json.loads(json.dumps(resp))

        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, base_env, clear=True), \
                mock.patch.object(jget.Client, "get_json", fake_get_json), \
                mock.patch.object(sys, "argv", ["jget"] + argv), \
                redirect_stdout(buf), mock.patch("sys.stderr", err):
            code = 0
            try:
                jget.main()
            except SystemExit as e:
                code = e.code or 0
        return code, buf.getvalue(), err.getvalue(), calls

    def test_url_argument_and_render(self):
        routes = {"/rest/api/2/issue/PROJ-123": ISSUE}
        code, out, _, calls = self.run_main(["https://x.atlassian.net/browse/PROJ-123", "--plain"], routes)
        self.assertEqual(code, 0)
        self.assertIn("[PROJ-123] Fix login", out)
        self.assertEqual(calls[0][1]["fields"], jget.ISSUE_FIELDS)

    def test_url_supplies_base_when_env_missing(self):
        routes = {"/rest/api/2/issue/PROJ-123": ISSUE}
        code, out, _, _ = self.run_main(["https://corp.example.com/jira/browse/PROJ-123", "--plain"], routes,
                                        {"JIRA_URL": ""})
        self.assertEqual(code, 0)
        self.assertIn("[PROJ-123]", out)

    def test_json_includes_all_comments(self):
        issue = json.loads(json.dumps(ISSUE))
        issue["fields"]["comment"] = {"total": 3, "comments": [{"id": "1"}]}
        routes = {
            "/rest/api/2/issue/PROJ-123": issue,
            "/rest/api/2/issue/PROJ-123/comment": {"comments": [{"id": "1"}, {"id": "2"}, {"id": "3"}]},
        }
        code, out, _, _ = self.run_main(["PROJ-123", "--json"], routes)
        self.assertEqual(code, 0)
        self.assertEqual([c["id"] for c in json.loads(out)["fields"]["comment"]["comments"]], ["1", "2", "3"])

    def test_v3_fallback_and_mention_lookup(self):
        issue = json.loads(json.dumps(ISSUE))
        issue["fields"]["description"] = "[~accountid:zz9]"
        routes = {
            "/rest/api/2/issue/PROJ-123": jget.JiraError("gone", 410),
            "/rest/api/3/issue/PROJ-123": issue,
            "/rest/api/3/user/bulk?accountId=zz9&maxResults=1": {"values": [{"accountId": "zz9", "displayName": "Zed"}]},
        }
        code, out, _, calls = self.run_main(["PROJ-123", "--plain"], routes)
        self.assertEqual(code, 0)
        self.assertIn("@Zed", out)
        self.assertEqual([c[0] for c in calls][:2], ["/rest/api/2/issue/PROJ-123", "/rest/api/3/issue/PROJ-123"])

    def test_bad_argument(self):
        code, _, err, _ = self.run_main(["not-a-key"], {})
        self.assertEqual(code, 1)
        self.assertIn("not an issue key or Jira issue URL", err)

    def test_missing_env(self):
        code, _, err, _ = self.run_main(["PROJ-1"], {}, {"JIRA_URL": "", "JIRA_USER": ""})
        self.assertEqual(code, 1)
        self.assertIn("missing environment variable(s): JIRA_URL, JIRA_USER", err)

    def test_bad_api_version(self):
        code, _, err, _ = self.run_main(["PROJ-1"], {}, {"JIRA_API": "4"})
        self.assertEqual(code, 1)
        self.assertIn("JIRA_API must be 2 or 3", err)


class InstallTest(unittest.TestCase):
    def test_install_and_uninstall_roundtrip(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, "bin")
            env = {"HOME": home, "USERPROFILE": home, "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude")}
            with mock.patch.dict(os.environ, env), mock.patch.object(jget.os.path, "expanduser",
                                                                         lambda p: p.replace("~", home, 1)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    jget.install(["--bin-dir", bin_dir, "--cursor"])
                skill = os.path.join(home, ".cursor", "skills", "jget", "SKILL.md")
                self.assertTrue(os.path.isfile(skill))
                exe = os.path.join(bin_dir, "jget.py" if jget.IS_WINDOWS else "jget")
                self.assertTrue(os.path.isfile(exe))
                self.assertTrue(jget.is_jget_file(exe))
                self.assertIn("jget installed.", buf.getvalue())

                buf = io.StringIO()
                with redirect_stdout(buf):
                    jget.uninstall(["--bin-dir", bin_dir, "--cursor"])
                self.assertFalse(os.path.exists(skill))
                self.assertFalse(os.path.exists(exe))
                self.assertIn("jget uninstalled.", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
