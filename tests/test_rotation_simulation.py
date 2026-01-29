import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from models import Song, PlaylistHistory  # noqa: E402
from rotation_manager import RotationManager  # noqa: E402


class FakeDB:
    def __init__(self, songs):
        self._songs = songs

    def get_all_songs(self):
        return list(self._songs)

    def find_similar_songs(self, song, k=1, threshold=0.9):
        # Not used in this test path (unused songs fill the count)
        return []


class InMemoryRotationManager(RotationManager):
    def _load_history(self):
        return PlaylistHistory(
            playlist_id=None,
            name=self.playlist_name,
            generations=[],
            current_generation=0,
        )

    def _save_history(self):
        # Avoid touching disk during tests
        return None


def test_simulate_generations_does_not_mutate_history():
    songs = [
        Song(id="a|||one", name="one", artist="a"),
        Song(id="b|||two", name="two", artist="b"),
        Song(id="c|||three", name="three", artist="c"),
        Song(id="d|||four", name="four", artist="d"),
    ]
    rm = InMemoryRotationManager("Test Playlist", db=FakeDB(songs), spotify=object())

    plans = rm.simulate_generations(count=2, fresh_days=30, generations=2)

    assert len(plans) == 2
    assert all(len(gen) == 2 for gen in plans)
    assert rm.history.generations == []
    assert rm.history.current_generation == 0
