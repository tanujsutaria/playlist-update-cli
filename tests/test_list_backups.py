import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import main


class TestListBackups:
    """Tests for the list_backups command"""

    def test_no_backups_directory(self, tmp_path, monkeypatch):
        """Test when backups directory doesn't exist"""
        calls = []
        monkeypatch.setattr(main, "info", lambda msg: calls.append(msg))

        cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
        cli._db = None
        cli._spotify = None
        cli._rotation_managers = {}

        with patch.object(main.Path, "__new__", return_value=tmp_path / "src" / "main.py"):
            # Point project_root to a temp dir without a backups subfolder
            with patch("main.Path") as MockPath:
                mock_file = MagicMock()
                mock_file.parent.parent = tmp_path
                MockPath.__file__ = mock_file
                MockPath.return_value = mock_file
                # Directly call with a patched project_root
                project_root = tmp_path
                backups_dir = project_root / "backups"
                assert not backups_dir.exists()

        # Call the real method after patching Path(__file__)
        with patch("main.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.parent.parent = tmp_path
            MockPath.return_value = mock_path_instance

            cli.list_backups()

        assert any("No backups directory" in c for c in calls)

    def test_empty_backups_directory(self, tmp_path, monkeypatch):
        """Test when backups directory exists but is empty"""
        calls = []
        monkeypatch.setattr(main, "info", lambda msg: calls.append(msg))

        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
        cli._db = None
        cli._spotify = None
        cli._rotation_managers = {}

        with patch("main.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.parent.parent = tmp_path
            MockPath.return_value = mock_path_instance

            cli.list_backups()

        assert any("No backups found" in c or "No backup folders" in c for c in calls)

    def test_list_backups_with_data(self, tmp_path, monkeypatch):
        """Test listing backups with actual backup folders"""
        info_calls = []
        table_calls = []
        monkeypatch.setattr(main, "info", lambda msg: info_calls.append(msg))
        monkeypatch.setattr(main, "section", lambda *a, **kw: None)
        monkeypatch.setattr(main, "table", lambda headers, rows: table_calls.append((headers, rows)))

        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Create mock backup folders
        backup1 = backups_dir / "20240101_120000"
        backup1.mkdir()
        (backup1 / "test_file.txt").write_text("test content 1")

        backup2 = backups_dir / "my_backup"
        backup2.mkdir()
        (backup2 / "test_file.txt").write_text("test content 2 with more data")
        (backup2 / "another_file.pkl").write_bytes(b"x" * 1000)

        cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
        cli._db = None
        cli._spotify = None
        cli._rotation_managers = {}

        with patch("main.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.parent.parent = tmp_path
            MockPath.return_value = mock_path_instance

            cli.list_backups()

        # Verify table was called with backup data
        assert len(table_calls) == 1
        headers, rows = table_calls[0]
        assert "Backup Name" in headers
        assert "Size" in headers
        assert len(rows) == 2

        # Verify info messages
        assert any("Total backups: 2" in c for c in info_calls)

    def test_list_backups_ignores_files(self, tmp_path, monkeypatch):
        """Test that non-directory items in backups folder are ignored"""
        info_calls = []
        table_calls = []
        monkeypatch.setattr(main, "info", lambda msg: info_calls.append(msg))
        monkeypatch.setattr(main, "section", lambda *a, **kw: None)
        monkeypatch.setattr(main, "table", lambda headers, rows: table_calls.append((headers, rows)))

        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Create a backup folder
        backup1 = backups_dir / "real_backup"
        backup1.mkdir()
        (backup1 / "data.txt").write_text("backup data")

        # Create a file (not a directory) in backups
        (backups_dir / "not_a_backup.txt").write_text("this is just a file")

        cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
        cli._db = None
        cli._spotify = None
        cli._rotation_managers = {}

        with patch("main.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.parent.parent = tmp_path
            MockPath.return_value = mock_path_instance

            cli.list_backups()

        # Only 1 directory backup should be listed
        assert len(table_calls) == 1
        _, rows = table_calls[0]
        assert len(rows) == 1
        assert rows[0][0] == "real_backup"


class TestListBackupsIntegration:
    """Integration tests for list_backups that test the actual method"""

    def test_list_backups_output_format(self, tmp_path, monkeypatch):
        """Test the actual output format of list_backups"""
        info_calls = []
        table_calls = []
        section_calls = []
        monkeypatch.setattr(main, "info", lambda msg: info_calls.append(msg))
        monkeypatch.setattr(main, "section", lambda *a, **kw: section_calls.append(a))
        monkeypatch.setattr(main, "table", lambda headers, rows: table_calls.append((headers, rows)))

        # Create backups directory with test data
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        backup = backups_dir / "test_backup"
        backup.mkdir()
        (backup / "songs.pkl").write_bytes(b"x" * 2048)  # 2KB file

        cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
        cli._db = None
        cli._spotify = None
        cli._rotation_managers = {}

        with patch("main.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.parent.parent = tmp_path
            MockPath.return_value = mock_path_instance

            cli.list_backups()

        # Verify section header
        assert any("Available Backups" in str(a) for a in section_calls)

        # Verify table output
        assert len(table_calls) == 1
        headers, rows = table_calls[0]
        assert headers == ["Backup Name", "Size", "Created"]
        assert len(rows) == 1
        assert rows[0][0] == "test_backup"
        assert "MB" in rows[0][1]

        # Verify info messages
        assert any("Total backups: 1" in c for c in info_calls)
        assert any("restore" in c.lower() for c in info_calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
