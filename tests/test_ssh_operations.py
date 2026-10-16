"""Pure remote-operation parser checks against the accepted H1 corpus."""

import json
import copy
import os
import shlex
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks" / "guard"))
from ssh_operations import _lex, inspect_remote as _inspect_remote


def inspect_remote(command, policy, *, cwd):
    # Traps end before unittest formats failures (which reads source files).
    with ExitStack() as traps:
        for target in ("subprocess.run", "subprocess.Popen", "os.system", "os.execve",
                       "socket.socket", "socket.create_connection", "socket.getaddrinfo",
                       "builtins.open", "io.open", "os.open", "os.stat", "os.lstat",
                       "os.listdir", "os.scandir", "os.readlink", "os.path.realpath",
                       "pathlib.Path.resolve"):
            traps.enter_context(mock.patch(target, side_effect=AssertionError("IO: " + target)))
        return _inspect_remote(command, policy, cwd=cwd)


class RemoteOperationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / "tests" / "fixtures" / "ssh-operations.json").read_text())
        cls.policy = cls.fixture["policy"]

    def test_remote_expansion_stays_unknown(self):
        result = inspect_remote("ssh deploy@example.test 'cat $FILE'", self.policy,
                                cwd="/workspace/site")
        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertEqual(result["effect_scope"], "invocation_only")

    def test_bound_enabled_corpus(self):
        selected = [case for case in self.fixture["cases"]
                    if case["state"] in ("bound", "enrolled-non-git")
                    and case["mode"] != "off" and not case.get("input_bytes")]
        self.assertGreaterEqual(len(selected), 150)
        for case in selected:
            with self.subTest(case=case["id"]):
                self.assertEqual(inspect_remote(case["command"], self.policy,
                                                cwd=case["cwd"]), case["expected"])

    def test_unknown_input_never_allows(self):
        for command in ("ssh deploy@example.test cat /srv/site/VERSION | cat",
                        "ssh deploy@example.test cat $FILE",
                        "sh -c 'sh -c \"sh -c \\\"ssh example.test cat /srv/site/VERSION\\\"\"'",
                        "ssh deploy@example.test cat /srv/site/VERSION\\"):
            with self.subTest(command=command[:30]):
                self.assertNotEqual(inspect_remote(command, self.policy,
                                                   cwd="/workspace/site")["verdict"], "ALLOW")

    def test_newline_exposes_command_position(self):
        result = inspect_remote("printf ready\nssh deploy@example.test rm /srv/site/current",
                                self.policy, cwd="/workspace/site")
        self.assertEqual((result["verdict"], result["rule_id"]),
                         ("BLOCK", "protect-current"))

    def test_lexer_keeps_quote_escape_and_source_spans(self):
        source = "ssh 'cat /srv/site/VERSION' /srv/site/VER\\SION"
        tokens = _lex(source)
        self.assertEqual([token[0] for token in tokens],
                         ["ssh", "cat /srv/site/VERSION", "/srv/site/VERSION"])
        self.assertIn("single-quote", tokens[1][5])
        self.assertIn("escape", tokens[2][5])
        for token in tokens:
            self.assertEqual(source[token[2]:token[3]].replace("'", "").replace("\\", ""),
                             token[0])

    def test_remote_reparse_has_own_token_limit(self):
        script = "cat " + " ".join(["/srv/site/VERSION"] * 512)
        result = inspect_remote("ssh deploy@example.test '" + script + "'",
                                self.policy, cwd="/workspace/site")
        self.assertEqual((result["applicability"], result["verdict"], result["reason_id"]),
                         ("unknown", "UNKNOWN", "scan-incomplete"))

    def test_review_regressions(self):
        policy = {
            "schema_version": 1,
            "destinations": {"reef": {"host": "node.invalid", "user": "operator", "port": 2227}},
            "rules": [
                {"id": "read", "decision": "allow", "transport": "ssh", "destination": "reef", "argv": ["cat", "/opt/reef/VERSION"]},
                {"id": "upload", "decision": "allow", "transport": "rsync", "destination": "reef", "remote_root": "/opt/reef"},
                {"id": "delete", "decision": "deny", "transport": "rsync", "destination": "reef", "operation": "delete", "remote_root": "/opt/reef"},
                {"id": "remove", "decision": "deny", "transport": "rsync", "destination": "reef", "operation": "remove", "remote_root": "/opt/reef"},
            ],
        }
        base = "ssh -p2227 operator@node.invalid cat /opt/reef/VERSION"
        upload = "./tree/ operator@node.invalid:/opt/reef/"
        cases = [
            ("rsync -a --delete " + upload, "BLOCK", "delete"),
            ("rsync -a --delete-before " + upload, "BLOCK", "delete"),
            ("rsync -a --remove-source-files " + upload, "BLOCK", "remove"),
            ("rsync -a --delete --remove-source-files " + upload, "BLOCK", "delete"),
            ("rsync -a -e --delete " + upload, "UNKNOWN", None),
            ("sh -c \"printf '$PAYLOAD'; " + base + "\"", "UNKNOWN", None),
            ("bash -c \"printf '$PAYLOAD'; " + base + "\"", "UNKNOWN", None),
            ("rsync -a ~/tree/ operator@node.invalid:/opt/reef/", "UNKNOWN", None),
            ("rsync -a * operator@node.invalid:/opt/reef/", "UNKNOWN", None),
            ("rsync -a ./tree/ operator@node.invalid:/opt/reef/*", "UNKNOWN", None),
            (base.replace("cat ", "cat\u00a0"), "UNKNOWN", None),
            (base.replace("cat ", "cat\r"), "UNKNOWN", None),
            (base.replace("cat ", "cat\v"), "UNKNOWN", None),
            ("ssh -p" + "9" * 5000 + " operator@node.invalid cat /opt/reef/VERSION", "UNKNOWN", None),
            ("timeout " + "9" * 5000 + " " + base, "UNKNOWN", None),
            (base.replace("ssh ", "ssh -oUserKnownHostsFile=/tmp/a -oUserKnownHostsFile=/tmp/b ", 1), "UNKNOWN", None),
        ]
        alias = copy.deepcopy(policy)
        alias["destinations"]["reef-copy"] = copy.deepcopy(alias["destinations"]["reef"])
        alias["rules"].append({"id": "alias-deny", "decision": "deny", "transport": "ssh", "destination": "reef-copy", "argv": ["cat", "/opt/reef/VERSION"]})
        for command, verdict, rule in cases:
            with self.subTest(command=command[:90]):
                result = inspect_remote(command, policy, cwd="/unused")
                self.assertEqual(result["verdict"], verdict)
                if rule:
                    self.assertEqual(result["rule_id"], rule)
        self.assertEqual(inspect_remote(base, alias, cwd="/unused")["rule_id"], "alias-deny")

    def test_continuations_preserve_words_and_deliberate_empty_arguments(self):
        cases = [
            ("\\\n", []), ("\\\n \\\n\t", []),
            ("\\\n ssh", ["ssh"]), ("s\\\nsh", ["ssh"]),
            ("''", [""]), ('""', [""]),
            ("\\\n '' \\\n", [""]), ('"\\\n"', [""]),
            ("''\\\n ssh", ["", "ssh"]),
            ("ssh \\\n host", ["ssh", "host"]),
        ]
        for source, words in cases:
            for remote in (False, True):
                with self.subTest(source=source, remote=remote):
                    self.assertEqual([t[0] for t in _lex(source, remote=remote)], words)
        token, = _lex("  s\\\nsh")
        self.assertEqual(token[2:4], (2, 7))
        self.assertIn("escape", token[5])

    def test_continuations_cannot_hide_remote_commands(self):
        for n in range(3):
            policy, base, root = self.boundary_policy(n)
            for argv, verdict, reason, rule in (
                ("cat " + root + "/VERSION", "ALLOW", "matched-allow", "read"),
                ("rm " + root + "/live", "BLOCK", "matched-deny", "protect"),
            ):
                expected = dict(schema_version=1, applicability="relevant", verdict=verdict,
                                reason_id=reason, rule_id=rule, effect_scope="invocation_only")
                command = base + " " + argv
                for prefix in ("", "command ", "env LABEL=ok ", "timeout 3 "):
                    for continuation in ("\\\n ", "\\\n\t\\\n "):
                        with self.subTest(n=n, verdict=verdict, prefix=prefix,
                                          continuation=continuation):
                            self.assertEqual(inspect_remote(prefix + continuation + command,
                                                            policy, cwd="/unused"), expected)
                for source in (
                    command.replace("ssh ", "ssh \\\n ", 1),
                    base + " " + shlex.quote("\\\n " + argv),
                    "sh -c " + shlex.quote("\\\n " + command),
                    "bash -c " + shlex.quote("env \\\n " + command),
                ):
                    with self.subTest(n=n, verdict=verdict, source=source):
                        self.assertEqual(inspect_remote(source, policy, cwd="/unused"), expected)
            # A quoted empty word really is a command word; removing it would
            # falsely reinterpret the following SSH text as command-position.
            empty_command = inspect_remote("'' " + base + " rm " + root + "/live",
                                           policy, cwd="/unused")
            self.assertEqual((empty_command["applicability"], empty_command["reason_id"]),
                             ("nonrelevant", "nonrelevant"))

    def test_remote_tilde_requires_remote_layer_quoting(self):
        for n in range(3):
            policy, base, _ = self.boundary_policy(n)
            for argument in ("~", "~/release", "~someuser/release", "~+", "~-"):
                policy["rules"].append(dict(id="literal-tilde", decision="allow", transport="ssh",
                                            destination="pier", argv=["/opt/tool/check", argument]))
                for remote_argument, verdict, reason, rule in (
                    (argument, "UNKNOWN", "dynamic-remote-argument", None),
                    (shlex.quote(argument), "ALLOW", "matched-allow", "literal-tilde"),
                    ('"' + argument + '"', "ALLOW", "matched-allow", "literal-tilde"),
                    ("\\" + argument, "ALLOW", "matched-allow", "literal-tilde"),
                ):
                    command = base + " " + shlex.quote("/opt/tool/check " + remote_argument)
                    with self.subTest(n=n, argument=argument, remote_argument=remote_argument):
                        self.assertEqual(inspect_remote(command, policy, cwd="/unused"),
                                         dict(schema_version=1,
                                              applicability="unknown" if verdict == "UNKNOWN" else "relevant",
                                              verdict=verdict, reason_id=reason, rule_id=rule,
                                              effect_scope="invocation_only"))
                policy["rules"].pop()

    def test_wrapper_chains_are_bounded_per_shell_level(self):
        for n in range(3):
            policy, base, root = self.boundary_policy(n)
            for argv, verdict, rule in (("cat " + root + "/VERSION", "ALLOW", "read"),
                                        ("rm " + root + "/live", "BLOCK", "protect")):
                for prefix in ("command " * 350, "env " * 350,
                               "timeout 1 " * 200, "command env timeout 1 " * 100):
                    command = prefix + base + " " + argv
                    for depth in range(3):
                        if depth:
                            command = prefix + "sh -c " + shlex.quote(command)
                        self.assertLessEqual(len(command.encode()), 65536)
                        self.assertLessEqual(len(_lex(command)), 512)
                        with self.subTest(n=n, verdict=verdict, wrapper=prefix[:10], depth=depth):
                            result = inspect_remote(command, policy, cwd="/unused")
                            self.assertEqual((result["verdict"], result["rule_id"]), (verdict, rule))
                    result = inspect_remote("sh -c " + shlex.quote(command), policy, cwd="/unused")
                    self.assertEqual((result["applicability"], result["verdict"], result["reason_id"]),
                                     ("unknown", "UNKNOWN", "scan-incomplete"))

    def test_continued_comment_cannot_match_exact_literal_argument(self):
        for n in range(3):
            policy, base, _ = self.boundary_policy(n)
            literal = "#note" + str(n)
            policy["rules"].append(dict(id="literal-hash", decision="allow", transport="ssh",
                                        destination="pier", argv=["/opt/check", literal]))
            for continuation in ("\\\n", "\\\n\\\n"):
                for gap in (" ", "\t"):
                    for quoted in (False, True):
                        remote = "/opt/check" + gap + continuation + (shlex.quote(literal) if quoted else literal)
                        with self.subTest(n=n, continuation=continuation, gap=gap, quoted=quoted):
                            actual = inspect_remote(base + " " + shlex.quote(remote), policy, cwd="/workspace/site")
                            self.assertEqual(actual["verdict"], "ALLOW" if quoted else "UNKNOWN")
                            if quoted:
                                self.assertEqual(actual["rule_id"], "literal-hash")
            for word in (shlex.quote(literal), '"' + literal + '"', "\\" + literal, "''" + literal):
                actual = inspect_remote(base + " " + shlex.quote("/opt/check " + word), policy, cwd="/workspace/site")
                self.assertEqual((actual["verdict"], actual["rule_id"]), ("ALLOW", "literal-hash"))

    def test_unsupported_operators_cannot_fabricate_remote_operands(self):
        for n in range(3):
            policy, base, root = self.boundary_policy(n)
            deny = base + " rm " + root + "/live"
            for operator in ("<", ">", ">>", "|", "&"):
                incomplete = base + " rm " + root + "/other " + operator + " " + root + "/live"
                with self.subTest(n=n, operator=operator):
                    self.assertEqual(inspect_remote(incomplete, policy, cwd="/workspace/site")["verdict"], "UNKNOWN")
                    for separator in (";", "&&", "||", "\n"):
                        for compound in (deny + separator + incomplete, incomplete + separator + deny):
                            actual = inspect_remote(compound, policy, cwd="/workspace/site")
                            self.assertEqual((actual["verdict"], actual["rule_id"]), ("BLOCK", "protect"))
                    for wrapper in ("command ", "env TAG=literal timeout 5 "):
                        self.assertEqual(inspect_remote(wrapper + incomplete, policy, cwd="/workspace/site")["verdict"], "UNKNOWN")
                    for shell in ("sh", "bash"):
                        self.assertEqual(inspect_remote(shell + " -c " + shlex.quote(incomplete), policy,
                                                        cwd="/workspace/site")["verdict"], "UNKNOWN")

    @staticmethod
    def boundary_policy(n):
        root = "/opt/pier-" + str(37 + n)
        host, user, port = "worker-" + str(n + 71) + ".invalid", "pilot" + str(n), 19113 + n
        policy = dict(schema_version=1,
                      destinations={"pier": dict(host=host, user=user, port=port)},
                      rules=[dict(id="read", decision="allow", transport="ssh", destination="pier",
                                  argv=["cat", root + "/VERSION"]),
                             dict(id="protect", decision="deny", transport="ssh", destination="pier",
                                  operation="remove", remote_root=root + "/live")])
        return policy, "ssh -p" + str(port) + " " + user + "@" + host, root


if __name__ == "__main__":
    unittest.main()
