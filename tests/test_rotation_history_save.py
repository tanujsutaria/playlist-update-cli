"""Regression: RotationManager._save_history must use the canonical generation
id returned by rotation_generations.upsert(), not the id it computed.

If an existing generation row for (playlist, index) carries a different id than
the uuid5 _save_history derives, upsert keeps the existing row and returns its
id; using the computed id for generation_tracks would reference a non-existent
generation and raise a FOREIGN KEY error.
"""

from models import PlaylistHistory, Song
from rotation_manager import RotationManager
from song_store import SongStore
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories


def _rm_with_repos(tmp_path, playlist_name, history):
    db = Database(str(tmp_path / "tunr.db"))
    conn = db.connect()
    ensure_schema(conn)
    repos = Repositories(conn)
    rm = RotationManager.__new__(RotationManager)
    rm.playlist_name = playlist_name
    rm.repos = repos
    rm.db = SongStore(repos)
    rm.history = history
    return rm, repos


def test_save_history_uses_existing_generation_id(tmp_path):
    history = PlaylistHistory(
        playlist_id=None, name="Mix", generations=[["a|||s"]], current_generation=0
    )
    rm, repos = _rm_with_repos(tmp_path, "Mix", history)

    # Seed the track (so the generation_tracks FK on track_id is satisfiable)
    rm.db.add_song(Song(id="a|||s", name="s", artist="a"))
    # Seed a generation row for ("mix", 0) under a NON-uuid5 id, mimicking an
    # id scheme that differs from what _save_history computes.
    repos.playlists.upsert(playlist_id="mix", name="Mix", current_generation=0)
    repos.rotation_generations.upsert("legacy-gen-0", "mix", 0)
    repos.conn.commit()

    # Pre-fix this raised sqlite3.IntegrityError (FOREIGN KEY); post-fix it is clean.
    rm._save_history()

    # Tracks must hang off the EXISTING canonical generation id, not a new uuid5.
    rows = repos.generation_tracks.list_by_generation("legacy-gen-0")
    assert [r["track_id"] for r in rows] == ["a|||s"]
    # No duplicate generation row was created for (mix, 0).
    gens = repos.rotation_generations.list_by_playlist("mix")
    assert len(gens) == 1
    assert gens[0]["generation_id"] == "legacy-gen-0"


def test_save_history_roundtrips_via_load(tmp_path):
    """A fresh playlist (no pre-existing rows) saves and reloads identically."""
    history = PlaylistHistory(
        playlist_id=None,
        name="Daily",
        generations=[["a|||one", "b|||two"], ["a|||one"]],
        current_generation=1,
    )
    rm, repos = _rm_with_repos(tmp_path, "Daily", history)
    for sid, (artist, name) in {
        "a|||one": ("a", "one"),
        "b|||two": ("b", "two"),
    }.items():
        rm.db.add_song(Song(id=sid, name=name, artist=artist))

    rm._save_history()

    reloaded = rm._load_history()
    assert reloaded is not None
    assert reloaded.name == "Daily"
    assert reloaded.current_generation == 1
    assert reloaded.generations == [["a|||one", "b|||two"], ["a|||one"]]
