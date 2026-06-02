"""Tests for the flagship /find — deep search re-ranked by taste, optional write.

`taste_rank_last_search` blends search-relevance with taste-similarity (min-max
normalized within the result set); `_handle_find` composes search -> rank ->
optional guarded add. Offline: search_songs is faked so no pipeline/network runs;
the taste centroid + result embeddings come from a seeded SQLite store.
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock

import pytest

import ui
from main import PlaylistCLI, dispatch_command
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

# Three axis-0 tracks define the user's taste; results split aligned vs off-taste.
TASTE = {"wn|||a": [1.0, 0.0], "wn|||b": [0.9, 0.1], "wn|||c": [0.8, 0.2]}
RESULT_EMB = {"r|||aligned": [1.0, 0.0], "r|||offbeat": [0.0, 1.0]}

# last_search_results: Aligned has LOW relevance, Offbeat has HIGH relevance —
# so taste vs relevance pull in opposite directions (clean to assert on).
RESULTS = [
    {
        "song": "Aligned",
        "artist": "Result Artist",
        "year": "2012",
        "score": 0.2,
        "strict_ratio": 0.5,
        "providers": [],
        "sources": [],
        "track_id": "r|||aligned",
    },
    {
        "song": "Offbeat",
        "artist": "Result Artist",
        "year": "2013",
        "score": 0.9,
        "strict_ratio": 0.5,
        "providers": [],
        "sources": [],
        "track_id": "r|||offbeat",
    },
]


@pytest.fixture(autouse=True)
def _reset():
    ui.set_output_sink(None)
    ui.set_json_mode(False)
    yield
    ui.set_output_sink(None)
    ui.set_json_mode(False)


def _seed(tmp_path, with_generation=True):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    conn.executemany(
        "INSERT INTO artists (artist_id, name) VALUES (?, ?)",
        [("wn", "Wild Nothing"), ("r", "Result Artist")],
    )
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [
            ("wn|||a", "Alpha", "wn"),
            ("wn|||b", "Beta", "wn"),
            ("wn|||c", "Gamma", "wn"),
            ("r|||aligned", "Aligned", "r"),
            ("r|||offbeat", "Offbeat", "r"),
        ],
    )
    all_emb = {**TASTE, **RESULT_EMB}
    conn.executemany(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (tid, "all-mpnet-base-v2", encode_vector(v), len(v), vector_norm(v))
            for tid, v in all_emb.items()
        ],
    )
    if with_generation:
        # Put ONLY the taste tracks in a rotation generation, so the centroid is
        # the taste cluster (not polluted by the result tracks' embeddings).
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('pl','mix',0)"
        )
        conn.execute(
            "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
            "VALUES ('g0','pl',0)"
        )
        conn.executemany(
            "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES ('g0', ?, ?)",
            [(tid, i) for i, tid in enumerate(TASTE)],
        )
    conn.commit()
    return conn


def _cli(conn):
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    cli.last_search_results = None
    cli.last_search_track_ids = None
    cli.last_search_query = None
    cli.last_search_handled = False
    return cli


def _mock_search(cli):
    def _search(query):
        cli.last_search_results = [dict(r) for r in RESULTS]
        cli.last_search_track_ids = [r["track_id"] for r in RESULTS]
        cli.last_search_query = " ".join(query) if isinstance(query, list) else str(query)

    cli.search_songs = _search


class TestTasteRank:
    def test_pure_taste_ranks_aligned_first(self, tmp_path):
        cli = _cli(_seed(tmp_path))
        cli.last_search_results = [dict(r) for r in RESULTS]
        ranked, signal = cli.taste_rank_last_search(taste_weight=1.0)
        assert ranked[0]["track_id"] == "r|||aligned"
        assert "your rotation" in signal  # seeded via generation_tracks

    def test_pure_relevance_ranks_offbeat_first(self, tmp_path):
        cli = _cli(_seed(tmp_path))
        cli.last_search_results = [dict(r) for r in RESULTS]
        ranked, _ = cli.taste_rank_last_search(taste_weight=0.0)
        assert ranked[0]["track_id"] == "r|||offbeat"  # higher relevance score

    def test_no_taste_signal_collapses_to_relevance(self, tmp_path):
        conn = _seed(tmp_path, with_generation=False)
        # Drop the taste embeddings: only 2 embedded tracks remain (< 3) -> no centroid.
        conn.execute("DELETE FROM track_embeddings WHERE track_id LIKE 'wn|||%'")
        conn.commit()
        cli = _cli(conn)
        cli.last_search_results = [dict(r) for r in RESULTS]
        ranked, signal = cli.taste_rank_last_search(taste_weight=1.0)
        assert "no taste signal" in signal
        assert ranked[0]["track_id"] == "r|||offbeat"  # relevance wins despite weight=1

    def test_empty_results(self, tmp_path):
        cli = _cli(_seed(tmp_path))
        cli.last_search_results = []
        ranked, signal = cli.taste_rank_last_search()
        assert ranked == []


class TestFindDispatch:
    def _args(self, **over):
        base = dict(
            query=["dreamy"],
            taste_weight=0.5,
            to_playlist=None,
            replace=False,
            limit=None,
            json=False,
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_preview_renders_ranked_table(self, tmp_path, capsys):
        cli = _cli(_seed(tmp_path))
        _mock_search(cli)
        rc = dispatch_command(cli, "find", self._args(taste_weight=1.0))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Find" in out
        assert "Aligned" in out and "Offbeat" in out
        assert "Preview only" in out  # no --to given

    def test_json_payload_is_taste_ranked(self, tmp_path, capsys):
        cli = _cli(_seed(tmp_path))
        _mock_search(cli)
        rc = dispatch_command(cli, "find", self._args(taste_weight=1.0, json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 2
        assert payload["taste_weight"] == 1.0
        assert payload["results"][0]["track_id"] == "r|||aligned"  # taste-first
        assert payload["wrote"] is None

    def test_to_writes_in_ranked_order(self, tmp_path):
        cli = _cli(_seed(tmp_path))
        _mock_search(cli)
        cli._spotify = MagicMock()
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.get_playlist_tracks.return_value = []
        cli._db = MagicMock()
        cli._undo_stack = []
        rc = dispatch_command(cli, "find", self._args(taste_weight=1.0, to_playlist="My Mix"))
        assert rc == 0
        cli._spotify.append_to_playlist.assert_called_once()
        name, songs = cli._spotify.append_to_playlist.call_args[0]
        assert name == "My Mix"
        # Taste-first order is what gets written.
        assert [s.id for s in songs] == ["r|||aligned", "r|||offbeat"]
        assert cli.last_search_handled is True

    def test_json_with_to_reports_wrote(self, tmp_path, capsys):
        cli = _cli(_seed(tmp_path))
        _mock_search(cli)
        cli._spotify = MagicMock()
        cli._spotify.append_to_playlist.return_value = True
        cli._spotify.get_playlist_tracks.return_value = []
        cli._db = MagicMock()
        cli._undo_stack = []
        dispatch_command(cli, "find", self._args(to_playlist="Mix", json=True))
        payload = json.loads(capsys.readouterr().out)
        assert payload["wrote"]["playlist"] == "Mix"
        assert payload["wrote"]["ok"] is True
        assert payload["wrote"]["requested"] == 2
