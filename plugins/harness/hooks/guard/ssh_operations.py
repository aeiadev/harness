"""Bounded, literal remote invocation classifier. Inspected text is never executed."""

import re

MAX_BYTES = 65536
MAX_TOKENS = 512
SEPARATORS = {";", "&&", "||", "\n"}
SHELL_SPECIAL = set(";&|()<>\n")
SHELL_BLANK = " \t"
DELETE_FLAGS = {"--delete", "--delete-before", "--delete-during", "--delete-delay",
                "--delete-after", "--delete-excluded", "--delete-missing-args"}
O_VALUES = {"BatchMode": "yes", "IdentitiesOnly": "yes", "StrictHostKeyChecking": "yes"}


class ParseFailure(Exception):
    def __init__(self, reason, relevance="unknown"):
        self.reason = reason
        self.relevance = relevance


def _result(applicability, verdict, reason, rule=None):
    result = {"schema_version": 1, "applicability": applicability, "verdict": verdict,
              "reason_id": reason, "rule_id": rule, "effect_scope": "invocation_only"}
    return result


def _unknown(reason, relevance="unknown"):
    return _result(relevance, "UNKNOWN", reason)


def _lex(source, remote=False):
    """Return value, dynamic marker, source span, operator flag, and provenance."""
    out = []
    i = 0
    while i < len(source):
        if source[i] in SHELL_BLANK:
            i += 1
            continue
        start = i
        if source[i] == "#":
            raise ParseFailure("unsupported-remote-syntax" if remote else "unsupported-shell-syntax")
        if source[i] in SHELL_SPECIAL:
            op = "&&" if source.startswith("&&", i) else "||" if source.startswith("||", i) else source[i]
            i += len(op)
            out.append((op, False, start, i, True, frozenset()))
            if len(out) > MAX_TOKENS:
                raise ParseFailure("scan-incomplete")
            continue
        chars = []
        word_started = False
        dynamic = False
        provenance = set()
        while i < len(source) and source[i] not in SHELL_BLANK and source[i] not in SHELL_SPECIAL:
            ch = source[i]
            if ch == "#" and not word_started:
                raise ParseFailure("unsupported-remote-syntax" if remote else "unsupported-shell-syntax")
            if ch == "\\":
                provenance.add("escape")
                if i + 1 >= len(source):
                    raise ParseFailure("malformed-quoting" if not remote else "unsupported-remote-syntax")
                if source[i + 1] != "\n":
                    chars.append(source[i + 1])
                    word_started = True
                i += 2
            elif ch in "'\"":
                word_started = True  # Even empty quotes deliberately form a word.
                quote = ch
                provenance.add("single-quote" if quote == "'" else "double-quote")
                i += 1
                while i < len(source) and source[i] != quote:
                    ch = source[i]
                    if quote == '"' and ch == "\\" and i + 1 < len(source) and source[i + 1] in '$`"\\\n':
                        provenance.add("escape")
                        i += 1
                        if source[i] != "\n":
                            chars.append(source[i])
                    else:
                        chars.append(ch)
                        if quote == '"' and ch in "$`":
                            dynamic = True
                    i += 1
                if i >= len(source):
                    raise ParseFailure("malformed-quoting" if not remote else "unsupported-remote-syntax")
                i += 1
            else:
                chars.append(ch)
                if ch == "~" and not word_started:
                    provenance.add("tilde-expansion")
                    dynamic = True
                if ch in "$`*?[":
                    dynamic = True
                word_started = True
                i += 1
        # Unquoted backslash-newline is removed before token formation. It
        # cannot create an empty command/argument or consume the token budget.
        if not word_started:
            continue
        out.append(("".join(chars), dynamic, start, i, False, frozenset(provenance)))
        if len(out) > MAX_TOKENS:
            raise ParseFailure("scan-incomplete")
    return out


def _canonical(path, directory=False):
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        return False
    if directory:
        if not path.endswith("/"):
            return False
        path = path[:-1]
    return all(part and part not in (".", "..") for part in path[1:].split("/"))


