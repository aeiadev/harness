"""Freeze the reviewed 0.5.0 remote-operation corpus; no command is executed."""

import copy
import hashlib
import json
import re
import shlex
import unittest
from pathlib import Path

FIXTURE = Path(__file__).with_name("fixtures") / "ssh-operations.json"
REQUIRED_CASES = {
    'ssh-read', 'ssh-mkdir', 'ssh-rename',
    'ssh-readback', 'scp-upload', 'rsync-upload',
    'protected-rm', 'rsync-delete', 'wrong-host',
    'wrong-user', 'wrong-port', 'joined-argv-injection',
    'expansion', 'wrapper', 'compound',
    'unmatched-quote', 'interactive', 'forwarding',
    'opaque', 'digest-drift', 'nested-root',
    'sibling-root', 'late-ssh-limit', 'benign-limit',
    'incomplete-wrapper', 'literal-nonremote', 'off-parity',
    'observer-write-denied', 'legacy-scp', 'remote-path-escape',
    'corrupt-registry', 'report-deny', 'report-unknown',
    'report-digest-drift', 'token-limit-at', 'token-limit-over',
    'depth-two', 'depth-three', 'policy-limit-at',
    'policy-limit-over', 'registry-limit-at', 'registry-limit-over',
    'ssh-joined-options', 'ssh-combined-flags', 'scp-joined-options',
    'scp-combined-flags', 'rsync-long-options', 'ssh-missing-value',
    'scp-missing-value', 'ssh-terminator', 'scp-terminator',
    'rsync-terminator', 'ssh-test', 'ssh-exact-executable',
    'ssh-executable-extra-argument', 'rename-not-upload', 'read-not-allowed',
    'root-component-prefix', 'ssh-defaults', 'ssh-all-options',
    'scp-all-options', 'ssh-joined-identity', 'scp-joined-identity',
    'ssh-group-value-joined', 'ssh-group-value-separate', 'scp-group-value-separate',
    'same-user-port', 'conflicting-user', 'conflicting-port',
    'empty-option-value', 'option-looking-value', 'unreviewed-o',
    'proxycommand', 'proxyjump', 'tunnel',
    'ssh-config', 'ssh-unknown-option', 'ssh-missing-l',
    'ssh-missing-i', 'ssh-missing-o', 'scp-missing-i',
    'scp-missing-o', 'scp-empty-value', 'scp-option-looking-value',
    'scp-conflicting-port', 'scp-unreviewed-o', 'ssh-post-destination-option',
    'scp-post-source-option', 'scp-option-operand', 'recursive-scp',
    'remote-to-remote-scp', 'scp-remote-to-remote-mode', 'scp-ambiguous-colon',
    'scp-multiple-sources', 'scp-missing-destination', 'scp-download',
    'rsync-relative', 'rsync-e', 'rsync-rsh',
    'rsync-path', 'rsync-combined', 'rsync-long-value',
    'rsync-option-after-source', 'rsync-daemon', 'rsync-daemon-url',
    'rsync-source-no-slash', 'rsync-dest-no-slash', 'rsync-missing-user',
    'rsync-multiple-sources', 'rsync-environment', 'rsync-source-removal',
    'rsync-delete-before', 'rsync-delete-during', 'rsync-delete-delay',
    'rsync-delete-after', 'rsync-delete-excluded', 'rsync-delete-unknown',
    'rsync-deny-with-unsupported', 'speculative-deny', 'relative-path',
    'tilde-path', 'remote-pipeline', 'remote-redirect',
    'remote-substitution', 'remote-backticks', 'nested-ssh',
    'remote-script', 'encoded-shell', 'remote-quote-unmatched',
    'scp-relative-path', 'scp-tilde-path', 'scp-variable-path',
    'rsync-relative-path', 'heredoc', 'stdin-script',
    'local-substitution', 'local-pipeline', 'unconsumed-background',
    'dynamic-destination', 'escaped-metacharacter', 'joined-space',
    'command-wrapper', 'bash-wrapper', 'compound-semicolon',
    'compound-or', 'compound-deny', 'compound-unknown',
    'opaque-local', 'argument-not-command', 'escaped-literal',
    'command-limit-at', 'command-limit-over', 'non-utf8',
    'report-policy-limit-over', 'report-allow', 'report-scan-incomplete',
    'off-scan-incomplete', 'off-remote', 'corrupt-registry-nonremote',
    'corrupt-registry-incomplete', 'policy-oversize-nonremote', 'report-missing-policy',
    'enforce-missing-policy', 'report-malformed-policy', 'enforce-malformed-policy',
    'report-duplicate-policy-key', 'enforce-duplicate-policy-key', 'report-invalid-policy-enum',
    'enforce-invalid-policy-enum', 'report-invalid-policy-version', 'enforce-invalid-policy-version',
    'report-policy-symlink', 'enforce-policy-symlink', 'report-policy-fifo',
    'enforce-policy-fifo', 'report-policy-ancestor-escape', 'enforce-policy-ancestor-escape',
    'report-wrong-project', 'enforce-wrong-project', 'report-registry-inside-project',
    'enforce-registry-inside-project', 'report-unsupported-platform', 'enforce-unsupported-platform',
    'nested-worktree', 'home-root', 'filesystem-root',
    'enrolled-non-git', 'rsync-delete-missing-args', 'token-limit-next',
    'wrapper-option', 'wrapper-extra-argument', 'wrapper-dynamic',
    'wrapper-missing-command', 'ssh-dot-path', 'scp-empty-component',
    'rsync-tilde-path', 'rsync-variable-path', 'nonremote-unknown-quoting',
    'remote-heredoc', 'byte-limit-multibyte',
}
REQUIRED_CASES.update({
    'remote-absolute-undeclared',
    'remote-cat-empty',
    'remote-cat-many',
    'remote-cat-option',
    'remote-cat-terminator',
    'remote-mkdir-bare',
    'remote-mkdir-empty',
    'remote-mkdir-many',
    'remote-mkdir-mode',
    'remote-mkdir-terminator',
    'remote-mv-many',
    'remote-mv-missing',
    'remote-mv-option',
    'remote-mv-terminator',
    'remote-rm-empty',
    'remote-rm-force',
    'remote-rm-long-option',
    'remote-rm-many',
    'remote-rm-option-after-path',
    'remote-rm-recursive',
    'remote-rm-separate-flags',
    'remote-rm-unlisted',
    'remote-test-directory',
    'remote-test-empty',
    'remote-test-exists',
    'remote-test-many',
    'remote-test-option',
    'wrong-destination-unknown-command',
})
RULE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
DECISIONS = {"allow", "deny"}
TRANSPORTS = {"ssh", "scp", "rsync"}
VERDICTS = {"ALLOW", "BLOCK", "UNKNOWN"}
APPLICABILITY = {"relevant", "nonrelevant", "unknown"}
REQUIREMENTS = {"S1", "S2", "S3"}

