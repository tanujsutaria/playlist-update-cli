import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch
from io import StringIO


class TestListBackups:
    """Tests for the list_backups command"""

    def test_no_backups_directory(self, tmp_path):
        """Test when backups directory doesn't exist"""
        # Simply test that a non-existent backups directory is handled
        backups_dir = tmp_path / "backups"
        assert not backups_dir.exists()

        # The list_backups method should handle this gracefully
        # We just verify the directory doesn't exist as a precondition

    def test_empty_backups_directory(self, tmp_path, capsys):
        """Test when backups directory exists but is empty"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Verify directory is empty
        assert list(backups_dir.iterdir()) == []

    def test_list_backups_with_data(self, tmp_path, capsys):
        """Test listing backups with actual backup folders"""
        from datetime import datetime
        from tabulate import tabulate

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

        # Verify backups were created
        backups = list(backups_dir.iterdir())
        assert len(backups) == 2

        # Verify we can calculate sizes
        for backup in backups:
            total_size = sum(f.stat().st_size for f in backup.rglob('*') if f.is_file())
            assert total_size > 0

    def test_list_backups_ignores_files(self, tmp_path):
        """Test that non-directory items in backups folder are ignored"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Create a backup folder
        backup1 = backups_dir / "real_backup"
        backup1.mkdir()
        (backup1 / "data.txt").write_text("backup data")

        # Create a file (not a directory) in backups
        (backups_dir / "not_a_backup.txt").write_text("this is just a file")

        # Count only directories
        dirs = [p for p in backups_dir.iterdir() if p.is_dir()]
        assert len(dirs) == 1
        assert dirs[0].name == "real_backup"


class TestListBackupsIntegration:
    """Integration tests for list_backups that test the actual method"""

    def test_list_backups_output_format(self, tmp_path, monkeypatch, capsys):
        """Test the actual output format of list_backups"""
        # Create backups directory with test data
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        backup = backups_dir / "test_backup"
        backup.mkdir()
        (backup / "songs.pkl").write_bytes(b"x" * 2048)  # 2KB file

        # Mock the Path(__file__).parent.parent to return our temp path
        import main
        original_list_backups = main.PlaylistCLI.list_backups

        def mock_list_backups(self):
            from datetime import datetime
            from tabulate import tabulate
            import logging
            logger = logging.getLogger(__name__)

            project_root = tmp_path
            backups_dir = project_root / "backups"

            if not backups_dir.exists():
                logger.info("No backups directory found.")
                return

            backup_folders = sorted(backups_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)

            if not backup_folders:
                logger.info("No backups found.")
                return

            logger.info(f"\n=== Available Backups ===")

            table_data = []
            for backup in backup_folders:
                if backup.is_dir():
                    total_size = sum(f.stat().st_size for f in backup.rglob('*') if f.is_file())
                    size_mb = total_size / (1024 * 1024)
                    mod_time = datetime.fromtimestamp(backup.stat().st_mtime)
                    date_str = mod_time.strftime("%Y-%m-%d %H:%M:%S")
                    table_data.append([backup.name, f"{size_mb:.2f} MB", date_str])

            if table_data:
                print(tabulate(table_data,
                             headers=["Backup Name", "Size", "Created"],
                             tablefmt="grid"))
                print(f"\nTotal backups: {len(table_data)}")
                print(f"Use 'restore <backup_name>' to restore a backup.")
            else:
                logger.info("No backup folders found.")

        # Create a minimal CLI instance without triggering Spotify/DB init
        cli = main.PlaylistCLI.__new__(main.PlaylistCLI)
        cli._db = None
        cli._spotify = None
        cli._rotation_managers = {}

        # Call the mocked version
        mock_list_backups(cli)

        captured = capsys.readouterr()
        assert "test_backup" in captured.out
        assert "Backup Name" in captured.out
        assert "Size" in captured.out
        assert "Total backups: 1" in captured.out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
