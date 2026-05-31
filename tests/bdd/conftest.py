"""Shared pytest-bdd foundation fixtures.

This module wires the REAL internal stack (SQLite ``Database`` -> ``Repositories``
-> ``SongStore`` -> ``PlaylistCLI``) against a throwaway temp database and mocks
ONLY the external edges (Spotify, and -- in later phases -- the LLM/search
providers). Step modules under ``tests/bdd`` reuse these fixtures verbatim.

Fixtures provided:
    seeded_repos -> Seeded: namedtuple(db, repos, store, artists, track_ids,
                    playlist_name, playlist_slug, generation_ids)
    store        -> the SongStore (convenience alias of seeded_repos.store)
    fake_spotify -> FakeSpotify: deterministic SpotifyManager-like double
    cli          -> fully-wired PlaylistCLI built via __new__ (no real init)
    run          -> Run: callable(command_str) -> int return code

The global ``sentence_transformers`` stub installed by ``tests/conftest.py`` is
inherited here (conftest inheritance), so embeddings are fast and offline.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from arg_parse import parse_tokens
from main import PlaylistCLI, dispatch_command
from models import Song, track_id_for
from song_store import SongStore
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

# Bundle of everything a seeded library exposes to steps/assertions.
Seeded = namedtuple(
    "Seeded",
    [
        "db",  # storage.db.Database (has .connect())
        "repos",  # storage.repos.Repositories
        "store",  # song_store.SongStore
        "artists",  # list[str] artist display names seeded
        "track_ids",  # list[str] canonical track ids (artist|||name, lowercased)
        "playlist_name",  # display name of the seeded playlist
        "playlist_slug",  # playlist_id / slug used as the rotation key
        "generation_ids",  # list[str] rotation generation ids (oldest -> newest)
    ],
)

# A small, deterministic seed library: (artist, track) pairs.
_SEED_TRACKS = [
    ("Alpha Artist", "First Light"),
    ("Beta Band", "Second Wind"),
    ("Gamma Group", "Third Rail"),
    ("Delta Duo", "Fourth Wall"),
    ("Epsilon Ensemble", "Fifth Element"),
]

_PLAYLIST_NAME = "Favorites"
_PLAYLIST_SLUG = "favorites"


@pytest.fixture
def seeded_repos(tmp_path) -> Seeded:
    """Build a temp SQLite DB, migrate it, and seed a small deterministic library.

    Seeds: 5 artists + 5 tracks (each with a real embedding row written via the
    offline embedder), one playlist ("Favorites"/"favorites"), and two rotation
    generations referencing the seeded tracks. Everything is committed.

    Returns a ``Seeded`` namedtuple (see module docstring). The underlying
    connection lives for the duration of the test; ``tmp_path`` is removed by
    pytest afterward.
    """
    db = Database(str(tmp_path / "tunr.db"))
    conn = db.connect()
    ensure_schema(conn)
    repos = Repositories(conn)
    store = SongStore(repos)

    now = datetime.now().isoformat()
    embedder = store._embedder()

    artists: List[str] = []
    track_ids: List[str] = []
    for artist, name in _SEED_TRACKS:
        artist_id = artist.lower()
        track_id = track_id_for(artist, name)
        uri = f"spotify:track:{track_id.replace('|', '').replace(' ', '')[:22]}"

        repos.artists.upsert(
            artist_id=artist_id,
            name=artist,
            genres_json="[]",
            updated_at=now,
        )
        repos.tracks.upsert(
            {
                "track_id": track_id,
                "spotify_id": uri,
                "name": name,
                "artist_id": artist_id,
                "album_name": None,
                "release_date": None,
                "duration_ms": None,
                "explicit": None,
                "popularity": None,
                "spotify_url": None,
                "status": "candidate",
                "last_decision": None,
                "decision_reason": None,
                "created_at": now,
                "updated_at": now,
            }
        )

        # Real (offline-stubbed) embedding so similarity-based flows have vectors.
        vector = embedder.embed([f"{name} by {artist}"])[0]
        repos.embeddings.upsert(
            {
                "track_id": track_id,
                "model_name": store.model_name,
                "embedding_blob": encode_vector(vector),
                "embedding_dim": len(vector),
                "embedding_norm": vector_norm(vector),
                "strict_ratio": None,
                "created_at": now,
            }
        )

        artists.append(artist)
        track_ids.append(track_id)

    # One playlist with two rotation generations (oldest -> newest).
    repos.playlists.upsert(
        playlist_id=_PLAYLIST_SLUG,
        name=_PLAYLIST_NAME,
        current_generation=1,
        created_at=now,
        updated_at=now,
    )
    generation_ids: List[str] = []
    gen_layout = [track_ids[:3], track_ids[2:]]  # gen0: first 3, gen1: last 3
    for idx, gen_tracks in enumerate(gen_layout):
        gen_id = f"{_PLAYLIST_SLUG}-gen-{idx}"
        # upsert returns the canonical generation_id (existing or the one passed).
        resolved = repos.rotation_generations.upsert(
            generation_id=gen_id,
            playlist_id=_PLAYLIST_SLUG,
            generation_index=idx,
            created_at=now,
        )
        for pos, track_id in enumerate(gen_tracks):
            repos.generation_tracks.add(resolved, track_id, pos)
        generation_ids.append(resolved)

    repos.conn.commit()

    return Seeded(
        db=db,
        repos=repos,
        store=store,
        artists=artists,
        track_ids=track_ids,
        playlist_name=_PLAYLIST_NAME,
        playlist_slug=_PLAYLIST_SLUG,
        generation_ids=generation_ids,
    )


@pytest.fixture
def store(seeded_repos: Seeded) -> SongStore:
    """Convenience alias: the SongStore from ``seeded_repos``."""
    return seeded_repos.store


class FakeSpotify:
    """Deterministic SpotifyManager-like double.

    Mocks ONLY the Spotify edge. Methods mirror the real ``SpotifyManager``
    surface the CLI flows touch. Track data is plain dicts (the shape
    ``SpotifyManager.get_playlist_tracks`` returns); ``Song`` inputs are accepted
    where the real manager takes them.
    """

    def __init__(self, seeded: Optional[Seeded] = None) -> None:
        self.user_id = "test_user_id"
        # name -> spotify playlist id
        self.playlists: Dict[str, str] = {_PLAYLIST_NAME: "spotify_playlist_favorites"}
        # records of mutating calls for assertions in steps
        self.refresh_calls: List[Dict[str, Any]] = []
        self._tracks: List[Dict[str, Any]] = []
        if seeded is not None:
            self.playlists.setdefault(seeded.playlist_name, "spotify_playlist_favorites")
            for artist, name in _SEED_TRACKS[:2]:
                track_id = track_id_for(artist, name)
                self._tracks.append(
                    {
                        "name": name,
                        "artist": artist,
                        "uri": f"spotify:track:{track_id.replace('|', '')[:22]}",
                        "added_at": "2024-01-01T00:00:00Z",
                    }
                )

    def get_playlist_id(self, name: str) -> Optional[str]:
        return self.playlists.get(name)

    def get_playlist_tracks(self, name: str) -> List[Dict[str, Any]]:
        if name not in self.playlists:
            return []
        return list(self._tracks)

    def search_song(self, song: Song) -> Optional[str]:
        return f"spotify:track:{track_id_for(song.artist, song.name).replace('|', '')[:22]}"

    def get_track_info(self, uri: str) -> Optional[Dict[str, Any]]:
        return {"name": "Fake Track", "artist": "Fake Artist", "uri": uri}

    def create_playlist(self, name: str, description: str = "") -> str:
        pid = f"spotify_playlist_{name.lower().replace(' ', '_')}"
        self.playlists[name] = pid
        return pid

    def refresh_playlist(self, name: str, songs: List[Song], sync_mode: bool = False) -> bool:
        self.refresh_calls.append({"name": name, "songs": list(songs), "sync_mode": sync_mode})
        return True

    def append_to_playlist(self, name: str, songs: List[Song]) -> bool:
        return True

    def remove_from_playlist(self, name: str, track_uris: List[str]) -> bool:
        return True


@pytest.fixture
def fake_spotify(seeded_repos: Seeded) -> FakeSpotify:
    """A deterministic fake SpotifyManager wired with the seeded playlist."""
    return FakeSpotify(seeded_repos)


@pytest.fixture
def cli(seeded_repos: Seeded, fake_spotify: FakeSpotify) -> PlaylistCLI:
    """Fully-wired PlaylistCLI built via ``__new__`` (no real ``__init__``).

    Sets every private attr the lazy ``@property`` getters guard on
    (``_db``/``_spotify``/``_repos``/``_storage``/``_search_pipeline``/
    ``_rotation_managers``) plus all ``last_search_*`` state, so the properties
    return our wired objects without ever touching real services. The attribute
    set is replicated from ``src/main.py`` ``PlaylistCLI.__init__``.
    """
    c = PlaylistCLI.__new__(PlaylistCLI)
    c._db = seeded_repos.store
    c._spotify = fake_spotify
    c._storage = seeded_repos.db  # exposes .connect() like the real Database
    c._repos = seeded_repos.repos
    c._search_pipeline = None
    c._rotation_managers = {}
    c.last_search_results = None
    c.last_search_query = None
    c.last_search_summary = None
    c.last_search_metrics = None
    c.last_search_constraints = None
    c.last_search_expanded = False
    c.last_search_policy = None
    c.last_search_run_id = None
    c.last_search_track_ids = None
    c.last_search_cached = False
    return c


class Run:
    """Callable helper: parse a command string and dispatch it against ``cli``.

    Usage in a step:  ``rc = run("view Favorites")``
    Uses the REAL parser (``arg_parse.parse_tokens``) so tests exercise argument
    parsing end to end, then ``dispatch_command(cli, command, args)``.
    Returns the integer return code (0 == success). Raises ValueError if the
    command string fails to parse (so step bugs surface loudly).
    """

    def __init__(self, cli: PlaylistCLI) -> None:
        self._cli = cli

    def __call__(self, command_str: str) -> int:
        tokens = command_str.split()
        command, args, error = parse_tokens(tokens)
        if error is not None or command is None:
            raise ValueError(f"Failed to parse command {command_str!r}: {error}")
        return dispatch_command(self._cli, command, args)


@pytest.fixture
def run(cli: PlaylistCLI) -> Run:
    """A ``Run`` helper bound to the wired ``cli`` fixture."""
    return Run(cli)
