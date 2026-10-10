#!/usr/bin/env python3
"""Read shell text from stdin; exit 1 for an unbounded wait loop, else 0.

--strip blanks quoted text and heredoc bodies for callers inspecting code.
Inline shell and eval strings are inspected as executable code. Ordinary
quoted arguments and heredoc data are ignored.
"""

import re
import sys

from shell_syntax import executed_string, segments, strip_heredoc_bodies, strip_quoted_strings, substitutions, unwrap

LOOP = re.compile(r"(?<![\w-])(until|while)\s+(.*?)(?:;|\n)\s*do\b(.*?)\bdone\b", re.S)
C_LOOP = re.compile(r"(?<![\w-])for\s*\(\(([^;]*);([^;]*);(.*?)\)\)\s*(?:;|\n)\s*do\b(.*?)\bdone\b", re.S)
COMPARISON = re.compile(r"(?:\$\{?)?\b([A-Za-z_]\w*)\}?\s*(-lt|-le|-gt|-ge|<=|>=|<|>)\s*")


def advanced(variable, body):
    name = re.escape(variable)
    for words, _ in segments(body):
        while words and words[0] in {"then", "do", "else"}:
            words = words[1:]
        if not words:
            continue
        if words[0] == "let":
            expression = " ".join(words[1:])
        elif words[0].startswith("((") and words[0].endswith("))"):
            expression = words[0][2:-2].strip()
        elif len(words) == 1 and words[0].startswith(variable + "="):
            expression = words[0]
        else:
            continue
        for sign, direction in ((r"\+", 1), ("-", -1)):
            if re.fullmatch(
                rf"{name}\s*=\s*\$\(\(\s*{name}\s*{sign}\s*[1-9]\d*\s*\)\)"
                rf"|(?:{name}\s*{sign}{sign}|{sign}{sign}\s*{name})"
                rf"|{name}\s*{sign}=\s*[1-9]\d*", expression):
                return direction
    return 0


def bounded(condition, body, kind):
    condition = condition.replace('"', "").replace("'", "")
    for comparison in COMPARISON.finditer(condition):
        variable, operator = comparison.groups()
        needed = 1 if operator in {"-lt", "-le", "<", "<="} else -1
        if kind == "until":
            needed *= -1
        direction = 1 if variable == "SECONDS" else advanced(variable, body)
        if direction == needed:
            return True
    # A condition can compare an explicit deadline to the clock on the right.
    reverse = re.search(r"(-lt|-le|-gt|-ge|<=|>=|<|>)\s*\$\{?SECONDS\}?\b", condition)
    return bool(reverse and (reverse[1] in {"-gt", "-ge", ">", ">="}) == (kind == "while"))


def unbounded_loop(command, depth=0):
    if depth > 12:
        return "shell nesting exceeds the inspection limit"
    text = strip_heredoc_bodies(command)
    for nested in substitutions(text):
        hit = unbounded_loop(nested, depth + 1)
        if hit:
            return hit
    for words, _ in segments(text):
        words, timed, _ = unwrap(words)
        nested = executed_string(words)
        if nested is not None and not timed:
            hit = unbounded_loop(nested, depth + 1)
            if hit:
                return hit
    # Keep quotes blanked for locating executable loops, but use the original
    # condition for comparisons such as [ "$attempt" -lt 5 ].
    code = strip_quoted_strings(text)
    code = re.sub(r"(?m)#.*$", lambda match: " " * len(match[0]), code)
    for match in C_LOOP.finditer(code):
        condition = text[match.start(2):match.end(2)]
        condition_code = match[2].strip()
        if condition_code == "0":
            continue
        body = text[match.start(4):match.end(4)]
        step = text[match.start(3):match.end(3)]
        waiting = bool(re.search(r"\b(?:sleep|wait)\b", match[4]))
        if not waiting and condition_code not in {"", "1"}:
            continue
        if bounded(condition, "((" + step + "));" + body, "while"):
            continue
        return "wait loop has no timeout or advancing counter"
    for match in LOOP.finditer(code):
        condition = text[match.start(2):match.end(2)]
        body = text[match.start(3):match.end(3)]
        body_code = match[3]
        condition_code = match[2].strip()
        if (match[1] == "while" and condition_code == "false") or (match[1] == "until" and condition_code in {"true", ":"}):
            continue
        waiting = bool(re.search(r"\b(?:sleep|wait)\b", body_code + " " + condition_code))
        unconditional = condition_code in {"true", ":", "false"}
        if not waiting and not unconditional:
            continue
        if bounded(condition, body, match[1]):
            continue
        if re.search(r"\bread\b", condition_code) and re.match(r"\s*<\s*\S", text[match.end():]):
            continue
        return "wait loop has no timeout or advancing counter"
    return None


def main():
    command = sys.stdin.read()
    if len(sys.argv) > 1 and sys.argv[1] == "--strip":
        sys.stdout.write(strip_quoted_strings(strip_heredoc_bodies(command)))
        return 0
    reason = unbounded_loop(command)
    if reason:
        print(reason)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
