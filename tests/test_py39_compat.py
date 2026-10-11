#!/usr/bin/env python3
"""Shipped Python must parse and import on Python 3.9 (stock macOS /usr/bin/python3)."""

import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FLOOR = (3, 9)
# Python embedded in shell scripts: (file, pattern that captures the program text).
EMBEDDED = (
    ("install.sh", r"<<'PY'\n(.*?)\nPY\n"),
)  # The hook helpers' Python lives in .py files, parsed with every shipped module.
# Names that exist only on Python 3.10 or newer.
NEWER_NAMES = {"pairwise", "bit_count", "UnionType", "TypeAlias", "ParamSpec", "TypeGuard",
               "hardlink_to", "file_digest", "anext", "aiter", "StrEnum", "Self"}


def shipped_sources(root=ROOT):
    """Yield (label, source) for every shipped Python module and embedded program."""
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts[0] in ("tests", ".git") or "__pycache__" in relative.parts:
            continue
        yield relative.as_posix(), path.read_text(encoding="utf-8")  # / on Windows too
    for name, pattern in EMBEDDED:
        found = re.findall(pattern, (root / name).read_text(encoding="utf-8"), re.S)
        if not found:
            raise AssertionError(f"{name}: embedded Python not found")
        for index, program in enumerate(found):
            yield f"{name}#{index}", program


def has_future_annotations(tree):
    return any(isinstance(node, ast.ImportFrom) and node.module == "__future__"
               and any(alias.name == "annotations" for alias in node.names)
               for node in tree.body)


def annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                             arguments.vararg, arguments.kwarg):
                if argument is not None and argument.annotation is not None:
                    yield argument.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def union_without_future(source):
    """Lines whose `X | Y` annotation would raise TypeError when evaluated on 3.9."""
    tree = ast.parse(source)
    if has_future_annotations(tree):
        return []
    return sorted({node.lineno for node in annotations(tree)
                   if any(isinstance(part, ast.BinOp) and isinstance(part.op, ast.BitOr)
                          for part in ast.walk(node))})


def newer_runtime_uses(source):
    """Lines that call 3.10+ APIs: zip(strict=), isinstance(x, A | B) and newer names."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            if name == "zip" and any(word.arg == "strict" for word in node.keywords):
                found.add(node.lineno)
            if (name in ("isinstance", "issubclass") and len(node.args) == 2
                    and isinstance(node.args[1], ast.BinOp)
                    and isinstance(node.args[1].op, ast.BitOr)):
                found.add(node.lineno)
        if isinstance(node, ast.Attribute) and node.attr in NEWER_NAMES:
            found.add(node.lineno)
        if isinstance(node, ast.Name) and node.id in ("anext", "aiter"):
            found.add(node.lineno)
        if isinstance(node, ast.ImportFrom) and any(a.name in NEWER_NAMES for a in node.names):
            found.add(node.lineno)
    return sorted(found)


class Python39Compat(unittest.TestCase):
    def test_every_shipped_module_parses_with_the_39_grammar(self):
        sources = list(shipped_sources())
        self.assertTrue(any(label.startswith("hooks/checkpoint/") for label, _ in sources))
        self.assertTrue(any(label.startswith("install.sh#") for label, _ in sources))
        for label, source in sources:
            with self.subTest(module=label):
                ast.parse(source, label, feature_version=FLOOR)

    def test_union_annotations_carry_the_future_import(self):
        flagged = {label: lines for label, source in shipped_sources()
                   if (lines := union_without_future(source))}
        self.assertEqual(flagged, {})

    def test_no_newer_runtime_apis(self):
        flagged = {label: lines for label, source in shipped_sources()
                   if (lines := newer_runtime_uses(source))}
        self.assertEqual(flagged, {})

    def test_checks_flag_what_39_cannot_run(self):
        self.assertEqual(union_without_future("def f(x: int | None) -> str | None:\n    pass\n"),
                         [1])
        self.assertEqual(union_without_future("value: dict | None = None\n"), [1])
        self.assertEqual(union_without_future(
            "from __future__ import annotations\ndef f(x: int | None): pass\n"), [])
        self.assertEqual(union_without_future("flags = 1 | 2\n"), [])
        self.assertEqual(newer_runtime_uses("zip(a, b, strict=True)\n"), [1])
        self.assertEqual(newer_runtime_uses("isinstance(x, int | str)\n"), [1])
        self.assertEqual(newer_runtime_uses("import itertools\nitertools.pairwise(x)\n"), [2])
        self.assertEqual(newer_runtime_uses("isinstance(x, (int, str))\nzip(a, b)\n"), [])
        with self.assertRaises(SyntaxError):
            ast.parse("match x:\n    case 1:\n        pass\n", feature_version=FLOOR)

    def test_installer_and_readme_state_the_39_floor(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"sys\.version_info < \((\d+), (\d+)\)", installer),
                         [("3", "9")])
        self.assertIn("harness: missing requirement: python3 (Python 3.9 or newer)", installer)
        self.assertIn('sys.exit("harness: missing requirement: python3 3.9 or newer")', installer)
        self.assertNotRegex(installer, r"3\.10 or newer")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Use Bash, Python 3.9 or newer, and Git", readme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
