"""Unit tests for jedit. Run with: python3 -m unittest discover -s tests -v"""

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
import jedit  # noqa: E402


USERS = [
    {"accountId": "a1", "displayName": "Alex Mercer", "emailAddress": "alex@example.com", "active": True},
    {"accountId": "a2", "displayName": "Alex Kim", "emailAddress": "ak@example.com", "active": True},
]

TRANSITIONS = {"transitions": [
    {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}},
    {"id": "21", "name": "Resolve", "to": {"name": "Done"}},
    {"id": "31", "name": "Won't Do", "to": {"name": "Done"}},
]}


def issue(status="To Do", assignee=None, summary="Fix login"):
    return {"key": "PROJ-1", "fields": {"summary": summary, "status": {"name": status}, "assignee": assignee}}


class PureTest(unittest.TestCase):
    def test_plain_to_adf(self):
        doc = jedit.plain_to_adf("hello\nworld\n\nnext")
        self.assertEqual(doc["type"], "doc")
        self.assertEqual(doc["content"], [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "hello"},
                {"type": "hardBreak"},
                {"type": "text", "text": "world"},
            ]},
            {"type": "paragraph", "content": [{"type": "text", "text": "next"}]},
        ])
        with self.assertRaises(ValueError):
            jedit.plain_to_adf("\n\n")

    def test_pick_user(self):
        self.assertEqual(jedit.pick_user(USERS, "Alex Mercer")["accountId"], "a1")
        self.assertEqual(jedit.pick_user(USERS, "ALEX@example.com")["accountId"], "a1")
        with self.assertRaises(jget.JiraError) as ambiguous:
            jedit.pick_user(USERS, "Alex")
        self.assertIn("Alex Mercer", str(ambiguous.exception))
        self.assertIn("ak@example.com", str(ambiguous.exception))
        with self.assertRaises(jget.JiraError):
            jedit.pick_user([], "nobody")

    def test_pick_user_never_assigns_a_partial_name(self):
        with self.assertRaises(jget.JiraError) as partial:
            jedit.pick_user([USERS[0]], "Al")
        self.assertIn("not an exact match", str(partial.exception))
        self.assertIn("Alex Mercer", str(partial.exception))

    def test_pick_user_email_query_with_hidden_email(self):
        hidden = {"accountId": "a9", "displayName": "Private Person"}
        self.assertEqual(jedit.pick_user([hidden], "pp@example.com")["accountId"], "a9")
        with self.assertRaises(jget.JiraError):
            jedit.pick_user([hidden, USERS[0]], "pp@example.com")

    def test_pick_user_prefers_single_active_exact_match(self):
        inactive = dict(USERS[0], active=False, accountId="old")
        active = dict(USERS[0], active=True, accountId="new")
        self.assertEqual(jedit.pick_user([inactive, active], "Alex Mercer")["accountId"], "new")

    def test_match_transition(self):
        items = TRANSITIONS["transitions"]
        self.assertEqual(jedit.match_transition(items, "11")["name"], "Start Progress")
        self.assertEqual(jedit.match_transition(items, "start progress")["id"], "11")
        self.assertEqual(jedit.match_transition(items, "In Progress")["id"], "11")
        with self.assertRaises(jget.JiraError) as ambiguous:
            jedit.match_transition(items, "Done")
        self.assertIn("21", str(ambiguous.exception))
        self.assertIn("31", str(ambiguous.exception))
        with self.assertRaises(jget.JiraError) as missing:
            jedit.match_transition(items, "Closed")
        self.assertIn("no transition", str(missing.exception))

    def test_assignee_payload(self):
        cloud = jget.Client("https://acme.atlassian.net", "u", "t")
        server = jget.Client("https://jira.example.com", "u", "t")
        server.jedit_cloud = False
        self.assertEqual(jedit.assignee_payload(cloud, unassign=True), {"accountId": None})
        self.assertEqual(jedit.assignee_payload(server, unassign=True), {"name": None})
        self.assertEqual(jedit.assignee_payload(server, {"name": "jdoe"}), {"name": "jdoe"})
        self.assertEqual(jedit.assignee_payload(cloud, {"accountId": "a1", "name": "legacy"}), {"accountId": "a1"})

    def test_is_cloud_asks_custom_domains_once(self):
        c = jget.Client("https://jira.example.com", "u", "t")
        c.get_json = mock.Mock(return_value={"deploymentType": "Cloud"})
        self.assertTrue(jedit.is_cloud(c))
        self.assertTrue(jedit.is_cloud(c))
        c.get_json.assert_called_once_with("/rest/api/2/serverInfo", None)

        c = jget.Client("https://jira.example.com", "u", "t")
        c.get_json = mock.Mock(side_effect=jget.JiraError("nope", 500))
        self.assertFalse(jedit.is_cloud(c))

    def test_load_extensionless_jget(self):
        with tempfile.TemporaryDirectory() as d:
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(here, "jget.py"), encoding="utf-8") as f:
                source = f.read()
            with open(os.path.join(d, "jget"), "w", encoding="utf-8") as f:
                f.write(source)
            fake_jedit = os.path.join(d, "jedit")
            saved = sys.modules.pop("jget")
            try:
                with mock.patch.object(jedit, "__file__", fake_jedit):
                    mod = jedit.load_jget()
                self.assertTrue(os.path.samefile(mod.__file__, os.path.join(d, "jget")))
                self.assertEqual(mod.PROG, "jedit")
                self.assertFalse(os.path.exists(os.path.join(d, "__pycache__")))
            finally:
                sys.modules["jget"] = saved

    def test_windows_launcher_is_not_jget_source(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "jget")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"#!/bin/sh\n# {jedit.JGET_MARKER} (launcher)\nexec python jget.py\n")
            self.assertFalse(jedit.is_jget_source(path))