OPERATIONS = {"ssh": {"read", "create-dir", "rename", "remove"},
              "scp": {"upload"}, "rsync": {"upload", "delete", "remove"}}
OFF_STATES = {"unbound", "nested-root", "sibling-root", "nested-worktree",
              "home-root", "filesystem-root"}
INTEGRITY_STATES = {"corrupt-registry", "registry-oversize"}
VALID_STATES = {"bound", "enrolled-non-git"}
INVALID_POLICY = {
    "digest-drift": "policy-digest-drift", "policy-oversize": "policy-size-limit",
    "missing-policy": "missing-policy", "malformed-policy": "policy-invalid",
    "duplicate-policy-key": "policy-invalid", "invalid-policy-enum": "policy-invalid",
    "invalid-policy-version": "policy-invalid", "policy-symlink": "policy-symlink",
    "policy-fifo": "policy-fifo", "policy-ancestor-escape": "policy-ancestor-escape",
    "wrong-project": "wrong-project", "registry-inside-project": "registry-inside-project",
    "unsupported-platform": "unsupported-platform",
}


def unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key: " + key)
        out[key] = value
    return out


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {"schema_version", "destinations", "rules"} or type(policy.get("schema_version")) is not int or policy.get("schema_version") != 1:
        raise ValueError("invalid policy schema")
    destinations = policy.get("destinations")
    if not isinstance(destinations, dict) or not destinations:
        raise ValueError("missing destinations")
    for label, destination in destinations.items():
        if not isinstance(label, str) or not RULE_ID.fullmatch(label) or not isinstance(destination, dict):
            raise ValueError("invalid destination")
        if set(destination) != {"host", "user", "port"}:
            raise ValueError("invalid destination fields")
        if not all(isinstance(destination[key], str) and destination[key] for key in ("host", "user")):
            raise ValueError("invalid destination identity")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", destination["host"]) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", destination["user"]):
            raise ValueError("destination identity must be literal")
        if type(destination["port"]) is not int or not 1 <= destination["port"] <= 65535:
            raise ValueError("invalid destination port")
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("missing rules")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not RULE_ID.fullmatch(rule["id"]):
            raise ValueError("malformed rule ID")
        if rule["id"] in seen:
            raise ValueError("duplicate rule ID")
        seen.add(rule["id"])
        if not isinstance(rule.get("decision"), str) or rule["decision"] not in DECISIONS or not isinstance(rule.get("transport"), str) or rule["transport"] not in TRANSPORTS:
            raise ValueError("invalid rule decision or transport")
        if not isinstance(rule.get("destination"), str) or rule["destination"] not in destinations:
            raise ValueError("unknown rule destination")
        shape = set(rule) - {"id", "decision", "transport", "destination"}
        if shape not in ({"argv"}, {"remote_root"}, {"operation", "remote_root"}):
            raise ValueError("invalid rule shape")
        if "argv" in rule and (not isinstance(rule["argv"], list) or not rule["argv"] or
                               not all(isinstance(v, str) and v for v in rule["argv"])):
            raise ValueError("invalid rule argv")
        if "remote_root" in rule and (not isinstance(rule["remote_root"], str) or
                                      not rule["remote_root"].startswith("/") or
                                      any(part in {"", ".", ".."} for part in rule["remote_root"][1:].split("/")) or
                                      any(char in rule["remote_root"] for char in "~$*?[]\\")):
            raise ValueError("invalid remote root")
        if "operation" in rule and (not isinstance(rule["operation"], str) or rule["operation"] not in OPERATIONS[rule["transport"]]):
            raise ValueError("invalid operation")
        if "argv" in rule and rule["transport"] != "ssh":
            raise ValueError("exact argv requires ssh")
        if shape == {"remote_root"} and rule["transport"] == "ssh":
            raise ValueError("ssh root requires an operation")
        if rule["transport"] == "ssh" and rule["decision"] == "allow" and shape != {"argv"}:
            raise ValueError("ssh allow requires exact argv")
    return policy


