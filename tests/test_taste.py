"""Integration tests for PlaylistCLI.show_taste (the /taste profile card).

Seeds a temp SQLite store with known embeddings so the centroid ranking is
deterministic, and asserts the most/least representative tracks land correctly
and the (honest) text-based caveat is shown. Offline; no embedding model.
"""

from __future__ import annotations

import pytest

import ui
from main import PlaylistCLI
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

# Three tracks cluster on axis 0; one outlier sits on axis 3.
EMB = {
    "wild nothing|||a": [1.0, 0.0, 0.0, 0.0],
    "wild nothing|||b": [0.9, 0.1, 0.0, 0.0],
    "wild nothing|||c": [0.8, 0.2, 0.0, 0.0],
    "outlier artist|||z": [0.0, 0.0, 0.0, 1.0],
}
NAMES = {
    "wild nothing|||a": ("Alpha", "wild nothing", "Wild Nothing"),
    "wild nothing|||b": ("Beta", "wild nothing", "Wild Nothing"),
    "wild nothing|||c": ("Gamma", "wild nothing", "Wild Nothing"),
    "outlier artist|||z": ("Zeta", "outlier artist", "Outlier Artist"),
}


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _cli_over(conn) -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    return cli


def _seed(tmp_path, with_rotation=True, with_listens=False):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    artists = {(aid, disp) for _, (_, aid, disp) in NAMES.items()}
    conn.executemany("INSERT INTO artists (artist_id, name) VALUES (?, ?)", list(artists))
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [(tid, NAMES[tid][0], NAMES[tid][1]) for tid in EMB],
    )
    conn.executemany(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (tid, "all-mpnet-base-v2", encode_vector(vec), len(vec), vector_norm(vec))
            for tid, vec in EMB.items()
        ],
    )
    if with_rotation:
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('pl','mix',0)"
        )
        conn.execute(
            "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
            "VALUES ('g0','pl',0)"
        )
        conn.executemany(
            "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES ('g0', ?, ?)",
            [(tid, i) for i, tid in enumerate(EMB)],
        )
    if with_listens:
        conn.executemany(
            "INSERT INTO listen_events (event_id, track_id, played_at, source) "
            "VALUES (?, ?, ?, 'recently_played')",
            [(f"e{i}", tid, f"2026-05-3{i}T00:00:00Z") for i, tid in enumerate(EMB)],
        )
    conn.commit()
    return _cli_over(conn)


class TestShowTaste:
    def test_representative_ranking_is_correct(self, tmp_path, capsys):
        cli = _seed(tmp_path)
        cli.show_taste(top=2)
        out = capsys.readouterr().out
        assert "Your Taste" in out
        assert "Most representative" in out
        # Axis-0 cluster dominates the centroid -> Alpha/Beta are most central.
        assert "Alpha" in out
        # The outlier shows up under the widest-ranging section, not as representative.
        assert "Widest-ranging" in out
        assert "Zeta" in out

    def test_source_and_signal_labels(self, tmp_path, capsys):
        cli = _seed(tmp_path)  # rotation seeded, no listens, no context
        cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "your rotation" in out  # seeded from generation_tracks
        assert "text-based" in out  # track_context empty -> lexical signal
        assert "/enrich" in out  # honest deepening hint
        assert "Distinct artists" in out

    def test_prefers_recent_plays_when_present(self, tmp_path, capsys):
        cli = _seed(tmp_path, with_listens=True)
        cli.show_taste(top=4)
        out = capsys.readouterr().out
        assert "recent plays" in out  # listen_events takes priority over rotation

    def test_too_few_tracks_message(self, tmp_path, capsys):
        db = Database(tmp_path / "sparse.db")
        conn = db.connect()
        ensure_schema(conn)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a','A')")
        conn.execute(
            "INSERT INTO tracks (track_id, name, artist_id, status) VALUES ('a|||x','X','a','candidate')"
        )
        conn.execute(
            "INSERT INTO track_embeddings "
            "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
            "VALUES ('a|||x','all-mpnet-base-v2',?,2,?)",
            (encode_vector([1.0, 0.0]), vector_norm([1.0, 0.0])),
        )
        conn.commit()
        cli = _cli_over(conn)
        cli.show_taste()
        out = capsys.readouterr().out
        assert "Not enough embedded tracks" in out