class MainTest(unittest.TestCase):
    def run_main(self, argv, routes, env=None, stdin=""):
        base_env = {"JIRA_URL": "https://x.atlassian.net", "JIRA_USER": "u", "JIRA_TOKEN": "t",
                    "JIRA_AUTH": "", "JIRA_API": "", "JIRA_PASSWORD": ""}
        base_env.update(env or {})
        calls = []

        def respond(method, path, params, body):
            key = (method, path)
            if key not in routes:
                raise AssertionError(f"unexpected {method} {path} {params}")
            resp = routes[key]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                resp = resp(params, body)
                if isinstance(resp, Exception):
                    raise resp
            return None if resp is None else json.loads(json.dumps(resp))

        def fake_get_json(self, path, params=None):
            calls.append(("GET", path, params, None))
            return respond("GET", path, params, None)

        def fake_request(self, method, path, params=None, body=None):
            calls.append((method, path, params, body))
            return 200, respond(method, path, params, body)

        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, base_env, clear=True), \
                mock.patch.object(jget.Client, "get_json", fake_get_json), \
                mock.patch.object(jget.Client, "request", fake_request), \
                mock.patch.object(sys, "argv", ["jedit"] + argv), \
                mock.patch("sys.stdin", io.StringIO(stdin)), \
                redirect_stdout(buf), mock.patch("sys.stderr", err):
            code = 0
            try:
                jedit.main()
            except SystemExit as e:
                code = e.code or 0
        return code, buf.getvalue(), err.getvalue(), calls

    def writes(self, calls):
        return [c for c in calls if c[0] != "GET"]

    def test_comment(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("POST", "/rest/api/2/issue/PROJ-1/comment"): {"id": "77"},
        }
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-c", "hello\n\nthere"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("[PROJ-1] Fix login", out)
        self.assertIn("comment: added (77)", out)
        body = self.writes(calls)[0][3]
        self.assertEqual(body, {"body": "hello\n\nthere"})

    def test_comment_from_stdin_and_v3_adf(self):
        routes = {
            ("GET", "/rest/api/3/issue/PROJ-1"): issue(),
            ("POST", "/rest/api/3/issue/PROJ-1/comment"): {"id": "8"},
        }
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-c", "-"], routes,
                                              env={"JIRA_API": "3"}, stdin="line\n\nmore")
        self.assertEqual(code, 0, err)
        self.assertIn("comment: added (8)", out)
        body = self.writes(calls)[0][3]["body"]
        self.assertEqual(body["type"], "doc")
        self.assertEqual(body["content"][0]["content"][0]["text"], "line")

    def test_comment_falls_back_to_v3_on_410(self):
        state = {"n": 0}

        def comment(params, body):
            state["n"] += 1
            if state["n"] == 1:
                raise jget.JiraError("gone", 410)
            self.assertEqual(body["body"]["type"], "doc")
            return {"id": "9"}

        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("POST", "/rest/api/2/issue/PROJ-1/comment"): comment,
            ("POST", "/rest/api/3/issue/PROJ-1/comment"): comment,
        }
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-c", "hi"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("comment: added (9)", out)
        self.assertEqual([c[1] for c in self.writes(calls)], [
            "/rest/api/2/issue/PROJ-1/comment",
            "/rest/api/3/issue/PROJ-1/comment",
        ])

    def test_status_by_target_name(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("GET", "/rest/api/2/issue/PROJ-1/transitions"): TRANSITIONS,
            ("POST", "/rest/api/2/issue/PROJ-1/transitions"): None,
        }
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "--status", "in progress"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("status: To Do -> In Progress", out)
        self.assertEqual(self.writes(calls)[0][3], {"transition": {"id": "11"}})

    def test_status_then_comment_as_separate_requests(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(status="In Progress"),
            ("GET", "/rest/api/2/issue/PROJ-1/transitions"): TRANSITIONS,
            ("POST", "/rest/api/2/issue/PROJ-1/transitions"): None,
            ("POST", "/rest/api/2/issue/PROJ-1/comment"): {"id": "5"},
        }
        code, out, err, calls = self.run_main(
            ["PROJ-1", "--plain", "--status", "21", "--resolution", "Done", "-c", "shipped"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("status: In Progress -> Done", out)
        self.assertIn("resolution: Done", out)
        self.assertIn("comment: added (5)", out)
        writes = self.writes(calls)
        self.assertEqual([w[1] for w in writes], [
            "/rest/api/2/issue/PROJ-1/transitions",
            "/rest/api/2/issue/PROJ-1/comment",
        ])
        self.assertEqual(writes[0][3], {"transition": {"id": "21"}, "fields": {"resolution": {"name": "Done"}}})
        self.assertEqual(writes[1][3], {"body": "shipped"})

    def test_partial_failure_reports_what_was_applied(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("GET", "/rest/api/2/user/assignable/search"): lambda params, body: [USERS[0]],
            ("GET", "/rest/api/2/issue/PROJ-1/transitions"): TRANSITIONS,
            ("POST", "/rest/api/2/issue/PROJ-1/transitions"): None,
            ("PUT", "/rest/api/2/issue/PROJ-1/assignee"):
                jget.JiraError("permission denied (HTTP 403): You cannot assign issues.", 403),
        }
        code, out, err, calls = self.run_main(
            ["PROJ-1", "--plain", "--status", "11", "-a", "Alex Mercer", "-c", "hi"], routes)
        self.assertEqual(code, 1)
        self.assertIn("assignee failed: permission denied (HTTP 403)", err)
        self.assertIn("already applied: status: To Do -> In Progress", err)
        self.assertTrue(all("/comment" not in c[1] for c in calls))

    def test_resolution_hints(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(status="In Progress"),
            ("GET", "/rest/api/2/issue/PROJ-1/transitions"): TRANSITIONS,
            ("POST", "/rest/api/2/issue/PROJ-1/transitions"):
                jget.JiraError("request failed (HTTP 400): resolution: Field 'resolution' cannot be set.", 400),
        }
        code, _, err, _ = self.run_main(["PROJ-1", "--plain", "--status", "21", "--resolution", "Done"], routes)
        self.assertEqual(code, 1)
        self.assertIn("retry without --resolution", err)
        self.assertNotIn("already applied", err)

        routes[("POST", "/rest/api/2/issue/PROJ-1/transitions")] = \
            jget.JiraError("request failed (HTTP 400): resolution: Resolution is required.", 400)
        code, _, err, _ = self.run_main(["PROJ-1", "--plain", "--status", "21"], routes)
        self.assertEqual(code, 1)
        self.assertIn("retry with --resolution", err)

    def test_ambiguous_status_writes_nothing(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(status="In Progress"),
            ("GET", "/rest/api/2/issue/PROJ-1/transitions"): TRANSITIONS,
        }
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "--status", "Done"], routes)
        self.assertEqual(code, 1)
        self.assertIn("matches 2 transitions", err)
        self.assertIn("current status: In Progress", err)
        self.assertEqual(self.writes(calls), [])

    def test_status_unchanged_still_comments(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(status="In Progress"),
            ("POST", "/rest/api/2/issue/PROJ-1/comment"): {"id": "3"},
        }
        code, out, err, calls = self.run_main(
            ["PROJ-1", "--plain", "--status", "In Progress", "-c", "note"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("status: In Progress (unchanged)", out)
        self.assertIn("comment: added (3)", out)
        self.assertTrue(all("/transitions" not in c[1] for c in calls))

    def test_assign_unassign_and_me(self):
        def search(params, body):
            self.assertEqual(params["issueKey"], "PROJ-1")
            return [USERS[0]] if params.get("query") == "Alex Mercer" else []

        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("GET", "/rest/api/2/user/assignable/search"): search,
            ("PUT", "/rest/api/2/issue/PROJ-1/assignee"): None,
        }
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-a", "Alex Mercer"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("assignee: Unassigned -> Alex Mercer", out)
        self.assertEqual(self.writes(calls)[0][3], {"accountId": "a1"})

        routes[("GET", "/rest/api/2/issue/PROJ-1")] = issue(assignee={"accountId": "a1", "displayName": "Alex Mercer"})
        code, out, err, calls = self.run_main(["https://x.atlassian.net/browse/PROJ-1", "--plain", "-a", "none"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("assignee: Alex Mercer -> Unassigned", out)
        self.assertEqual(self.writes(calls)[0][3], {"accountId": None})

        routes[("GET", "/rest/api/2/myself")] = {"accountId": "me1", "displayName": "Myself"}
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-a", "me"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("assignee: Alex Mercer -> Myself", out)
        self.assertEqual(self.writes(calls)[0][3], {"accountId": "me1"})

    def test_server_username_search_and_unassign(self):
        def search(params, body):
            self.assertNotIn("query", params)
            self.assertEqual(params.get("username"), "jdoe")
            return [{"name": "jdoe", "displayName": "J Doe"}]

        routes = {
            ("GET", "/rest/api/2/serverInfo"): {"deploymentType": "Server"},
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(assignee={"name": "old", "displayName": "Old"}),
            ("GET", "/rest/api/2/user/assignable/search"): search,
            ("PUT", "/rest/api/2/issue/PROJ-1/assignee"): None,
        }
        env = {"JIRA_URL": "https://jira.example.com"}
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-a", "jdoe"], routes, env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("assignee: Old -> J Doe", out)
        self.assertEqual(self.writes(calls)[0][3], {"name": "jdoe"})

        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-a", "-"], routes, env=env)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.writes(calls)[0][3], {"name": None})

    def test_ambiguous_assignee_and_account_id_fallback(self):
        def search(params, body):
            if params.get("query") == "abc123":
                return []
            return USERS

        def user(params, body):
            if params.get("accountId") == "abc123":
                return {"accountId": "abc123", "displayName": "Sam"}
            raise jget.JiraError("not found", 404)

        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("GET", "/rest/api/2/user/assignable/search"): search,
            ("GET", "/rest/api/2/user"): user,
            ("PUT", "/rest/api/2/issue/PROJ-1/assignee"): None,
        }
        code, _, err, calls = self.run_main(["PROJ-1", "--plain", "-a", "Alex"], routes)
        self.assertEqual(code, 1)
        self.assertIn("not an exact match", err)
        self.assertIn("Alex Kim", err)
        self.assertEqual(self.writes(calls), [])

        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-a", "abc123"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("assignee: Unassigned -> Sam", out)
        self.assertEqual(self.writes(calls)[0][3], {"accountId": "abc123"})

    def test_already_assigned_is_a_noop(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(assignee={"accountId": "a1", "displayName": "Alex Mercer"}),
            ("GET", "/rest/api/2/user/assignable/search"): lambda params, body: [USERS[0]],
        }
        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "-a", "alex@example.com"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("assignee: Alex Mercer (unchanged)", out)
        self.assertEqual(self.writes(calls), [])

    def test_transition_failure_does_not_assign(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("GET", "/rest/api/2/user/assignable/search"): lambda params, body: [USERS[0]],
            ("GET", "/rest/api/2/issue/PROJ-1/transitions"): TRANSITIONS,
            ("POST", "/rest/api/2/issue/PROJ-1/transitions"): jget.JiraError("resolution: Resolution is required.", 400),
        }
        code, out, err, calls = self.run_main(
            ["PROJ-1", "--plain", "--status", "In Progress", "-a", "Alex Mercer"], routes)
        self.assertEqual(code, 1)
        self.assertIn("Resolution is required", err)
        self.assertEqual(self.writes(calls)[0][0], "POST")
        self.assertTrue(all(c[0] != "PUT" for c in calls))
        self.assertNotIn("assignee:", out)

    def test_dry_run_and_list_transitions_do_not_write(self):
        routes = {
            ("GET", "/rest/api/2/issue/PROJ-1"): issue(),
            ("GET", "/rest/api/2/issue/PROJ-1/transitions"): TRANSITIONS,
            ("GET", "/rest/api/2/user/assignable/search"): lambda params, body: [USERS[0]],
        }
        code, out, err, calls = self.run_main(
            ["PROJ-1", "--plain", "--status", "In Progress", "-a", "Alex Mercer", "-c", "hi", "--dry-run"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("status: To Do -> In Progress", out)
        self.assertIn("assignee: Unassigned -> Alex Mercer", out)
        self.assertIn("comment: would add", out)
        self.assertIn("dry-run", out)
        self.assertEqual(self.writes(calls), [])

        code, out, err, calls = self.run_main(["PROJ-1", "--plain", "--transitions"], routes)
        self.assertEqual(code, 0, err)
        self.assertIn("status: To Do", out)
        self.assertIn("11  Start Progress -> In Progress", out)
        self.assertEqual(self.writes(calls), [])

    def test_usage_errors(self):
        code, _, err, calls = self.run_main(["PROJ-1", "--plain"], {})
        self.assertEqual(code, 1)
        self.assertIn("nothing to do", err)
        self.assertEqual(calls, [])

        code, _, err, _ = self.run_main(["PROJ-1", "--resolution", "Done"], {})
        self.assertEqual(code, 1)
        self.assertIn("--resolution requires --status", err)

        code, _, err, _ = self.run_main(["PROJ-1", "-c", "  "], {})
        self.assertEqual(code, 1)
        self.assertIn("comment is empty", err)

        code, _, err, _ = self.run_main(["nope", "-c", "hi"], {})
        self.assertEqual(code, 1)
        self.assertIn("not an issue key", err)

        code, _, err, _ = self.run_main(["PROJ-1", "-c", "hi"], {}, {"JIRA_URL": "", "JIRA_USER": ""})
        self.assertEqual(code, 1)
        self.assertIn("jedit: missing environment variable(s): JIRA_URL, JIRA_USER", err)

        code, _, err, _ = self.run_main(["PROJ-1", "--transitions", "-c", "hi"], {})
        self.assertEqual(code, 1)
        self.assertIn("--transitions only lists", err)


class InstallTest(unittest.TestCase):
    def test_install_and_uninstall_roundtrip(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, "bin")
            env = {"HOME": home, "USERPROFILE": home, "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude")}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(os.path.expanduser("~"), home)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    jedit.install(["--bin-dir", bin_dir, "--cursor"])
                skill = os.path.join(home, ".cursor", "skills", "jedit", "SKILL.md")
                self.assertTrue(os.path.isfile(skill))
                with open(skill, encoding="utf-8") as fh:
                    self.assertIn("name: jedit", fh.read())
                exe = os.path.join(bin_dir, "jedit.py" if jget.IS_WINDOWS else "jedit")
                self.assertTrue(os.path.isfile(exe))
                self.assertTrue(jedit.is_jedit_file(exe))
                jget_exe = os.path.join(bin_dir, "jget.py" if jget.IS_WINDOWS else "jget")
                self.assertTrue(jedit.is_jget_source(jget_exe))
                self.assertIn("jedit installed.", buf.getvalue())

                # jget uninstall must not pull the library out from under jedit.
                buf = io.StringIO()
                with redirect_stdout(buf):
                    jget.uninstall(["--bin-dir", bin_dir, "--cursor"])
                self.assertTrue(os.path.isfile(jget_exe))
                self.assertIn("kept jget", buf.getvalue())

                buf = io.StringIO()
                with redirect_stdout(buf):
                    jedit.uninstall(["--bin-dir", bin_dir, "--cursor"])
                self.assertFalse(os.path.exists(skill))
                self.assertFalse(os.path.exists(exe))
                self.assertTrue(os.path.isfile(jget_exe))
                self.assertIn("jedit uninstalled.", buf.getvalue())

                with redirect_stdout(io.StringIO()):
                    jget.uninstall(["--bin-dir", bin_dir, "--cursor"])
                self.assertFalse(os.path.exists(jget_exe))


if __name__ == "__main__":
    unittest.main()