def validate(corpus):
    if not isinstance(corpus, dict) or set(corpus) != {"schema_version", "policy", "cases"} or type(corpus.get("schema_version")) is not int or corpus.get("schema_version") != 1:
        raise ValueError("invalid corpus schema")
    policy = validate_policy(corpus.get("policy"))
    rules = policy["rules"]
    seen = {rule["id"] for rule in rules}
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise ValueError("missing cases")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) not in ({"id", "command", "cwd", "mode", "state", "expected", "covers", "hook_effect"}, {"id", "command", "cwd", "mode", "state", "expected", "covers", "hook_effect", "input_bytes"}):
            raise ValueError("invalid case fields")
        if not isinstance(case["id"], str) or not RULE_ID.fullmatch(case["id"]) or case["id"] in ids:
            raise ValueError("invalid or duplicate case ID")
        ids.add(case["id"])
        if not isinstance(case["command"], str) or not case["command"] or not isinstance(case["cwd"], str):
            raise ValueError("invalid command or cwd")
        if not isinstance(case["mode"], str) or not isinstance(case["state"], str) or case["mode"] not in {"off", "report", "enforce", "unknown"} or case["state"] not in OFF_STATES | INTEGRITY_STATES | VALID_STATES | set(INVALID_POLICY):
            raise ValueError("invalid activation state")
        if (case["mode"] == "unknown") != (case["state"] in INTEGRITY_STATES):
            raise ValueError("unrecoverable mode requires invalid registry")
        if case["state"] in OFF_STATES and case["mode"] != "off":
            raise ValueError("unbound roots cannot inherit mode")
        if case["state"] not in OFF_STATES and case["mode"] == "off":
            raise ValueError("activated root cannot be off")
        if not isinstance(case["hook_effect"], str) or case["hook_effect"] not in {"silent", "warn", "refuse"}:
            raise ValueError("invalid hook effect")
        expected = case["expected"]
        if not isinstance(expected, dict) or set(expected) != {"schema_version", "applicability", "verdict", "reason_id", "rule_id", "effect_scope"} or type(expected["schema_version"]) is not int or expected["schema_version"] != 1:
            raise ValueError("invalid expected result")
        if not isinstance(expected["applicability"], str) or not isinstance(expected["verdict"], str) or expected["applicability"] not in APPLICABILITY or expected["verdict"] not in VERDICTS:
            raise ValueError("invalid expected classification")
        if not isinstance(expected["reason_id"], str) or not RULE_ID.fullmatch(expected["reason_id"]):
            raise ValueError("invalid reason ID")
        if expected["rule_id"] is not None and (not isinstance(expected["rule_id"], str) or expected["rule_id"] not in seen):
            raise ValueError("unknown expected rule")
        if expected["effect_scope"] != "invocation_only":
            raise ValueError("invalid effect scope")
        if expected["applicability"] == "unknown" and expected["verdict"] == "ALLOW":
            raise ValueError("unknown input cannot allow")
        if expected["verdict"] == "ALLOW" and expected["applicability"] == "relevant" and case["mode"] != "off" and (case["state"] not in VALID_STATES or expected["rule_id"] is None):
            raise ValueError("ALLOW requires a bound allow rule")
        if case["mode"] == "off" and (expected["verdict"], expected["reason_id"], case["hook_effect"]) != ("ALLOW", "feature-off", "silent") and expected["applicability"] == "relevant":
            raise ValueError("off mode must retain baseline")
        if case["hook_effect"] != ("silent" if case["mode"] == "off" or expected["verdict"] == "ALLOW" else "warn" if case["mode"] == "report" else "refuse"):
            raise ValueError("inconsistent hook effect")
        if expected["applicability"] == "nonrelevant" and (expected["verdict"], expected["reason_id"], expected["rule_id"]) != ("ALLOW", "nonrelevant", None):
            raise ValueError("complete nonrelevance must retain baseline")
        if expected["applicability"] == "unknown" and (expected["verdict"] != "UNKNOWN" or expected["rule_id"] is not None):
            raise ValueError("incomplete or unsupported input stays unknown")
        if expected["reason_id"] == "scan-incomplete" and (expected["applicability"], expected["verdict"]) != ("unknown", "UNKNOWN"):
            raise ValueError("incomplete scan cannot be nonrelevant")
        if expected["applicability"] == "relevant" and case["state"] in INVALID_POLICY:
            if (expected["verdict"], expected["reason_id"], expected["rule_id"]) != ("BLOCK", INVALID_POLICY[case["state"]], None):
                raise ValueError("invalid enabled policy must block classification")
        if expected["applicability"] == "relevant" and case["state"] in INTEGRITY_STATES:
            reason = "registry-size-limit" if case["state"] == "registry-oversize" else "registry-integrity"
            if (expected["verdict"], expected["reason_id"], expected["rule_id"]) != ("BLOCK", reason, None):
                raise ValueError("invalid registry requires integrity classification")
        if expected["rule_id"] is not None:
            rule = next(r for r in rules if r["id"] == expected["rule_id"])
            verdict, reason = ("ALLOW", "matched-allow") if rule["decision"] == "allow" else ("BLOCK", "matched-deny")
            if (expected["verdict"], expected["reason_id"], expected["applicability"]) != (verdict, reason, "relevant"):
                raise ValueError("inconsistent rule result")
        elif expected["reason_id"] in {"matched-allow", "matched-deny"}:
            raise ValueError("matched result requires rule")
        recipe = case.get("input_bytes")
        if recipe is not None:
            if not isinstance(recipe, dict) or set(recipe) != {"surface", "base", "pad_count", "utf8_bytes"} or not isinstance(recipe["surface"], str) or recipe["surface"] not in {"policy", "registry"} or recipe["base"] != "sample-" + recipe["surface"] or type(recipe["pad_count"]) is not int or not 0 <= recipe["pad_count"] <= 65537 or type(recipe["utf8_bytes"]) is not int:
                raise ValueError("invalid byte recipe")
            policy_base = json.dumps(policy, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            base = policy if recipe["surface"] == "policy" else {"schema_version": 1, "bindings": [{"project_root": "/workspace/site", "policy_path": ".harness/operations.json", "sha256": hashlib.sha256(policy_base).hexdigest(), "mode": "enforce"}]}
            payload = json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b" " * recipe["pad_count"]
            if len(payload) != recipe["utf8_bytes"]:
                raise ValueError("incorrect byte recipe length")
            limit = 16384 if recipe["surface"] == "policy" else 65536
            if (len(payload) > limit) != (case["state"] in {"policy-oversize", "registry-oversize"}):
                raise ValueError("incorrect resource boundary state")
        if not isinstance(case["covers"], list) or not case["covers"] or not all(isinstance(item, str) for item in case["covers"]) or not set(case["covers"]) <= REQUIREMENTS:
            raise ValueError("invalid requirement coverage")
    missing = REQUIRED_CASES - ids
    if missing:
        raise ValueError("missing required cases: " + ", ".join(sorted(missing)))
    for transport in TRANSPORTS:
        if not any(c["id"].startswith(transport + "-") and c["expected"]["verdict"] == "ALLOW" for c in cases):
            raise ValueError("missing allowed " + transport + " transport")
    return corpus


class SSHContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = validate(json.loads(FIXTURE.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs))

    def test_required_cases_and_coverage(self):
        self.assertEqual(REQUIRED_CASES - {c["id"] for c in self.corpus["cases"]}, set())
        self.assertEqual(set().union(*(set(c["covers"]) for c in self.corpus["cases"])), REQUIREMENTS)

    def test_duplicate_and_malformed_rules_rejected(self):
        for bad_id in ("", "Invalid ID", "../escape"):
            with self.subTest(bad_id=bad_id):
                changed = copy.deepcopy(self.corpus)
                changed["policy"]["rules"][0]["id"] = bad_id
                with self.assertRaisesRegex(ValueError, "malformed rule ID"):
                    validate(changed)
        changed = copy.deepcopy(self.corpus)
        changed["policy"]["rules"].append(copy.deepcopy(changed["policy"]["rules"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate rule ID"):
            validate(changed)
        changed = copy.deepcopy(self.corpus)
        changed["policy"]["rules"][0]["secret"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "invalid rule shape"):
            validate(changed)
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            json.loads('{"id":"one","id":"two"}', object_pairs_hook=unique_pairs)

    def test_unknown_and_missing_cases_cannot_be_relabelled_safe(self):
        changed = copy.deepcopy(self.corpus)
        changed["cases"] = [c for c in changed["cases"] if c["id"] != "joined-argv-injection"]
        with self.assertRaisesRegex(ValueError, "missing required cases"):
            validate(changed)
        changed = copy.deepcopy(self.corpus)
        next(c for c in changed["cases"] if c["id"] == "joined-argv-injection")["expected"]["verdict"] = "ALLOW"
        with self.assertRaisesRegex(ValueError, "unknown input cannot allow"):
            validate(changed)

    def test_reviewed_boundaries_are_exact(self):
        cases = {case["id"]: case for case in self.corpus["cases"]}
        for name in ("nested-root", "sibling-root"):
            self.assertEqual((cases[name]["mode"], cases[name]["expected"]["reason_id"], cases[name]["hook_effect"]), ("off", "feature-off", "silent"))
        for name in ("report-deny", "report-unknown", "report-digest-drift", "report-policy-limit-over"):
            self.assertEqual((cases[name]["mode"], cases[name]["hook_effect"]), ("report", "warn"))
        self.assertEqual(len(shlex.split(cases["token-limit-at"]["command"])), 512)
        self.assertGreater(len(shlex.split(cases["token-limit-over"]["command"])), 512)
        self.assertEqual(cases["token-limit-over"]["expected"]["reason_id"], "scan-incomplete")
        for name, limit in (("policy", 16384), ("registry", 65536)):
            self.assertEqual(cases[name + "-limit-at"]["input_bytes"]["utf8_bytes"], limit)
            self.assertEqual(cases[name + "-limit-over"]["input_bytes"]["utf8_bytes"], limit + 1)
            self.assertEqual(cases[name + "-limit-over"]["hook_effect"], "refuse")
        self.assertEqual(cases["depth-two"]["expected"]["verdict"], "ALLOW")
        self.assertEqual(cases["depth-three"]["expected"]["verdict"], "UNKNOWN")
        for name, wrappers in (("depth-two", 2), ("depth-three", 3)):
            command = cases[name]["command"]
            for _ in range(wrappers):
                argv = shlex.split(command)
                self.assertEqual(argv[:2], ["sh", "-c"])
                self.assertEqual(len(argv), 3)
                command = argv[2]
            self.assertEqual(shlex.split(command)[:2], ["ssh", "-p"])
        for case in self.corpus["cases"]:
            self.assertEqual(case["expected"]["schema_version"], 1)

    def test_invalid_result_and_binding_rejected(self):
        changed = copy.deepcopy(self.corpus)
        changed["cases"][0]["expected"].pop("schema_version")
        with self.assertRaisesRegex(ValueError, "invalid expected result"):
            validate(changed)
        changed = copy.deepcopy(self.corpus)
        next(c for c in changed["cases"] if c["id"] == "nested-root")["mode"] = "enforce"
        with self.assertRaisesRegex(ValueError, "unbound roots cannot inherit mode"):
            validate(changed)
        changed = copy.deepcopy(self.corpus)
        next(c for c in changed["cases"] if c["id"] == "policy-limit-over")["input_bytes"]["pad_count"] -= 1
        with self.assertRaisesRegex(ValueError, "incorrect byte recipe length"):
            validate(changed)

    def test_report_oversized_policy_warns(self):
        changed = copy.deepcopy(self.corpus)
        case = next(c for c in changed["cases"] if c["id"] == "policy-limit-over")
        case["mode"] = "report"
        case["hook_effect"] = "warn"
        validate(changed)

    def test_coverage_matrix(self):
        doc = (FIXTURE.parents[2] / "docs" / "ssh-policy.md").read_text(encoding="utf-8")
        matrix = doc.split("<!-- H1 coverage matrix -->", 1)[1].split("<!-- /H1 coverage matrix -->", 1)[0]
        rows = re.findall(r"^\| (M\d+) \|[^\n]+", matrix, re.MULTILINE)
        self.assertEqual(rows, [f"M{i:02d}" for i in range(1, 35)])
        references = set(re.findall(r"`([a-z][a-z0-9-]+)`", matrix))
        self.assertEqual(references, REQUIRED_CASES)
        self.assertEqual(references, {case["id"] for case in self.corpus["cases"]})
        self.assertEqual(re.findall(r"^\| (D\d+) \|", doc, re.MULTILINE),
                         [f"D{i:02d}" for i in range(1, 15)])

    def test_ssh_operation_root_allows_require_exact_argv(self):
        for operation in OPERATIONS["ssh"]:
            with self.subTest(operation=operation):
                policy = copy.deepcopy(self.corpus["policy"])
                policy["rules"] = [{"id": "broad-allow", "decision": "allow",
                                    "transport": "ssh", "destination": "site-stage",
                                    "operation": operation, "remote_root": "/srv/site/releases"}]
                with self.assertRaisesRegex(ValueError, "ssh allow requires exact argv"):
                    validate_policy(policy)

    def test_valid_policy_shapes(self):
        validate_policy(self.corpus["policy"])
        for transport, operations in OPERATIONS.items():
            for operation in operations:
                for decision in DECISIONS:
                    if decision == "allow" and transport == "ssh":
                        continue  # Requires exact argv, already exercised in sample.
                    with self.subTest(transport=transport, operation=operation, decision=decision):
                        policy = copy.deepcopy(self.corpus["policy"])
                        policy["rules"] = [{"id": "scoped-rule", "decision": decision,
                                            "transport": transport, "destination": "site-stage",
                                            "operation": operation, "remote_root": "/srv/site/releases"}]
                        self.assertIs(validate_policy(policy), policy)

    def test_invalid_policy_shapes(self):
        mutations = [
            (("schema_version",), True), (("schema_version",), 2),
            (("extra",), "not-in-schema"), (("destinations",), []),
            (("destinations", "site-stage", "port"), True),
            (("destinations", "site-stage", "port"), 0),
            (("destinations", "site-stage", "port"), 65536),
            (("destinations", "site-stage", "user"), ""),
            (("destinations", "site-stage", "host"), None),
            (("destinations", "site-stage", "host"), "*.example.test"),
            (("destinations", "site-stage", "user"), "$USER"),
            (("rules", 0, "decision"), "permit"), (("rules", 0, "decision"), []),
            (("rules", 0, "transport"), "shell"), (("rules", 0, "transport"), []),
            (("rules", 0, "destination"), "missing"), (("rules", 0, "destination"), []),
            (("rules", 0, "argv"), "cat /srv/site/VERSION"),
            (("rules", 0, "argv"), []), (("rules", 0, "argv"), ["cat", None]),
            (("rules", 0, "operation"), "read"),  # Mixed argv/root shape.
            (("rules", 3, "argv"), ["scp"]),
            (("rules", 5, "operation"), "execute"), (("rules", 5, "operation"), []),
        ]
        for root in ("relative", "~/site", "/", "/srv/../site", "/srv/./site", "/srv//site",
                     "/srv/site/", "/srv/*", "/srv/$SITE", "/srv/site?", "/srv/[site]"):
            mutations.append((("rules", 3, "remote_root"), root))
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                policy = copy.deepcopy(self.corpus["policy"])
                target = policy
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    validate_policy(policy)
        for payload in ('{"schema_version":1,}', '{"schema_version":1,"schema_version":1}',
                        '{"rules":[{"id":"one","id":"two"}]}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                json.loads(payload, object_pairs_hook=unique_pairs)

    def test_activation_outcomes_are_consistent(self):
        # Every invalid policy state retains the recovered mode; valid report allows silently.
        for original in self.corpus["cases"]:
            if original["state"] not in INVALID_POLICY or original["expected"]["applicability"] != "relevant":
                continue
            for mode, effect in (("report", "warn"), ("enforce", "refuse")):
                with self.subTest(case=original["id"], mode=mode):
                    changed = copy.deepcopy(self.corpus)
                    case = next(c for c in changed["cases"] if c["id"] == original["id"])
                    case.update(mode=mode, hook_effect=effect)
                    validate(changed)
                    case["hook_effect"] = "silent"
                    with self.assertRaisesRegex(ValueError, "inconsistent hook effect"):
                        validate(changed)
                    case["hook_effect"] = effect
                    case["expected"].update(verdict="ALLOW", reason_id="matched-allow", rule_id="version-read")
                    with self.assertRaisesRegex(ValueError, "ALLOW requires a bound allow rule"):
                        validate(changed)

    def test_invalid_outcomes_rejected(self):
        for field, value in (("schema_version", True), ("schema_version", 2),
                             ("applicability", "maybe"), ("verdict", "PASS"),
                             ("rule_id", "absent"), ("effect_scope", "remote-effects"),
                             ("reason_id", "not a stable id"), ("rule_id", "protect-current"),
                             ("verdict", []), ("applicability", []), ("rule_id", [])):
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(self.corpus)
                changed["cases"][0]["expected"][field] = value
                with self.assertRaises(ValueError):
                    validate(changed)
        for name, changes in (
            ("corrupt-registry", {"mode": "report", "hook_effect": "warn"}),
            ("nested-root", {"mode": "report"}),
            ("report-policy-limit-over", {"hook_effect": "refuse"}),
            ("report-allow", {"hook_effect": "warn"}),
            ("corrupt-registry-nonremote", {"hook_effect": "refuse"}),
        ):
            with self.subTest(case=name, changes=changes):
                changed = copy.deepcopy(self.corpus)
                next(c for c in changed["cases"] if c["id"] == name).update(changes)
                with self.assertRaises(ValueError):
                    validate(changed)

    def test_resource_and_precedence_inputs(self):
        cases = {case["id"]: case for case in self.corpus["cases"]}
        self.assertEqual(len(cases["command-limit-at"]["command"].encode("utf-8")), 65536)
        self.assertEqual(len(cases["command-limit-over"]["command"].encode("utf-8")), 65537)
        self.assertEqual(len(cases["byte-limit-multibyte"]["command"].encode("utf-8")), 65537)
        self.assertEqual(len(shlex.split(cases["token-limit-next"]["command"])), 513)
        self.assertGreater(cases["late-ssh-limit"]["command"].index("ssh"), 65536)
        with self.assertRaises(UnicodeEncodeError):
            cases["non-utf8"]["command"].encode("utf-8")
        self.assertTrue(cases["non-utf8"]["command"].encode("utf-8", "surrogateescape").endswith(b"\xff"))
        rules = {rule["id"]: rule for rule in self.corpus["policy"]["rules"]}
        self.assertEqual(rules["remove-current-allow"]["argv"], ["rm", rules["protect-current"]["remote_root"]])
        for name in ("protected-rm", "compound-deny", "rsync-deny-with-unsupported"):
            self.assertEqual(cases[name]["expected"]["reason_id"], "matched-deny")


if __name__ == "__main__":
    unittest.main()
