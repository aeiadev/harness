#!/usr/bin/env python3
"""hooks/checkpoint/compat.py and its call sites, on Linux, under tests/win_sim and on Windows.

PYTHONPATH=tests/win_sim python3 tests/test_compat.py hides pwd, os.geteuid, os.getuid,
os.mkfifo, os.fchmod, os.O_NOFOLLOW and os.O_NONBLOCK, as native Windows Python does.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from _platform import BASH  # noqa: E402
sys.path.insert(0, str(ROOT / "hooks/checkpoint"))
import compat  # noqa: E402
import storage  # noqa: E402

SIM = os.environ.get("HARNESS_WIN_SIM") == "1"
GUARD = ROOT / "hooks/guard/danger_cmd_guard.py"
HELPERS = ROOT / "hooks/helpers"
POSIX_ONLY = {"geteuid", "getuid", "mkfifo", "fchmod", "O_NOFOLLOW", "O_NONBLOCK"}


def banned_uses(source, filename="<fixture>"):
    """Each POSIX-only use in the source as 'line: form'. A syntax tree walk, so comments and strings
    never count, and a renamed os module (import os as o) is followed like os itself."""
    tree = ast.parse(source, filename)
    modules = {"os"} | {alias.asname for node in ast.walk(tree) if isinstance(node, ast.Import)
                        for alias in node.names if alias.name == "os" and alias.asname}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node, "import " + alias.name) for alias in node.names
                      if alias.name == "pwd" or alias.name.startswith("pwd.")]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            if module == "pwd" or module.startswith("pwd."):
                found.append((node, "from pwd import"))
            elif module == "os":
                found += [(node, "from os import " + alias.name) for alias in node.names
                          if alias.name in POSIX_ONLY or alias.name == "*"]
        elif (isinstance(node, ast.Attribute) and node.attr in POSIX_ONLY
              and isinstance(node.value, ast.Name) and node.value.id in modules):
            found.append((node, f"{node.value.id}.{node.attr}"))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
              and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in modules
              and isinstance(node.args[1], ast.Constant) and node.args[1].value in POSIX_ONLY):
            found.append((node, f"getattr({node.args[0].id}, {node.args[1].value!r})"))
    return [f"{node.lineno}: {form}" for node, form in sorted(found, key=lambda item: item[0].lineno)]


# The vendored Router-owned shared helper: its parity and native Windows behaviour are tested elsewhere.
VENDORED_ROUTER_HELPER = Path("scripts/commands_install.py")


def shipped_python(root=ROOT):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative == VENDORED_ROUTER_HELPER:
            continue
        if relative.parts[0] in {"tests", ".git", "router"} or path.name == "compat.py" and relative.parent.name == "checkpoint":
            continue
        yield path


class Compat(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Resolved: storage refuses a link above its folder, and TMPDIR may be one (macOS /var).
        self.base = Path(self.temp.name).resolve()

    @unittest.skipUnless(SIM, "only under PYTHONPATH=tests/win_sim")
    def test_simulation_hides_the_posix_names(self):
        for name in ("geteuid", "getuid", "mkfifo", "fchmod", "O_NOFOLLOW", "O_NONBLOCK"):
            self.assertFalse(hasattr(os, name), name)
        with self.assertRaises(ImportError):
            import pwd  # noqa: F401

    def test_read_flags_take_what_the_platform_has(self):
        for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_BINARY", "O_CLOEXEC"):
            self.assertEqual(bool(compat.READ_FLAGS & getattr(os, name, 0)), hasattr(os, name), name)
        self.assertEqual(compat.NOFOLLOW, getattr(os, "O_NOFOLLOW", 0))

    def test_open_read_refuses_a_final_link_with_or_without_o_nofollow(self):
        target = self.base / "target"
        target.write_bytes(b"one\ntwo\n")
        link = self.base / "link"
        try:
            link.symlink_to(target)
        except OSError:
            print(f"SKIP {self.id()}: this account cannot create symbolic links", file=sys.stderr, flush=True)
            self.skipTest("this account cannot create symbolic links")
        with compat.open_read(target) as stream:
            self.assertEqual(stream.read(), b"one\ntwo\n")
        for flags in ((compat.NOFOLLOW,) if compat.NOFOLLOW else ()) + (0,):
            with self.subTest(nofollow=flags), mock.patch.object(compat, "NOFOLLOW", flags), \
                    mock.patch.object(compat, "READ_FLAGS", compat.READ_FLAGS & ~compat.NOFOLLOW | flags):
                with self.assertRaises(OSError):
                    compat.open_read(link)

    def test_open_read_refuses_a_swap_between_lstat_and_open(self):
        first, second = self.base / "first", self.base / "second"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        real = os.lstat
        with mock.patch.object(compat, "NOFOLLOW", 0), \
                mock.patch.object(compat.os, "lstat", lambda path: real(second)):
            with self.assertRaises(OSError):
                compat.open_read(first)

    def test_storage_reads_through_compat(self):
        path = self.base / "data.json"
        path.write_bytes(b'{"a": 1}\n')
        self.assertEqual(storage.guarded_read(path, 100), (b'{"a": 1}\n', True))
        self.assertEqual(storage.json_object(path), {"a": 1})
        target = self.base / "private" / "out.json"
        storage.replace_private(target, b"x\n")
        self.assertEqual(target.read_bytes(), b"x\n")

    def test_owner_check(self):
        if hasattr(os, "geteuid"):
            self.assertTrue(compat.owned(SimpleNamespace(st_uid=os.geteuid())))
            self.assertFalse(compat.owned(SimpleNamespace(st_uid=os.geteuid() + 1)))
        else:
            print(f"SKIP {self.id()} POSIX owner assertions: no effective user ID on this OS", file=sys.stderr, flush=True)
        with mock.patch.object(compat, "os", SimpleNamespace()):
            self.assertTrue(compat.owned(SimpleNamespace(st_uid=12345)))

    def test_set_private_skips_missing_calls(self):
        path = self.base / "file"
        path.write_bytes(b"")
        descriptor = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        compat.set_private(descriptor, 0o600)
        compat.set_private(path, 0o600)
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        else:
            print(f"SKIP {self.id()} private mode assertion: no POSIX file modes on this OS", file=sys.stderr, flush=True)

    def test_replace_retries_permission_errors(self):
        source, target = self.base / "source", self.base / "target"
        source.write_bytes(b"new")
        real, calls = os.replace, []

        def flaky(a, b):
            calls.append(a)
            if len(calls) < 3:
                raise PermissionError("in use")
            real(a, b)

        with mock.patch.object(compat.os, "replace", flaky):
            compat.replace(source, target, pause=0)
        self.assertEqual((len(calls), target.read_bytes()), (3, b"new"))
        with mock.patch.object(compat.os, "replace", mock.Mock(side_effect=PermissionError)) as stuck:
            with self.assertRaises(PermissionError):
                compat.replace(source, target, attempts=4, pause=0)
        self.assertEqual(stuck.call_count, 4)

    def test_text_is_written_as_utf8_with_lf(self):
        path = self.base / "out.txt"
        compat.write_text(path, "a\nb \u754c\n")
        self.assertEqual(path.read_bytes(), "a\nb \u754c\n".encode("utf-8"))

    def test_path_forms(self):
        self.assertEqual(compat.posix_form("C:\\Users\\Ann"), "/c/Users/Ann")
        self.assertEqual(compat.posix_form("D:/x/y"), "/d/x/y")
        self.assertEqual(compat.posix_form("/home/ann"), "/home/ann")
        self.assertEqual(compat.posix_form("c:"), "/c")
        with mock.patch.object(compat, "WINDOWS", True):
            self.assertEqual(compat.native_form("/c/Users/Ann"), "c:/Users/Ann")
        with mock.patch.object(compat, "WINDOWS", False):
            self.assertEqual(compat.native_form("/c/Users/Ann"), "/c/Users/Ann")

    def test_home_forms_cover_every_spelling(self):
        forms = compat.home_forms({"HOME": "/c/Users/Ann", "USERPROFILE": "C:\\Users\\Bob\\"})
        self.assertTrue({"/c/users/ann", "c:/users/ann", "/c/users/bob", "c:/users/bob"} <= forms, forms)
        self.assertIn(compat.posix_form(Path.home()).lower().rstrip("/") or "/", forms)
        self.assertTrue(all(form == form.lower() for form in forms))

    def test_home_forms_without_pwd(self):
        with mock.patch.dict(sys.modules, {"pwd": None}):
            self.assertIn("/home/zed", compat.home_forms({"HOME": "/home/zed"}))


class GuardOnWindowsPaths(unittest.TestCase):
    """The guard runs under this interpreter, so the win_sim view reaches it too."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, HOME="/c/Users/Ann", USERPROFILE="C:\\Users\\Ann",
                        XDG_STATE_HOME=self.temp.name, PYTHONDONTWRITEBYTECODE="1")

    def decide(self, command):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        result = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload).encode("utf-8"),
                                capture_output=True, env=self.env, cwd=self.temp.name, timeout=10)
        self.assertIn(result.returncode, (0, 2), result.stderr)
        return result.returncode

    def test_both_spellings_of_the_home_are_protected(self):
        for command in ("rm -rf /c/Users/Ann", "rm -rf 'C:\\Users\\Ann'", "rm -rf C:/Users/Ann/",
                        "rm -rf /C/users/ann", "rm -rf 'C:\\Users'", "rm -rf ~",
                        "rm -rf 'C:\\Users\\Ann\\.ssh'", "rm -rf /c/Users/Ann/.claude"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), 2)

    def test_folders_inside_the_home_stay_allowed(self):
        for command in ("rm -rf /c/Users/Ann/project/build", "rm -rf 'C:\\Users\\Ann\\project\\build'",
                        "rm -rf /c/Users/Ann-other"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), 0)

    def test_payload_is_read_as_utf8_bytes(self):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo \u754c && rm -rf /c/Users/Ann"}},
                             ensure_ascii=False).encode("utf-8")
        result = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True,
                                env=self.env, cwd=self.temp.name, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)


