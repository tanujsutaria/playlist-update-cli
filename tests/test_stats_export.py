import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from main import PlaylistCLI  # noqa: E402


class FakeDB:
    def get_stats(self):
        return {
            "total_songs": 12,
            "embedding_dimensions": 384,
            "storage_size_mb": 1.23,
        }


def test_export_stats_json(tmp_path):
    cli = PlaylistCLI()
    cli._db = FakeDB()

    output_file = tmp_path / "stats.json"
    cli.export_stats(playlist_name=None, export_format="json", output_file=str(output_file))

    assert output_file.exists()
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["database"]["total_songs"] == 12
    assert payload["playlist"] is None


def test_export_stats_csv(tmp_path):
    cli = PlaylistCLI()
    cli._db = FakeDB()

    output_file = tmp_path / "stats.csv"
    cli.export_stats(playlist_name=None, export_format="csv", output_file=str(output_file))

    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "database,total_songs,12" in content
