"""Layering rail: domain modules must not import the rendering layer.

Domain/service modules communicate via return values and callbacks; only the
command layer (main, future commands/*) and the shell (interactive_app,
dashboard, results_screen, completions, arg_parse) may import ui. Enforced by
AST so the extraction PRs cannot introduce new domain-to-ui edges.

ALLOWLIST holds the pre-existing violations. It must only ever SHRINK: each
entry is asserted to still violate, so fixing one forces its removal here —
the list cannot rot into a blanket exemption.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# Modules whose job is rendering, or that legitimately drive rendering today.
# commands/ is the command layer — handlers render by design, so the whole
# package is exempt (the rule gates DOMAIN modules only). playlist_resolver
# is the shared miss/warn renderer for every playlist-taking command — its
# whole job is the actionable error panel, so it sits in the command layer.
RENDER_LAYER = {
    "ui",
    "main",
    "commands",
    "playlist_resolver",
    "interactive_app",
    "dashboard",
    "results_screen",
    "completions",
    "arg_parse",
}

# Pre-existing domain-to-ui violations (verified 2026-08-02). Shrink only:
# - rotation_manager: emits a listen-data caption mid-selection
# - spotify_manager: consent-wait messaging via emit_status/info/warning
ALLOWLIST = {"rotation_manager", "spotify_manager"}


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    return ".".join(rel.parts)


def _imports_ui(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "ui" or alias.name.startswith("ui.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module is not None:
                if node.module == "ui" or node.module.startswith("ui."):
                    return True
    return False


def _domain_files():
    for path in sorted(SRC.rglob("*.py")):
        name = _module_name(path)
        top = name.split(".")[0]
        if top in RENDER_LAYER:
            continue
        yield name, path


def test_domain_modules_do_not_import_ui():
    violations = sorted(
        name for name, path in _domain_files() if name not in ALLOWLIST and _imports_ui(path)
    )
    assert not violations, (
        f"domain modules importing ui (new layering violations): {violations} — "
        "return data for the command layer to render instead."
    )


def test_allowlist_only_shrinks():
    """Every allowlisted module must still violate; once fixed, remove it."""
    healed = sorted(
        name for name in ALLOWLIST if not _imports_ui(SRC / (name.replace(".", "/") + ".py"))
    )
    assert not healed, (
        f"allowlisted modules no longer import ui: {healed} — "
        "delete them from ALLOWLIST in tests/test_layering.py."
    )
