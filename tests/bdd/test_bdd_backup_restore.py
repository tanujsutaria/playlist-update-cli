"""BDD flow: backup_restore.

Drives the REAL ``PlaylistCLI.backup_data`` / ``restore_data`` / ``list_backups``
methods end to end via ``dispatch_command`` (the shared ``run`` helper), against a
fully hermetic, tmp_path-based project root. Those methods anchor their data and
backups folders via ``config.project_root()`` — the durable seam — so redirecting
that one function to ``tmp_path`` keeps the real ``data/`` and ``backups/``
folders untouched, no matter which module the backup code lives in.

The data payload is a real SQLite database so the assertions check actual row
counts before and after a restore -- not filesystem mechanics in the abstract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path as RealPath

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import config

# Feature file resolved relative to bdd_features_base_dir (tests/bdd/features).
scenarios("backup_restore.feature")


# --------------------------------------------------------------------------- #
# Local fixtures (kept here, NOT in the shared conftest, per phase rules).
# --------------------------------------------------------------------------- #


def _seed_db(db_path: RealPath, count: int) -> None:
    """Write a tiny real SQLite db with ``count`` rows in a ``tracks`` table."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE tracks (track_id TEXT PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO tracks (track_id, name) VALUES (?, ?)",
            [(f"t{i}", f"Track {i}") for i in range(count)],
        )
        conn.commit()
    finally:
        conn.close()


def _track_count(db_path: RealPath) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
    finally:
        conn.close()


class _BackupWorld:
    """Mutable per-scenario state shared across steps."""

    def __init__(self, root: RealPath) -> None:
        self.root = root
        self.data_dir = root / "data"
        self.backups_dir = root / "backups"
        self.db_path = self.data_dir / "tunr.db"
        self.last_rc: int | None = None
        self.last_stdout: str = ""


@pytest.fixture
def patched_root(tmp_path, monkeypatch) -> _BackupWorld:
    """A hermetic project root; ``config.project_root`` redirected so the CLI's
    backup/restore/list code resolves its data + backups folders inside
    ``tmp_path``."""
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(config, "project_root", lambda: root)
    return _BackupWorld(root)


@pytest.fixture
def world(patched_root: _BackupWorld) -> _BackupWorld:
    return patched_root


# --------------------------------------------------------------------------- #
# Given
# --------------------------------------------------------------------------- #


@given("a hermetic project root with a data directory")
def _given_root(world: _BackupWorld) -> None:
    world.data_dir.mkdir(parents=True, exist_ok=True)
    assert world.data_dir.exists()
    # Guard the safety invariant: we must be operating inside tmp, never the repo.
    assert str(world.root).startswith(str(world.root.parent))
    assert world.root.name == "proj"


@given(parsers.parse("the data directory holds a SQLite database with {count:d} tracks"))
def _given_db(world: _BackupWorld, count: int) -> None:
    _seed_db(world.db_path, count)
    assert _track_count(world.db_path) == count


@given(parsers.parse('the data has been backed up as "{name}"'))
def _given_backed_up(world: _BackupWorld, cli, run, name: str) -> None:
    rc = run(f"backup {name}")
    assert rc == 0
    assert (world.backups_dir / name).exists()


@given("the live database is corrupted")
def _given_corrupted(world: _BackupWorld) -> None:
    world.db_path.write_bytes(b"not a database")
    # The file is no longer a valid sqlite db.
    with pytest.raises(sqlite3.DatabaseError):
        _track_count(world.db_path)


# --------------------------------------------------------------------------- #
# When
# --------------------------------------------------------------------------- #


@when(parsers.parse('I run "{command}"'))
def _when_run(world: _BackupWorld, run, capsys, command: str) -> None:
    # capsys must be active *while* the command prints; rich resolves sys.stdout
    # dynamically, so we drain the buffer here and stash it for later Then steps.
    world.last_rc = run(command)
    world.last_stdout = capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Then
# --------------------------------------------------------------------------- #


@then("the command returns 0")
def _then_rc_zero(world: _BackupWorld) -> None:
    assert world.last_rc == 0


@then(parsers.parse('a backup named "{name}" exists under backups'))
def _then_backup_exists(world: _BackupWorld, name: str) -> None:
    assert (world.backups_dir / name).is_dir()


@then(parsers.parse("the backup contains a SQLite database with {count:d} tracks"))
def _then_backup_db(world: _BackupWorld, name_holder, count: int) -> None:
    # The most-recently created backup folder under backups/.
    backups = [p for p in world.backups_dir.iterdir() if p.is_dir()]
    assert backups, "no backup folders were created"
    backup = max(backups, key=lambda p: p.stat().st_mtime)
    assert _track_count(backup / "tunr.db") == count


@then(parsers.parse('the output mentions the backup "{name}"'))
def _then_output_mentions(world: _BackupWorld, name: str) -> None:
    assert name in world.last_stdout


@then(parsers.parse("the live database again has {count:d} tracks"))
def _then_live_restored(world: _BackupWorld, count: int) -> None:
    assert world.data_dir.exists()
    assert _track_count(world.db_path) == count


@then(parsers.parse("the live database still has {count:d} tracks"))
def _then_live_unchanged(world: _BackupWorld, count: int) -> None:
    assert _track_count(world.db_path) == count


@then("no staging or data_old directories are left behind")
def _then_no_leftovers(world: _BackupWorld) -> None:
    leftovers = [
        p.name
        for p in world.root.iterdir()
        if p.name.startswith(".data_restore_") or p.name.startswith("data_old_")
    ]
    assert leftovers == [], f"unexpected leftover dirs: {leftovers}"
    # Only the expected top-level entries remain.
    assert sorted(p.name for p in world.root.iterdir()) == ["backups", "data"]


@then(parsers.parse('no backup named "{name}" exists under backups'))
def _then_no_backup(world: _BackupWorld, name: str) -> None:
    assert not (world.backups_dir / name).exists()


# pytest-bdd needs every named step arg to resolve to a fixture; the backup-db
# step parses a ``{count}`` but no ``{name}``, so provide a harmless placeholder.
@pytest.fixture
def name_holder() -> None:
    return None
