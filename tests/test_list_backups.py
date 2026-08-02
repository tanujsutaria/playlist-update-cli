"""Tests for the list-backups command.

Hermetic via the ``config.project_root`` seam; output asserted through the
``ui.set_output_sink`` capture (the choke point every ui helper funnels
through), so the assertions survive the command body moving out of main.py.
"""

from io import StringIO

import pytest
from rich.console import Console

import config
import ui
from main import PlaylistCLI


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
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


class TestListBackups:
    """Tests for the list_backups command"""

    def test_no_backups_directory(self, cli, root, sink):
        cli.list_backups()
        assert "No backups directory" in _rendered(sink)

    def test_empty_backups_directory(self, cli, root, sink):
        (root / "backups").mkdir()
        cli.list_backups()
        out = _rendered(sink)
        assert "No backups found" in out or "No backup folders" in out

    def test_list_backups_with_data(self, cli, root, sink):
        backups_dir = root / "backups"
        backups_dir.mkdir()

        backup1 = backups_dir / "20240101_120000"
        backup1.mkdir()
        (backup1 / "test_file.txt").write_text("test content 1")

        backup2 = backups_dir / "my_backup"
        backup2.mkdir()
        (backup2 / "test_file.txt").write_text("test content 2 with more data")
        (backup2 / "another_file.pkl").write_bytes(b"x" * 1000)

        cli.list_backups()

        out = _rendered(sink)
        assert "20240101_120000" in out
        assert "my_backup" in out
        assert "Total backups: 2" in out

    def test_list_backups_ignores_files(self, cli, root, sink):
        backups_dir = root / "backups"
        backups_dir.mkdir()

        backup1 = backups_dir / "real_backup"
        backup1.mkdir()
        (backup1 / "data.txt").write_text("backup data")

        # A file (not a directory) in backups must not be listed as a backup.
        (backups_dir / "not_a_backup.txt").write_text("this is just a file")

        cli.list_backups()

        out = _rendered(sink)
        assert "real_backup" in out
        assert "not_a_backup.txt" not in out
        assert "Total backups: 1" in out


class TestListBackupsIntegration:
    """Integration tests for list_backups that test the actual method"""

    def test_list_backups_output_format(self, cli, root, sink):
        backups_dir = root / "backups"
        backups_dir.mkdir()

        backup = backups_dir / "test_backup"
        backup.mkdir()
        (backup / "songs.pkl").write_bytes(b"x" * 2048)  # 2KB file

        cli.list_backups()

        out = _rendered(sink)
        assert "Available Backups" in out
        assert "Backup Name" in out
        assert "Size" in out
        assert "Created" in out
        assert "test_backup" in out
        assert "MB" in out
        assert "Total backups: 1" in out
        assert "restore" in out.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
