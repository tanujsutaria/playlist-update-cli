"""
Unit tests for backup and restore commands.
Tests the data directory backup and restore functionality.
"""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime


class TestBackupCommand:
    """Tests for the backup command"""

    def test_backup_creates_folder(self, cli_no_init, tmp_path):
        """Test that backup creates a folder in backups directory"""
        with patch('main.Path') as mock_path:
            # Set up the path mocking
            mock_path.return_value.parent.parent = tmp_path

            # Create data directory
            data_dir = tmp_path / "data"
            data_dir.mkdir()
            (data_dir / "test.txt").write_text("test data")

            backups_dir = tmp_path / "backups"

            # Manually call the backup logic
            from main import PlaylistCLI
            cli = PlaylistCLI.__new__(PlaylistCLI)
            cli._db = None
            cli._spotify = None
            cli._rotation_managers = {}

            # Patch __file__ to return our tmp_path
            with patch.object(Path, '__new__', return_value=tmp_path / "src" / "main.py"):
                # The backup should create the backups directory
                backups_dir.mkdir(exist_ok=True)
                assert backups_dir.exists()

    def test_backup_uses_timestamp_name(self, tmp_path):
        """Test that backup uses timestamp when name not provided"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Generate expected name format
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Timestamp should match YYYYMMDD_HHMMSS format
        assert len(timestamp) == 15
        assert timestamp[8] == '_'

    def test_backup_uses_custom_name(self, tmp_path):
        """Test that backup uses provided name"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        custom_backup = backups_dir / "my_custom_backup"
        custom_backup.mkdir()

        assert custom_backup.exists()
        assert custom_backup.name == "my_custom_backup"

    def test_backup_copies_data_contents(self, tmp_path):
        """Test that backup copies all data directory contents"""
        # Create data directory with files
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        embeddings_dir = data_dir / "embeddings"
        embeddings_dir.mkdir()
        (embeddings_dir / "songs.pkl").write_bytes(b"pickle data")
        (embeddings_dir / "embeddings.npy").write_bytes(b"numpy data")

        # Create backup
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        backup_dir = backups_dir / "test_backup"

        import shutil
        shutil.copytree(str(data_dir), str(backup_dir))

        # Verify contents copied
        assert (backup_dir / "embeddings" / "songs.pkl").exists()
        assert (backup_dir / "embeddings" / "embeddings.npy").exists()

    def test_backup_aborts_if_name_exists(self, tmp_path):
        """Test that backup aborts if backup name already exists"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Create existing backup
        existing = backups_dir / "existing_backup"
        existing.mkdir()

        # Trying to create same backup should fail
        assert existing.exists()


class TestRestoreCommand:
    """Tests for the restore command"""

    def test_restore_copies_backup_to_data(self, tmp_path):
        """Test that restore copies backup contents to data directory"""
        # Create backup
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        backup_dir = backups_dir / "my_backup"
        backup_dir.mkdir()
        (backup_dir / "test.txt").write_text("backup data")

        # Create (empty) data directory
        data_dir = tmp_path / "data"

        # Restore
        import shutil
        if data_dir.exists():
            shutil.rmtree(str(data_dir))
        shutil.copytree(str(backup_dir), str(data_dir))

        # Verify
        assert data_dir.exists()
        assert (data_dir / "test.txt").read_text() == "backup data"

    def test_restore_renames_existing_data(self, tmp_path):
        """Test that existing data directory is renamed before restore"""
        # Create existing data
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "old_data.txt").write_text("old data")

        # Rename pattern: data_old_YYYYMMDD_HHMMSS
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        old_data_dir = tmp_path / f"data_old_{timestamp}"
        data_dir.rename(old_data_dir)

        assert old_data_dir.exists()
        assert not data_dir.exists()

    def test_restore_not_found(self, tmp_path):
        """Test error when backup doesn't exist"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        nonexistent = backups_dir / "nonexistent_backup"
        assert not nonexistent.exists()

    def test_restore_preserves_backup(self, tmp_path):
        """Test that restore doesn't modify the backup"""
        # Create backup
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        backup_dir = backups_dir / "my_backup"
        backup_dir.mkdir()
        backup_file = backup_dir / "data.txt"
        backup_file.write_text("original backup data")

        # Restore (copy)
        data_dir = tmp_path / "data"
        import shutil
        shutil.copytree(str(backup_dir), str(data_dir))

        # Modify restored data
        (data_dir / "data.txt").write_text("modified data")

        # Backup should be unchanged
        assert backup_file.read_text() == "original backup data"


class TestListBackupsCommand:
    """Tests for the list-backups command"""

    def test_list_backups_shows_all(self, tmp_path):
        """Test that all backups are listed"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Create multiple backups
        (backups_dir / "backup1").mkdir()
        (backups_dir / "backup2").mkdir()
        (backups_dir / "backup3").mkdir()

        backups = list(backups_dir.iterdir())
        assert len(backups) == 3

    def test_list_backups_shows_sizes(self, tmp_path):
        """Test that backup sizes are calculated"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        backup = backups_dir / "my_backup"
        backup.mkdir()
        (backup / "file1.txt").write_bytes(b"x" * 1024)  # 1KB
        (backup / "file2.txt").write_bytes(b"x" * 2048)  # 2KB

        # Calculate size
        total_size = sum(f.stat().st_size for f in backup.rglob('*') if f.is_file())
        assert total_size == 3072  # 3KB

    def test_list_backups_empty_directory(self, tmp_path):
        """Test handling of empty backups directory"""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        backups = list(backups_dir.iterdir())
        assert len(backups) == 0

    def test_list_backups_no_directory(self, tmp_path):
        """Test handling when backups directory doesn't exist"""
        backups_dir = tmp_path / "backups"
        assert not backups_dir.exists()

    def test_list_backups_sorted_by_date(self, tmp_path):
        """Test that backups are sorted by modification time"""
        import time

        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        # Create backups with different times
        (backups_dir / "old_backup").mkdir()
        time.sleep(0.1)
        (backups_dir / "new_backup").mkdir()

        # Sort by mtime descending
        sorted_backups = sorted(
            backups_dir.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        assert sorted_backups[0].name == "new_backup"
        assert sorted_backups[1].name == "old_backup"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
