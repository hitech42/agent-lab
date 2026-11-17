"""Guard against drift from the OOP conventions in CLAUDE.md.

Every module under ``src/agent_lab`` is parsed with ``ast``. Two things fail the build:

1. a module-level function outside ``ALLOWED_FUNCTIONS`` (small helpers belong to the class
   that owns their data);
2. a module-level name bound to a mutable ``list``/``dict``/``set`` literal, comprehension or
   constructor call, other than ``__all__`` (no module-level mutable state; use
   ``MappingProxyType``, ``frozenset``, a tuple or a class attribute).
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "agent_lab"

# Relative path -> allowed function names; None means any function (pure-function modules).
ALLOWED_FUNCTIONS: dict[str, frozenset[str] | None] = {
    "report/stats.py": None,
    "runner/cli.py": frozenset({"main"}),
}
_MUTABLE_CALLS = {"list", "dict", "set"}
_MUTABLE_NODES = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)


def _module_level(statements: list[ast.stmt]) -> list[ast.stmt]:
    """Statements that run at import time: also those inside module-level if/try/with/for."""
    found: list[ast.stmt] = []
    for node in statements:
        found.append(node)
        for name in ("body", "orelse", "finalbody"):
            found += _module_level(getattr(node, name, []) if not _is_scope(node) else [])
        for handler in getattr(node, "handlers", []):
            found += _module_level(handler.body)
    return found


def _is_scope(node: ast.stmt) -> bool:
    return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)


def _is_mutable(value: ast.expr | None) -> bool:
    if isinstance(value, _MUTABLE_NODES):
        return True
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _MUTABLE_CALLS
    )


def violations(source: str, rel: str) -> list[str]:
    """Convention violations of one module's source; ``rel`` is its path under src/agent_lab."""
    allowed = ALLOWED_FUNCTIONS.get(rel, frozenset())
    found: list[str] = []
    for node in _module_level(ast.parse(source).body):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if allowed is None or node.name in allowed:
                continue
            found.append(f"{rel}:{node.lineno}: module-level function {node.name}()")
        elif isinstance(node, ast.Assign | ast.AnnAssign) and _is_mutable(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name) and t.id != "__all__"]
            found += [f"{rel}:{node.lineno}: mutable module-level name {n}" for n in names]
    return found


def tree_violations(root: Path) -> tuple[int, list[str]]:
    """(modules scanned, violations) for every .py file under ``root``."""
    modules = sorted(root.rglob("*.py"))
    found: list[str] = []
    for path in modules:
        found += violations(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())
    return len(modules), found


def test_the_source_tree_follows_the_conventions() -> None:
    scanned, found = tree_violations(SRC)
    assert scanned > 40, "the scan found suspiciously few modules"
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "def helper(x):\n    return x\n",
        "async def helper():\n    pass\n",
        "import os\nif os:\n    def helper():\n        pass\n",
        "X = []\n",
        "X: dict[str, int] = {}\n",
        "X = dict()\n",
        "X = list(range(3))\n",
        "X = {1, 2}\n",
        "X = [i for i in range(3)]\n",
        "A = B = {}\n",
    ],
)
def test_planted_violations_are_caught(source: str) -> None:
    assert violations(source, "runner/plan.py")


@pytest.mark.parametrize(
    "source",
    [
        "__all__ = ['a', 'b']\n",
        "from types import MappingProxyType\nX = MappingProxyType({'a': 1})\n",
        "X = ('a', 'b')\nY = frozenset({'a'})\nZ = 'text'\nW = 3\n",
        "class A:\n    TABLE = {'a': 1}\n\n    def method(self):\n        return [1]\n",
        "def_ = 1\n",
    ],
)
def test_immutable_tables_classes_and_all_are_accepted(source: str) -> None:
    assert violations(source, "runner/plan.py") == []


def test_the_allowlist_admits_only_what_it_names() -> None:
    assert violations("def anything():\n    pass\n", "report/stats.py") == []
    assert violations("def main():\n    pass\n", "runner/cli.py") == []
    assert violations("def other():\n    pass\n", "runner/cli.py")
    assert violations("X = []\n", "report/stats.py")  # the allowlist is for functions only


def test_a_planted_module_in_a_tree_is_found(tmp_path: Path) -> None:
    (tmp_path / "runner").mkdir()
    (tmp_path / "runner" / "ok.py").write_text("class A:\n    pass\n")
    (tmp_path / "runner" / "bad.py").write_text("def helper():\n    pass\nCACHE = {}\n")
    scanned, found = tree_violations(tmp_path)
    assert scanned == 2
    assert [v.split(": ", 1)[1] for v in found] == [
        "module-level function helper()",
        "mutable module-level name CACHE",
    ]
    assert all(v.startswith("runner/bad.py:") for v in found)