def _within(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def _identity(host, user, port, policy):
    return [label for label, value in policy.get("destinations", {}).items()
            if value.get("host") == host and value.get("user") == user and value.get("port") == port]


def _destination(word):
    if word.count("@") > 1 or not re.fullmatch(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.:-]*", word):
        raise ParseFailure("unsupported-transfer-shape")
    if "@" in word:
        return word.split("@", 1)
    return None, word


def _options(words, transport):
    flags = []
    values = {}
    seen_o = {}
    i = 0
    allowed_values = {"ssh": "plio", "scp": "Pio"}.get(transport, "")
    allowed_flags = {"ssh": "Tn", "scp": "p"}.get(transport, "")
    if transport == "rsync":
        while i < len(words) and words[i].startswith("-"):
            value = words[i]
            if value in ("-a", "--archive", "--dry-run") or value in DELETE_FLAGS or value == "--remove-source-files":
                flags.append(value)
            elif value == "--relative":
                flags.append("unsupported")
            elif value in ("-e", "--rsh", "--rsync-path"):
                flags.append("unsupported")
                i += 1
                if i >= len(words):
                    raise ParseFailure("missing-option-value")
            elif value.startswith(("--rsh=", "--rsync-path=")):
                flags.append("unsupported")
            else:
                raise ParseFailure("unsupported-option")
            i += 1
        return i, values, flags
    while i < len(words) and words[i].startswith("-"):
        word = words[i]
        if not word.startswith("-") or word.startswith("--"):
            raise ParseFailure("unsupported-option")
        group = word[1:]
        if not group:
            raise ParseFailure("unsupported-option")
        j = 0
        while j < len(group):
            opt = group[j]
            if opt in allowed_flags:
                flags.append(opt)
                j += 1
                continue
            if opt not in allowed_values:
                raise ParseFailure("unsupported-option")
            if j + 1 < len(group):
                value = group[j + 1:]
            else:
                i += 1
                if i >= len(words):
                    raise ParseFailure("missing-option-value")
                value = words[i]
            if not value or value.startswith("-"):
                raise ParseFailure("invalid-option-value")
            if opt == "o":
                name, sep, setting = value.partition("=")
                if not sep or (name in O_VALUES and setting != O_VALUES[name]) or (name == "UserKnownHostsFile" and (not setting or "$" in setting or "`" in setting)) or name not in set(O_VALUES) | {"UserKnownHostsFile"}:
                    raise ParseFailure("unsupported-option")
                if name in seen_o and seen_o[name] != setting:
                    raise ParseFailure("unsupported-option")
                seen_o[name] = setting
            elif opt in ("p", "P"):
                digits = value.lstrip("0")
                if not value.isascii() or not value.isdecimal() or len(digits) > 5 or not digits or int(digits) > 65535:
                    raise ParseFailure("invalid-option-value")
                value = int(digits)
            previous = values.get(opt)
            if previous is not None and previous != value:
                if opt != "o":
                    raise ParseFailure("conflicting-destination" if opt in "plP" else "unsupported-option")
            values[opt] = value
            break
        i += 1
    return i, values, flags


def _remote(remote_text, policy):
    try:
        tokens = _lex(remote_text, remote=True)
    except ParseFailure as failure:
        raise ParseFailure("scan-incomplete" if failure.reason == "scan-incomplete" else "unsupported-remote-syntax")
    if not tokens or any(token[4] for token in tokens):
        raise ParseFailure("unsupported-remote-syntax")
    argv = [token[0] for token in tokens]
    if argv[0] in ("sh", "bash", "ssh", "scp", "rsync"):
        raise ParseFailure("unsupported-remote-syntax")
    if any(token[1] for token in tokens):
        # Keep H1's path-specific reason for tilde operands of builtins; an
        # opaque exact-argv invocation must reject expansion just as strictly.
        if argv[0] in ("cat", "test", "mkdir", "mv", "rm") and any(
                "tilde-expansion" in token[5] for token in tokens[1:]):
            raise ParseFailure("noncanonical-remote-path")
        if any("$(" in token[0] or "`" in token[0] for token in tokens):
            raise ParseFailure("unsupported-remote-syntax")
        raise ParseFailure("dynamic-remote-argument")
    cmd, args = argv[0], argv[1:]
    paths = []
    operation = None
    if cmd == "cat":
        if args[:1] == ["--"]:
            args = args[1:]
        paths, operation = args, "read"
        valid = len(args) == 1 and not any(x.startswith("-") for x in args)
    elif cmd == "test":
        paths, operation = args[1:], "read"
        valid = len(args) == 2 and args[0] in ("-e", "-f", "-d")
    elif cmd == "mkdir":
        rest = list(args)
        if rest[:1] == ["-p"]:
            rest.pop(0)
        if rest[:1] == ["--"]:
            rest.pop(0)
        if rest and rest[0].startswith("-"):
            raise ParseFailure("unsupported-remote-syntax", "relevant")
        paths, operation = rest, "create-dir"
        valid = len(rest) == 1 and not any(x.startswith("-") for x in rest)
    elif cmd == "mv":
        rest = args[1:] if args[:1] == ["--"] else args
        paths, operation = rest, "rename"
        valid = len(rest) == 2 and not any(x.startswith("-") for x in rest)
    elif cmd == "rm":
        rest = list(args)
        while rest and rest[0].startswith("-") and rest[0] != "--" and re.fullmatch(r"-[frR]+", rest[0]):
            rest.pop(0)
        if rest[:1] == ["--"]:
            rest.pop(0)
        paths, operation = rest, "remove"
        valid = bool(rest) and not any(x.startswith("-") for x in rest)
    else:
        declared = any(r.get("transport") == "ssh" and "argv" in r and r["argv"] and r["argv"][0] == cmd for r in policy.get("rules", []))
        if not declared:
            raise ParseFailure("unsupported-remote-syntax", "relevant" if cmd.startswith("/") or cmd == "touch" else "unknown")
        return argv, None, []
    if any(not _canonical(path) for path in paths if not path.startswith("-")):
        raise ParseFailure("noncanonical-remote-path")
    if not valid:
        raise ParseFailure("unsupported-remote-syntax", "relevant")
    return argv, operation, paths


def _evaluate(transport, labels, argv, operations, paths, policy):
    if not labels:
        return _result("relevant", "BLOCK", "unmatched-destination")
    if isinstance(operations, str) or operations is None:
        operations = {operations}
    matches = []
    for rule in policy.get("rules", []):
        if rule.get("transport") != transport or rule.get("destination") not in labels:
            continue
        if "argv" in rule:
            match = transport == "ssh" and argv == rule["argv"]
        else:
            match = ("operation" not in rule or rule["operation"] in operations) and any(_within(p.rstrip("/"), rule["remote_root"]) for p in paths)
        if match:
            matches.append(rule)
    for decision in ("deny", "allow"):
        for rule in matches:
            if rule["decision"] == decision:
                return _result("relevant", "BLOCK" if decision == "deny" else "ALLOW",
                               "matched-deny" if decision == "deny" else "matched-allow", rule["id"])
    return _result("relevant", "BLOCK", "no-matching-rule")


def _client(transport, words, policy):
    try:
        index, opts, flags = _options(words[1:], transport)
        operands = words[1 + index:]
        if transport == "ssh":
            if not operands:
                raise ParseFailure("interactive-session", "relevant")
            declared_user, host = _destination(operands[0])
            if declared_user and opts.get("l") and declared_user != opts["l"]:
                raise ParseFailure("conflicting-destination")
            defaults = next((d for d in policy.get("destinations", {}).values() if d["host"] == host), None)
            user = declared_user or opts.get("l") or (defaults or {}).get("user")
            port = opts.get("p", (defaults or {}).get("port", 22))
            if len(operands) < 2:
                raise ParseFailure("interactive-session", "relevant")
            argv, operation, paths = _remote(" ".join(operands[1:]), policy)
            return _evaluate("ssh", _identity(host, user, port, policy), argv, operation, paths, policy)
        if transport == "scp":
            if any(x.startswith("-") for x in operands):
                raise ParseFailure("unsupported-option")
            if len(operands) != 2 or ":" in operands[0] or ":" not in operands[1]:
                raise ParseFailure("unsupported-transfer-shape")
            target, path = operands[1].split(":", 1)
            user, host = _destination(target)
            if not user:
                raise ParseFailure("unsupported-transfer-shape")
            if not _canonical(path):
                raise ParseFailure("noncanonical-remote-path")
            defaults = next((d for d in policy.get("destinations", {}).values() if d["host"] == host), None)
            port = opts.get("P", (defaults or {}).get("port", 22))
            return _evaluate("scp", _identity(host, user, port, policy), None, "upload", [path], policy)
        if any(x.startswith("-") for x in operands):
            raise ParseFailure("unsupported-option")
        if len(operands) != 2 or ":" in operands[0] or not operands[0].endswith("/") or ":" not in operands[1] or "::" in operands[1] or operands[1].startswith("rsync://"):
            raise ParseFailure("unsupported-transfer-shape")
        target, path = operands[1].split(":", 1)
        user, host = _destination(target)
        if not user or not path.endswith("/"):
            raise ParseFailure("unsupported-transfer-shape")
        if not _canonical(path, directory=True):
            raise ParseFailure("noncanonical-remote-path")
        defaults = next((d for d in policy.get("destinations", {}).values() if d["host"] == host), None)
        label = _identity(host, user, (defaults or {}).get("port", 22), policy)
        operations = set()
        if any(f in DELETE_FLAGS for f in flags):
            operations.add("delete")
        if "--remove-source-files" in flags:
            operations.add("remove")
        if operations:
            denied = _evaluate("rsync", label, None, operations, [path], policy)
            if denied["reason_id"] == "matched-deny":
                return denied
            return _unknown("unsupported-option")
        if "unsupported" in flags:
            return _unknown("unsupported-option")
        return _evaluate("rsync", label, None, "upload", [path], policy)
    except ParseFailure as failure:
        return _unknown(failure.reason, failure.relevance)


def _segment(tokens, policy, depth):
    # Never form argv by deleting unsupported shell operators. A known deny
    # may still come from a different, fully parsed clause.
    if any(token[4] for token in tokens):
        return _unknown("unsupported-shell-syntax")
    words = [t[0] for t in tokens]
    if not words:
        return _unknown("scan-incomplete", "unknown")
    # Consume argv wrappers iteratively. Only literal shell reparses recurse,
    # and those are capped at depth 2 independently of each level's 512 tokens.
    i = 0
    while words[i] in ("command", "env", "timeout"):
        wrapper = words[i]
        if wrapper == "command":
            if i + 1 == len(words) or words[i + 1].startswith("-"):
                return _unknown("unsupported-wrapper")
            i += 1
        elif wrapper == "env":
            i += 1
            while i < len(words) and "=" in words[i] and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[i]):
                if tokens[i][1]:
                    return _unknown("dynamic-wrapper")
                if words[i].startswith("RSYNC_RSH="):
                    return _unknown("dynamic-transport")
                i += 1
            if i == len(words) or words[i].startswith("-"):
                return _unknown("scan-incomplete", "unknown")
        else:
            if i + 2 >= len(words):
                return _unknown("scan-incomplete", "unknown")
            duration = words[i + 1]
            if (not duration.isascii() or not duration.isdecimal()
                    or not duration.strip("0") or len(duration.strip("0")) > 4300):
                return _unknown("unsupported-wrapper")
            i += 2
    tokens, words = tokens[i:], words[i:]
    if words[0] in ("sh", "bash"):
        if len(words) != 3 or words[1] != "-c":
            return _unknown("unsupported-wrapper")
        if depth >= 2:
            return _unknown("scan-incomplete", "unknown")
        if tokens[2][1]:
            return _unknown("dynamic-wrapper")
        return _inspect(words[2], policy, depth + 1)
    if words[0] not in ("ssh", "scp", "rsync"):
        if any(t[1] for t in tokens):
            return _unknown("unsupported-shell-syntax")
        return _result("nonrelevant", "ALLOW", "nonrelevant")
    if any(t[1] for t in tokens):
        return _unknown("unsupported-shell-syntax")
    return _client(words[0], words, policy)


