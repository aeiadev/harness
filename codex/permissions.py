"""Plan a reversible, text-preserving merge of the optional judge profile.

Only the public inserted block is recorded as ownership metadata. Existing config
contents never enter that metadata. Python 3.10 conservatively declines multiline
TOML strings and values; Python 3.11 and later use the standard TOML parser.
"""

import hashlib
import json
import re

try:
    import tomllib
except ImportError:  # Python 3.10 has no standard TOML parser.
    tomllib = None


_PROFILE = ("permissions", "harness-judge")
_MISSING = object()
_KEY = re.compile(r'''\s*("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_-]+)\s*''')
_START = "# >>> harness judge permissions >>>\n"
_END = "# <<< harness judge permissions <<<\n"


def _key_path(value):
    parts = []
    while value:
        match = _KEY.match(value)
        if not match:
            raise ValueError("unsupported TOML key")
        token = match.group(1)
        parts.append(json.loads(token) if token.startswith('"') else token.strip("'"))
        value = value[match.end():]
        if not value:
            break
        if not value.startswith("."):
            raise ValueError("unsupported TOML key")
        value = value[1:]
        if not value:
            raise ValueError("incomplete TOML key")
    if not parts:
        raise ValueError("empty TOML key")
    return tuple(parts)


def _line_code(line):
    """Remove comments and find unquoted equals signs in a single TOML line."""
    quote = None
    escaped = False
    equals = None
    depth = 0
    for index, char in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            line = line[:index]
            break
        elif char == "=" and equals is None:
            equals = index
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
    if quote or depth:
        raise ValueError("multiline TOML requires Python 3.11 or later")
    return line.strip(), equals


def _fallback_profile(text):
    """Recognize profile paths safely without implementing a full TOML parser."""
    if '\"\"\"' in text or "'''" in text:
        raise ValueError("multiline TOML requires Python 3.11 or later")
    table = ()
    rows = []
    found = False
    for raw in text.splitlines():
        code, _ = _line_code(raw)
        if not code:
            continue
        if code.startswith("["):
            array = code.startswith("[[")
            width = 2 if array else 1
            if not code.endswith("]" * width):
                raise ValueError("unsupported TOML table")
            table = _key_path(code[width:-width])
            if array and table[:1] == _PROFILE[:1]:
                raise ValueError("array permission tables are unsupported")
            if table[:2] == _PROFILE:
                rows.append((table, None))
                found = True
            continue
        _, equals = _line_code(code)
        if equals is None:
            raise ValueError("unsupported TOML assignment")
        path = table + _key_path(code[:equals])
        value = code[equals + 1:].strip()
        if not value:
            raise ValueError("empty TOML value")
        if path == _PROFILE[:1]:
            raise ValueError("inline permissions require Python 3.11 or later")
        if path[:2] == _PROFILE:
            rows.append((path, value))
            found = True
    return rows if found else _MISSING


def _profile(text):
    if tomllib is None:
        return _fallback_profile(text)
    data = tomllib.loads(text)
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        raise ValueError("permissions is not a table")
    return permissions.get("harness-judge", _MISSING)


def _block(fragment):
    sections = {}
    current = None
    for line in fragment.decode("utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            path = _key_path(stripped[1:-1])
            current = path if path in (_PROFILE + ("filesystem",), _PROFILE + ("network",)) else None
            if current:
                if current in sections:
                    raise ValueError("duplicate judge profile section")
                sections[current] = [line]
        elif current:
            sections[current].append(line)
    if len(sections) != 2:
        raise ValueError("judge profile needs filesystem and network sections")
    body = "\n\n".join("\n".join(lines).rstrip() for lines in sections.values()) + "\n"
    _profile(body)
    return (_START + body + _END).encode("utf-8")


def _owned_block(owned):
    try:
        block = owned["block"].encode("utf-8")
        separator = owned["separator"].encode("utf-8")
        valid = (block.startswith(_START.encode()) and block.endswith(_END.encode())
                 and separator in (b"", b"\n", b"\n\n")
                 and isinstance(owned["had_file"], bool)
                 and hashlib.sha256(block).hexdigest() == owned["sha256"])
        if valid:
            return block, separator
    except (KeyError, TypeError, AttributeError):
        pass
    raise ValueError("invalid judge profile ownership metadata")


def plan_install(original, fragment, owned=None):
    """Return (planned config bytes or None, ownership or None, message)."""
    try:
        if owned:
            block, _ = _owned_block(owned)
            if original is not None and original.count(block) == 1:
                return original, owned, "Judge profile already installed."
            return original, owned, "Judge profile was edited; left unchanged."
        existing = (original or b"").decode("utf-8")
        if _profile(existing) is not _MISSING:
            return original, None, "Existing harness-judge profile preserved."
        block = _block(fragment)
        separator = b"" if not original or original.endswith(b"\n\n") else b"\n" if original.endswith(b"\n") else b"\n\n"
        updated = (original or b"") + separator + block
        # This also refuses extending an inline permissions table on Python 3.11+.
        _profile(updated.decode("utf-8"))
    except (ValueError, UnicodeError):
        # Parser errors may quote private config values; expose no parser text.
        return original, owned, "Judge profile merge skipped: unsupported or invalid TOML; configure the session profile manually."
    ownership = {"block": block.decode("utf-8"), "separator": separator.decode("utf-8"),
                 "had_file": original is not None, "sha256": hashlib.sha256(block).hexdigest()}
    return updated, ownership, "Judge session profile added; select it with -c default_permissions=harness-judge."


def plan_uninstall(original, owned=None, dry_run=False):
    """Remove only an unchanged owned profile, preserving unrelated user edits."""
    if not owned or original is None:
        return original, "No owned judge profile to remove."
    try:
        block, separator = _owned_block(owned)
        if original.count(block) != 1 or _profile(original.decode("utf-8")) != _profile(block.decode("utf-8")):
            return original, "Judge profile was edited; preserved."
        index = original.index(block)
        before, after = original[:index], original[index + len(block):]
        if separator and before.endswith(separator):
            original_prefix = before[:-len(separator)]
            if (not after or not original_prefix or original_prefix.endswith(b"\n")
                    or after.startswith(b"\n")):
                before = original_prefix
        updated = before + after
        # A following assignment may have extended the removed table. Refuse any
        # resulting invalid TOML, even if its value matched an existing key.
        _profile(updated.decode("utf-8"))
        if not updated and not owned["had_file"]:
            updated = None
        return updated, "Would remove owned judge profile." if dry_run else "Owned judge profile removed."
    except (ValueError, UnicodeError):
        return original, "Judge profile removal skipped: config or ownership changed; preserved."
