"""Small, optional local facts for checkpoint recovery."""

import os
import subprocess
import time


def free_facts(anchor, document):
    """Return one bounded line, or nothing if Git or the checkpoint is unavailable."""
    try:
        deadline = time.monotonic() + 1

        def git(*args, check=True):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            result = subprocess.run(["git", *args], cwd=anchor, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=remaining, check=check)
            return result.stdout

        branch = git("symbolic-ref", "--quiet", "--short", "HEAD",
                     check=False).decode("utf-8", "replace").strip() or "detached"
        head = git("rev-parse", "--short", "HEAD").decode("ascii", "replace").strip()
        dirty = len(git("status", "--porcelain").splitlines())
        age = max(0, int(time.time() - os.stat(document).st_mtime))
        age_text = f"{age // 86400}d" if age >= 86400 else (f"{age // 3600}h" if age >= 3600
                                                         else f"{age // 60}m")
        branch = "".join(char if char.isprintable() else "?" for char in branch)
        fixed = f" HEAD={head} dirty={dirty} STATE.md age={age_text}"
        room = 200 - len(("Git: " + fixed + "\n").encode())
        if room < 0:
            return ""
        branch = branch.encode("utf-8")[:room].decode("utf-8", "ignore")
        return f"Git: {branch}{fixed}\n"
    except (OSError, ValueError, subprocess.SubprocessError, TimeoutError):
        return ""