def _inspect(command, policy, depth=0):
    if len(command.encode("utf-8", "surrogatepass")) > MAX_BYTES:
        return _unknown("scan-incomplete", "unknown")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in command):
        return _unknown("invalid-encoding", "unknown")
    try:
        tokens = _lex(command)
    except ParseFailure as failure:
        return _unknown(failure.reason, "unknown")
    if not tokens:
        return _result("nonrelevant", "ALLOW", "nonrelevant")
    segments = []
    current = []
    unsupported = False
    for token in tokens:
        if token[4]:
            if token[0] in SEPARATORS:
                if not current:
                    return _unknown("unsupported-shell-syntax")
                segments.append(current)
                current = []
            else:
                unsupported = True
                current.append(token)
        else:
            current.append(token)
    if current:
        segments.append(current)
    elif tokens[-1][4]:
        return _unknown("scan-incomplete", "unknown")
    decisions = [_segment(s, policy, depth) for s in segments]
    for decision in decisions:
        if decision["verdict"] == "BLOCK" and decision["reason_id"] == "matched-deny":
            return decision
    if unsupported:
        return _unknown("unsupported-shell-syntax")
    for decision in decisions:
        if decision["verdict"] == "UNKNOWN":
            return decision
    for decision in decisions:
        if decision["verdict"] == "BLOCK":
            return decision
    for decision in decisions:
        if decision["applicability"] == "relevant":
            return decision
    return _result("nonrelevant", "ALLOW", "nonrelevant")


def inspect_remote(command: str, policy: dict, *, cwd: str) -> dict:
    """Classify a complete literal command under an already validated policy."""
    if not isinstance(command, str):
        return _unknown("invalid-encoding", "unknown")
    return _inspect(command, policy)
