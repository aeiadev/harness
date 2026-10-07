"""Small shell inspection helpers. These never execute the inspected command.

This is a guard for common command forms, not a complete shell interpreter.
"""

import re


def command_input(payload):
    """Normalize Claude and Codex shell hook inputs without running commands.

    Codex can report Bash/command or native exec_command and shell_command
    calls with cmd. Namespaced native calls use the same input shape.
    """
    if not isinstance(payload, dict):
        return None
    name = payload.get("tool_name")
    if name not in {"Bash", "exec_command", "shell_command",
                    "functions.exec_command", "functions.shell_command"}:
        return None
    args = payload.get("tool_input")
    if not isinstance(args, dict):
        return None
    key = "command" if name == "Bash" else "cmd"
    command = args.get(key, args.get("command"))
    if not isinstance(command, str):
        return None
    return command, args.get("run_in_background") is True


def quoted_spans(text):
    """Return start, content start, and content end for shell quote spans."""
    spans = []
    index = 0
    while index < len(text):
        quote = text[index]
        if quote == "\\":
            index += 2
            continue
        if quote not in "'\"":
            index += 1
            continue
        end = index + 1
        while end < len(text):
            if quote == '"' and text[end] == "\\":
                end += 2
                continue
            if text[end] == quote:
                break
            end += 1
        spans.append((index, index + 1, end))
        index = end + 1
    return spans


def strip_quoted_strings(text):
    """Blank quoted contents, preserving positions and quote delimiters."""
    chars = list(text)
    for _, start, end in quoted_spans(text):
        chars[start:end] = [" " if char != "\n" else "\n" for char in chars[start:end]]
    return "".join(chars)


def strip_heredoc_bodies(text):
    """Ignore heredoc data, including multiple heredocs on a command line."""
    lines = text.splitlines(keepends=True)
    result = []
    pending = []
    pattern = re.compile(r"(?<!<)<<(-)?\s*(?:'([^']+)'|\"([^\"]+)\"|([\w]+))")
    for line in lines:
        if pending:
            delimiter, tabs = pending[0]
            candidate = line.rstrip("\r\n")
            if tabs:
                candidate = candidate.lstrip("\t")
            if candidate == delimiter:
                pending.pop(0)
            result.append("\n" if line.endswith("\n") else "")
            continue
        result.append(line)
        spans = quoted_spans(line)
        for match in pattern.finditer(line):
            if any(start <= match.start() < end for _, start, end in spans):
                continue
            pending.append((next(group for group in match.groups()[1:] if group), bool(match[1])))
    return "".join(result)


