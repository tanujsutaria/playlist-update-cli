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
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # py39/py310: tomli ships with pytest
    import tomli as tomllib  # type: ignore[no-redef]

SRC = Path(__file__).resolve().parent.parent / "src"
PYPROJECT = SRC.parent / "pyproject.toml"

IGNORED_DIRS = {"__pycache__"}


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _declared_py_modules() -> set:
    return set(_pyproject()["tool"]["setuptools"]["py-modules"])


def _declared_package_patterns() -> list:
    return list(_pyproject()["tool"]["setuptools"]["packages"]["find"]["include"])


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


def _contains_python(directory: Path) -> bool:
    return any(directory.rglob("*.py"))


def test_every_package_dir_is_packaged():
    """Every directory under src/ that holds Python code must be a real,
    declared package.

    Two failure modes are caught: a directory matching no packages.find
    pattern, and a directory with no __init__.py at all — setuptools'
    packages.find skips namespace dirs silently, so such a directory would
    pass CI (pythonpath imports it fine) yet be absent from the wheel.

    Directories WITHOUT any .py files are exempt: local checkouts accumulate
    non-code state next to the sources (a stray src/data/ from an old run,
    *.egg-info from an editable install) that no wheel should ship anyway.
    """
    patterns = _declared_package_patterns()
    dirs = sorted(
        p
        for p in SRC.iterdir()
        if p.is_dir() and p.name not in IGNORED_DIRS and _contains_python(p)
    )
    assert dirs, "expected at least storage/, nextgen/, commands/ under src/"

    missing_init = [d.name for d in dirs if not (d / "__init__.py").exists()]
    assert not missing_init, (
        f"src/ directories with .py files but no __init__.py: {missing_init} — "
        "packages.find skips namespace dirs, so these would ship in no wheel. "
        "Add __init__.py."
    )

    undeclared = [d.name for d in dirs if not any(fnmatch.fnmatch(d.name, pat) for pat in patterns)]
    assert not undeclared, (
        f"src/ packages not covered by packages.find include {patterns}: {undeclared}"
    )


def test_every_declared_module_imports():
    """Catches declaration typos: each declared module must actually import."""
    for name in sorted(_declared_py_modules()):
        importlib.import_module(name)
    for pkg in ("storage", "nextgen", "commands"):
        importlib.import_module(pkg)