class ShellHelpersFindPython(unittest.TestCase):
    """Through pyrun.sh: python3, then python, then py -3; a Store stub (exit 49, no output) is skipped."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.script("python3", "exit 49")
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin), XDG_STATE_HOME=str(self.home / "state"))
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def script(self, name, body):
        path = self.bin / name
        path.write_bytes(("#!/bin/sh\n" + body + "\n").encode())
        path.chmod(0o755)

    def real_python(self, name="python"):
        self.script(name, "exec '" + Path(sys.executable).as_posix() + "' \"$@\"")

    def run_hook(self, script, payload, cwd=None):
        # A forward-slash path, as hook commands spell it; bash on Windows loses backslashes.
        result = subprocess.run([BASH, Path(script).as_posix()], input=json.dumps(payload).encode(),
                                capture_output=True, env=self.env, cwd=cwd or self.home, timeout=30)
        return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")

    def test_store_stub_alone_warns_once_never_silently(self):
        self.env["HARNESS_AUTO_FORMAT"] = "1"
        for hook in ("verify-patch-landed-auto.sh", "stop-check-verify-patch.sh", "auto-format.sh"):
            with self.subTest(hook=hook):
                code, out, err = self.run_hook(HELPERS / hook, {"tool_name": "Bash"})
                self.assertEqual((code, out), (1, ""))  # 1, never 2: a warning must not block or loop Stop
                self.assertEqual(len(err.splitlines()), 1, err)
                self.assertIn("no Python 3.9 or newer", err)

    def test_python_after_a_store_stub_runs_the_helper(self):
        self.real_python()
        command = "python3 - <<'PY'\nimport ast\nast.parse(source)\nPY"
        code, out, err = self.run_hook(HELPERS / "verify-patch-landed-auto.sh",
                                       {"tool_name": "Bash", "tool_input": {"command": command}})
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Python rewrite", json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_py_launcher_is_the_last_choice(self):
        log = (self.home / "py.log").as_posix()
        self.script("py", "[ \"$1\" = -3 ] || exit 3; shift; echo used >> '" + log + "'; exec '"
                    + Path(sys.executable).as_posix() + "' \"$@\"")
        command = "python3 - <<'PY'\nimport ast\nast.parse(source)\nPY"
        code, out, err = self.run_hook(HELPERS / "verify-patch-landed-auto.sh",
                                       {"tool_name": "Bash", "tool_input": {"command": command}})
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Python rewrite", out)
        self.assertTrue((self.home / "py.log").is_file())

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_project_context_runs_from_its_python_file(self):
        self.real_python()
        self.env["PATH"] = str(self.bin) + os.pathsep + str(Path(shutil.which("git")).parent)
        project = self.home / "project"
        project.mkdir()
        subprocess.run([shutil.which("git"), "init", "-q", str(project)], check=True, timeout=30,
                       stdin=subprocess.DEVNULL, capture_output=True)
        (project / "CLAUDE.md").write_bytes(b"rules\n")
        code, out, err = self.run_hook(ROOT / "hooks/context/inject-project-context.sh",
                                       {"session_id": "s", "cwd": str(project)}, cwd=project)
        self.assertEqual((code, err), (0, ""))
        self.assertIn('Nearest project instructions: "CLAUDE.md"', out)


class ContextMarkerWithoutModes(unittest.TestCase):
    """The context hook sets its marker folder private through compat.set_private, so a platform
    without POSIX modes (simulated: compat.WINDOWS and an os.chmod that raises) still records it."""

    CHILD = ("import os, runpy, sys\n"
             "sys.path.insert(0, sys.argv[1])\n"
             "import compat\n"
             "compat.WINDOWS = True\n"
             "def refuse(*args, **kwargs):\n"
             "    raise PermissionError('no POSIX modes here')\n"
             "os.chmod = refuse\n"
             "sys.argv = sys.argv[2:]\n"
             "runpy.run_path(sys.argv[0], run_name='__main__')\n")

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_second_request_is_silent_when_chmod_is_unavailable(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            project = base / "project"
            project.mkdir()
            subprocess.run([shutil.which("git"), "init", "-q", str(project)], check=True, timeout=30,
                           stdin=subprocess.DEVNULL, capture_output=True)
            (project / "CLAUDE.md").write_bytes(b"rules\n")
            env = dict(os.environ, HOME=str(base), XDG_STATE_HOME=str(base / "state"), PYTHONDONTWRITEBYTECODE="1")
            env.pop("CLAUDE_PROJECT_DIR", None)
            payload = json.dumps({"session_id": "s", "cwd": str(project)}).encode()
            runs = [subprocess.run([sys.executable, "-B", "-c", self.CHILD, str(ROOT / "hooks/checkpoint"),
                                    str(ROOT / "hooks/context/inject_project_context.py")],
                                   input=payload, capture_output=True, cwd=project, env=env, timeout=30)
                    for _ in range(2)]
            seen = sorted((base / "state/claude-harness/context").glob("*/*.seen"))
        first, second = runs
        self.assertEqual((first.returncode, first.stderr), (0, b""))
        self.assertIn(b'Nearest project instructions: "CLAUDE.md"', first.stdout)
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual((second.returncode, second.stderr, second.stdout), (0, b"", b""))


class OutputIsUtf8(unittest.TestCase):
    def test_restore_survives_a_windows_code_page(self):
        with TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            project = base / "project"
            project.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(project)], stdin=subprocess.DEVNULL,
                           capture_output=True, check=True, timeout=5)
            (project / "STATE.md").write_bytes(("## Next move\n" + "界" * 40 + "\n").encode("utf-8"))
            # A Windows pipe uses the ANSI code page (cp1252), which has no CJK characters.
            env = dict(os.environ, HOME=str(base), XDG_STATE_HOME=str(base / "state"), PYTHONIOENCODING="cp1252")
            env.pop("PYTHONUTF8", None)
            payload = {"session_id": "s", "cwd": str(project), "source": "compact"}
            result = subprocess.run([sys.executable, str(ROOT / "hooks/checkpoint/dispatch.py"), "restore",
                                     "--host=claude"], input=json.dumps(payload).encode(), capture_output=True,
                                    cwd=project, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("界" * 40, text)


class StaticBan(unittest.TestCase):
    def test_static_scan_covers_owned_files_with_nested_router_checkout(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            owned = root / "hooks/guard/unsafe.py"
            sibling = root / "router/hooks/router/compat.py"
            owned.parent.mkdir(parents=True)
            sibling.parent.mkdir(parents=True)
            owned.write_text("import pwd\n", encoding="utf-8")
            sibling.write_text("import pwd\n", encoding="utf-8")
            self.assertEqual(list(shipped_python(root)), [owned])
            self.assertEqual(banned_uses(owned.read_text(encoding="utf-8")), ["1: import pwd"])

    def test_no_shipped_python_outside_compat_touches_posix_only_names(self):
        # A file that does not parse fails here rather than being skipped.
        offenders = [f"{path.relative_to(ROOT)}:{use}" for path in shipped_python()
                     for use in banned_uses(path.read_text(encoding="utf-8"), str(path))]
        self.assertEqual(offenders, [])

    def test_the_ban_catches_each_form(self):
        for source in ("import pwd", "from pwd import getpwuid", "os.geteuid()", "x = os.O_NOFOLLOW",
                       "os.mkfifo(p)", "os.fchmod(f, 0)", "getattr(os, 'O_NONBLOCK', 0)", "os.getuid()",
                       "import os, pwd", "import json\nimport pwd as accounts",
                       "from os import geteuid", "from os import path, getuid as uid", "from os import *",
                       "import os as o; o.O_NOFOLLOW", "import os as o\ngetattr(o, 'mkfifo', None)"):
            with self.subTest(source=source):
                self.assertTrue(banned_uses(source), source)

    def test_the_ban_passes_ordinary_code(self):
        for source in ("import os, json", "from os import path", "import os as o; o.path.join('a', 'b')",
                       "# os.geteuid() and import pwd\nx = 1", "note = 'import pwd; os.O_NOFOLLOW'",
                       "getattr(os, 'name', '')", "account.getuid()", "import pwdx"):
            with self.subTest(source=source):
                self.assertEqual(banned_uses(source), [])

    def test_the_ban_reports_the_line(self):
        self.assertEqual(banned_uses("import os as o\n\nflag = o.O_NOFOLLOW\n"), ["3: o.O_NOFOLLOW"])

    def test_shell_helpers_hold_no_inline_python(self):
        for name in ("context/inject-project-context.sh", "helpers/auto-format.sh",
                     "helpers/verify-patch-landed-auto.sh", "helpers/stop-check-verify-patch.sh"):
            text = (ROOT / "hooks" / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotRegex(text, r"python3? +(-c|-)\b|<<'PY'|command -v python3 [^\n]*\|\| exit 0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
