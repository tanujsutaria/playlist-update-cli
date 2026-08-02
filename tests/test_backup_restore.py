"""
Unit tests for backup and restore commands.

Drives the REAL ``PlaylistCLI.backup_data`` / ``restore_data`` / ``list_backups``
against a hermetic tmp_path project root via the ``config.project_root`` seam —
no ``Path`` mocking, no re-implementing the copy logic in the test body.
"""

import re
from io import StringIO

import pytest
from rich.console import Console

import config
import ui
from main import PlaylistCLI


@pytest.fixture
def root(tmp_path, monkeypatch):
    """Hermetic project root with a populated data/ directory."""
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "tunr.db").write_bytes(b"sqlite-bytes")
    embeddings_dir = data_dir / "embeddings"
    embeddings_dir.mkdir()
    (embeddings_dir / "songs.pkl").write_bytes(b"pickle data")
    return tmp_path


@pytest.fixture
def cli(root):
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._db = None
    cli._spotify = None
    cli._rotation_managers = {}
    return cli


@pytest.fixture
def sink():
    """Capture everything the ui helpers emit."""
    captured = []
    ui.set_output_sink(captured.append)
    yield captured
    ui.set_output_sink(None)


def _rendered(captured, width: int = 120) -> str:
    buf = StringIO()
    console = Console(file=buf, width=width)
    for renderable in captured:
        console.print(renderable)
    return buf.getvalue()


class TestBackupCommand:
    """Tests for the backup command"""

    def test_backup_creates_folder_and_copies_contents(self, cli, root):
        cli.backup_data("my_backup")
        backup = root / "backups" / "my_backup"
        assert backup.is_dir()
        assert (backup / "tunr.db").read_bytes() == b"sqlite-bytes"
        assert (backup / "embeddings" / "songs.pkl").read_bytes() == b"pickle data"

    def test_backup_uses_timestamp_name(self, cli, root):
        cli.backup_data()
        (created,) = list((root / "backups").iterdir())
        assert re.fullmatch(r"\d{8}_\d{6}", created.name)

    def test_backup_aborts_if_name_exists(self, cli, root):
        existing = root / "backups" / "taken"
        existing.mkdir(parents=True)
        cli.backup_data("taken")
        # The pre-existing folder is untouched — nothing was copied into it.
        assert list(existing.iterdir()) == []


def _snap(cli, root):
    """Back up into the sandbox and TRIPWIRE before any restore runs.

    restore_data's success path deletes the moved-aside live data directory;
    if the config.project_root seam were ever stranded, a restore here would
    swap the repo's REAL data/. Asserting the backup landed under tmp fails
    fast before that can happen (mirrors the bdd suite's guard).
    """
    cli.backup_data("snap")
    assert (root / "backups" / "snap").is_dir(), "backup did not land in the tmp sandbox"


class TestRestoreCommand:
    """Tests for the restore command"""

    def test_restore_copies_backup_to_data(self, cli, root):
        _snap(cli, root)
        (root / "data" / "tunr.db").write_bytes(b"mutated")
        assert cli.restore_data("snap") is True
        assert (root / "data" / "tunr.db").read_bytes() == b"sqlite-bytes"

    def test_restore_removes_moved_aside_data(self, cli, root):
        """On success the data_old_<ts> copy must not leak."""
        _snap(cli, root)
        assert cli.restore_data("snap") is True
        leftovers = [p.name for p in root.iterdir() if p.name.startswith("data_old_")]
        assert leftovers == []

    def test_restore_not_found(self, cli, root):
        assert cli.restore_data("nonexistent_backup") is False
        # Live data untouched by a failed restore.
        assert (root / "data" / "tunr.db").read_bytes() == b"sqlite-bytes"

    def test_restore_preserves_backup(self, cli, root):
        _snap(cli, root)
        assert cli.restore_data("snap") is True
        (root / "data" / "tunr.db").write_bytes(b"modified after restore")
        assert (root / "backups" / "snap" / "tunr.db").read_bytes() == b"sqlite-bytes"

    def test_restore_swap_failure_rolls_back_live_data(self, cli, root, monkeypatch):
        """If the staging→data swap raises, the moved-aside live data must be
        rolled back into place and the staging copy cleaned up."""
        from pathlib import Path

        _snap(cli, root)
        (root / "data" / "tunr.db").write_bytes(b"live-current")

        data_dir = root / "data"
        real_rename = Path.rename

        def failing_rename(self, target):
            # Fail ONLY the final staging→data swap; the move-aside of the
            # live dir (data → data_old_<ts>) must still succeed first.
            if Path(target) == data_dir and self.name.startswith(".data_restore_"):
                raise OSError("simulated swap failure")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", failing_rename)

        assert cli.restore_data("snap") is False
        # Live data rolled back intact, nothing leaked.
        assert (data_dir / "tunr.db").read_bytes() == b"live-current"
        leftovers = [
            p.name
            for p in root.iterdir()
            if p.name.startswith("data_old_") or p.name.startswith(".data_restore_")
        ]
        assert leftovers == []


class TestListBackupsCommand:
    """Tests for the list-backups command"""

    def test_list_backups_shows_all(self, cli, root, sink):
        for name in ("backup1", "backup2", "backup3"):
            cli.backup_data(name)
        cli.list_backups()
        out = _rendered(sink)
        for name in ("backup1", "backup2", "backup3"):
            assert name in out
        assert "Total backups: 3" in out

    def test_list_backups_empty_directory(self, cli, root, sink):
        (root / "backups").mkdir()
        cli.list_backups()
        assert "No backups found." in _rendered(sink)

    def test_list_backups_no_directory(self, cli, root, sink):
        cli.list_backups()
        assert "No backups directory found." in _rendered(sink)

    def test_list_backups_sorted_by_date(self, cli, root, sink):
        import os
        import time

        cli.backup_data("old_backup")
        cli.backup_data("new_backup")
        now = time.time()
        os.utime(root / "backups" / "old_backup", (now - 60, now - 60))
        os.utime(root / "backups" / "new_backup", (now, now))
        cli.list_backups()
        out = _rendered(sink)
        assert out.index("new_backup") < out.index("old_backup")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
