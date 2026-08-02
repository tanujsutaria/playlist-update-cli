"""Packaging rails: every module in src/ must be declared in pyproject.

pytest runs with ``pythonpath = ["src"]``, so the suite passes even when a
module is missing from ``[tool.setuptools]`` — only the *installed* ``tunr``
breaks, at user runtime. These tests make the declaration gap a CI failure.
(Found on introduction: completions, llm_json, results_screen, scopes were
all undeclared, and main.py imports scopes at startup.)
"""

from __future__ import annotations

import fnmatch
import importlib
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
PYPROJECT = SRC.parent / "pyproject.toml"


def _pyproject_text() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def _declared_py_modules() -> set:
    match = re.search(r"py-modules = \[(.*?)\]", _pyproject_text(), re.S)
    assert match, "py-modules list not found in pyproject.toml"
    return set(re.findall(r'"([A-Za-z_0-9]+)"', match.group(1)))


def _declared_package_patterns() -> list:
    match = re.search(
        r"\[tool\.setuptools\.packages\.find\].*?include = \[(.*?)\]",
        _pyproject_text(),
        re.S,
    )
    assert match, "packages.find include list not found in pyproject.toml"
    return re.findall(r'"([A-Za-z_0-9*]+)"', match.group(1))


def test_every_top_level_module_is_declared():
    actual = {p.stem for p in SRC.glob("*.py")}
    declared = _declared_py_modules()
    missing = sorted(actual - declared)
    assert not missing, (
        f"src/ modules missing from [tool.setuptools] py-modules: {missing} — "
        "the installed tunr cannot import them (pytest's pythonpath masks this)."
    )
    stale = sorted(declared - actual)
    assert not stale, f"py-modules declares modules that do not exist in src/: {stale}"


def test_every_package_matches_an_include_pattern():
    patterns = _declared_package_patterns()
    packages = sorted(p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists())
    assert packages, "expected at least storage/, nextgen/, commands/ under src/"
    undeclared = [pkg for pkg in packages if not any(fnmatch.fnmatch(pkg, pat) for pat in patterns)]
    assert not undeclared, (
        f"src/ packages not covered by packages.find include {patterns}: {undeclared}"
    )


def test_every_declared_module_imports():
    """Catches declaration typos: each declared module must actually import."""
    for name in sorted(_declared_py_modules()):
        importlib.import_module(name)
    for pkg in ("storage", "nextgen", "commands"):
        importlib.import_module(pkg)