def _balanced_end(text, start):
    """Return the end of a parenthesized expression without evaluating it."""
    depth, index, quote = 0, start, None
    while index < len(text):
        char = text[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return index


def _tokens(text):
    """Keep operator identity separate from quoted or escaped argument text."""
    operators = ("&>>", "<<-", "<<<", ";;&", "&&", "||", ";;", ";&", "|&", ">>", "<<", ">&", "<&", "<>", ">|", "&>")
    index = 0
    while index < len(text):
        if text[index] in " \t\r":
            index += 1
            continue
        if text[index] == "#":
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        start = index
        if text.startswith("((", index):
            index = _balanced_end(text, index)
            yield text[start:index], "word", start, index
            continue
        if text[index] in ";&|()<>\n":
            operator = next((op for op in operators if text.startswith(op, index)), text[index])
            index += len(operator)
            yield operator, "operator", start, index
            continue
        chars = []
        quoted = False
        while index < len(text) and text[index] not in " \t\r\n;&|()<>":
            char = text[index]
            if text.startswith("$(", index):
                end = _balanced_end(text, index + 1)
                chars.append(text[index:end])
                index = end
            elif char == "\\":
                quoted = True
                if index + 1 < len(text) and text[index + 1] != "\n":
                    chars.append(text[index + 1])
                index += 2
            elif char in "'\"":
                quoted = True
                quote = char
                index += 1
                while index < len(text) and text[index] != quote:
                    if quote == '"' and text.startswith("$(", index):
                        end = _balanced_end(text, index + 1)
                        chars.append(text[index:end])
                        index = end
                        continue
                    if quote == '"' and text[index] == "\\" and index + 1 < len(text) and text[index + 1] in '$`"\\\n':
                        index += 1
                        if text[index] != "\n":
                            chars.append(text[index])
                    else:
                        chars.append(text[index])
                    index += 1
                if index >= len(text):
                    return
                index += 1
            else:
                chars.append(char)
                index += 1
        value = "".join(chars)
        kind = "operator" if value in {"{", "}"} and not quoted else "word"
        yield value, kind, start, index


def segments(text):
    """Yield command words and control separators, excluding redirections.

    Quoted operator arguments remain words. Brace groups expose their inner
    command positions, and leading file-descriptor redirections are skipped.
    """
    tokens = list(_tokens(strip_heredoc_bodies(text)))
    words = []
    index = 0
    redirects = {"<", ">", ">>", "<<", "<<-", "<<<", ">&", "<&", "<>", ">|", "&>", "&>>"}
    while index < len(tokens):
        value, kind, start, end = tokens[index]
        if kind == "word" and value.isdigit() and index + 1 < len(tokens):
            next_value, next_kind, next_start, _ = tokens[index + 1]
            if next_kind == "operator" and next_value in redirects and end == next_start:
                index += 1
                value, kind, start, end = tokens[index]
        if kind == "operator" and value in redirects:
            index += 2  # Operator and its target are not command words.
            continue
        if kind == "operator":
            if words:
                yield words, value
            words = []
        else:
            words.append(value)
        index += 1
    if words:
        yield words, ""


def duration(value):
    """Accept a concrete, positive timeout duration, including common units."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", value)
    return bool(match and float(match[1]) > 0)


def unwrap(words):
    """Return command words, whether timeout bounds them, and sudo presence."""
    words = list(words)
    bounded = False
    elevated = False
    while words:
        command = words[0].rsplit("/", 1)[-1]
        if re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]) or command in {"then", "do", "else", "if", "elif", "!"}:
            words.pop(0)
        elif command in {"command", "builtin", "exec", "nohup", "env", "sudo"}:
            elevated |= command == "sudo"
            words.pop(0)
            while words and words[0].startswith("-"):
                option = words.pop(0)
                if option in {"-u", "-g", "-h", "-p", "-C", "-T", "--user", "--group", "--host", "--chdir", "--unset"} and words:
                    words.pop(0)
            # The next iteration also consumes env NAME=value assignments.
        elif command == "timeout":
            words.pop(0)
            while words and words[0].startswith("-"):
                option = words.pop(0)
                if option in {"-k", "--kill-after", "-s", "--signal"} and words:
                    words.pop(0)
            if not words or not duration(words.pop(0)):
                return words, False, elevated
            bounded = True
        else:
            break
    return words, bounded, elevated


def executed_string(words):
    """Return inline shell code only for commands which execute strings."""
    if not words:
        return None
    command = words[0].rsplit("/", 1)[-1]
    if command == "eval":
        return " ".join(words[1:])
    if command in {"bash", "sh", "dash", "zsh", "ksh"}:
        for index, word in enumerate(words[1:], 1):
            if word.startswith("-") and "c" in word[1:]:
                return words[index + 1] if index + 1 < len(words) else None
    return None


def substitutions(text):
    """Extract common command substitutions, including inside double quotes."""
    text = strip_heredoc_bodies(text)
    index = 0
    quoted = False
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            quoted = not quoted
            index += 1
            continue
        if char == "'" and not quoted:
            end = text.find("'", index + 1)
            index = len(text) if end < 0 else end + 1
            continue
        if char == "#" and not quoted and (index == 0 or text[index - 1].isspace()):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end + 1
            continue
        if char == "`":
            end = index + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                elif text[end] == "`":
                    yield text[index + 1:end]
                    break
                else:
                    end += 1
            index = end + 1
            continue
        if text.startswith("$(", index) and not text.startswith("$((", index):
            start = index + 2
            depth = 1
            end = start
            quote = None
            while end < len(text):
                current = text[end]
                if current == "\\" and quote != "'":
                    end += 2
                    continue
                if quote:
                    if current == quote:
                        quote = None
                elif current in "'\"":
                    quote = current
                elif current == "(":
                    depth += 1
                elif current == ")":
                    depth -= 1
                    if depth == 0:
                        yield text[start:end]
                        break
                end += 1
            index = end + 1
            continue
        index += 1


def background_groups(text):
    """Yield subshell or brace-group bodies launched in the background."""
    text = strip_heredoc_bodies(text)
    tokens = list(_tokens(text))
    stack = []
    closing = {")": "(", "}": "{"}
    redirects = {"<", ">", ">>", "<<", "<<-", "<<<", ">&", "<&", "<>", ">|", "&>", "&>>"}
    for index, (value, kind, start, end) in enumerate(tokens):
        if kind != "operator":
            continue
        if value in {"(", "{"}:
            stack.append((value, end))
        elif value in closing and stack and stack[-1][0] == closing[value]:
            _, body_start = stack.pop()
            following = index + 1
            while following < len(tokens):
                token, category, _, token_end = tokens[following]
                if category == "word" and token.isdigit() and following + 1 < len(tokens):
                    next_token, next_kind, next_start, _ = tokens[following + 1]
                    if next_kind == "operator" and next_token in redirects and token_end == next_start:
                        following += 1
                        token, category, _, _ = tokens[following]
                if category == "operator" and token in redirects:
                    following += 2
                    continue
                if category == "operator" and token == "&":
                    yield text[body_start:start]
                break
