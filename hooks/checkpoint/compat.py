"""The only place Harness hooks touch POSIX-only calls; each has a Windows path.

Standard library only, Python 3.9 syntax. Windows limits: no O_NOFOLLOW (an lstat and
fstat compare stands in), no owner check (profile ACLs guard the files), no POSIX modes.
"""

import os
from pathlib import Path
import posixpath
import re
import time

WINDOWS = os.name == "nt"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
READ_FLAGS = (os.O_RDONLY | NONBLOCK | NOFOLLOW
              | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0))
DRIVE = re.compile(r"([A-Za-z]):(?=[\\/]|$)")


def open_read(path):
    """Open a regular file for binary reading without following a final link."""
    # Without O_NOFOLLOW, a link (or a swap) shows as a different file before and after the open.
    before = None if NOFOLLOW else os.lstat(path)
    descriptor = os.open(path, READ_FLAGS)
    try:
        after = os.fstat(descriptor)
        if before is not None and (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError("file changed while opening")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def owned(info):
    """True when the stat result belongs to this user; Windows has no euid, so True."""
    geteuid = getattr(os, "geteuid", None)
    return geteuid is None or info.st_uid == geteuid()


def set_private(target, mode):
    """Apply a POSIX mode where supported; Windows fchmod only changes the read-only flag."""
    if WINDOWS:
        return
    if isinstance(target, int):
        change = getattr(os, "fchmod", None)
    else:
        change = None if WINDOWS else getattr(os, "chmod", None)
    if change is not None:
        change(target, mode)


def replace(source, target, attempts=8, pause=0.01):
    """os.replace, retried while Windows still holds the target open (PermissionError)."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(pause * (attempt + 1))


def write_text(path, text):
    """Write UTF-8 with the newlines given (LF stays LF on Windows too)."""
    Path(path).write_bytes(text.encode("utf-8"))


def posix_form(path):
    """C:\\x and C:/x become /c/x; other backslashes become / on Windows only."""
    text = os.fspath(path)
    drive = DRIVE.match(text)
    if drive or WINDOWS:
        text = text.replace("\\", "/")
    if drive:
        text = "/" + drive.group(1).lower() + text[2:]
    return text


def native_form(path):
    """/c/x becomes c:/x on Windows, for a filesystem call; unchanged elsewhere."""
    text = os.fspath(path)
    if WINDOWS and re.match(r"/[A-Za-z](/|$)", text):
        return text[1] + ":" + (text[2:] or "/")
    return text


def home_forms(environ=None):
    """Every spelling of this user's home, lower case: /c/x and c:/x for a drive path."""
    environ = os.environ if environ is None else environ
    found = {environ.get("HOME", ""), environ.get("USERPROFILE", "")}
    try:
        found.add(str(Path.home()))
    except (KeyError, OSError, RuntimeError):
        pass
    try:
        import pwd
        found.add(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, AttributeError, KeyError):
        pass
    forms = set()
    for home in found:
        posix = posix_form(home) if home else ""
        if not posix.startswith("/"):
            continue
        posix = posixpath.normpath(posix).lower()
        if posix.startswith("//"):
            posix = "/" + posix.lstrip("/")
        forms.add(posix)
        drive = re.match(r"/([a-z])(/|$)", posix)
        if drive:
            forms.add(drive.group(1) + ":" + (posix[2:] or "/"))
    return forms


def home() -> Path:
    """$HOME when set (POSIX, Git Bash, Claude Code on Windows), else Path.home(); Windows Python's
    Path.home() reads USERPROFILE only. A Git Bash /c/... form becomes C:/... on Windows."""
    value = os.environ.get("HOME", "")
    if not value:
        return Path.home()
    if os.name == "nt" and len(value) >= 3 and value[0] == "/" and value[1].isalpha() and value[2] == "/":
        value = f"{value[1].upper()}:{value[2:]}"
    return Path(value)


def state_is_absolute(path) -> bool:
    """An absolute state directory; UNC paths need a server and a nonempty share."""
    text = str(path).replace("\\", "/")
    if os.name == "nt" and text.startswith("//"):
        parts = text[2:].split("/")
        if len(parts) < 2 or not all(parts[:2]):
            return False
    return Path(path).is_absolute()
