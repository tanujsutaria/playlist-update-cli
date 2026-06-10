"""Tests for the --json headless output mode.

Two layers: (1) the ui-level json mode (decorative output suppressed at the
`_emit` choke point; `emit_json` prints clean JSON), and (2) the command handlers
emitting structured payloads for profile/taste/search. Offline; search uses a
faked `search_songs` so no pipeline/network runs.
"""

from __future__ import annotations

import argparse
import json

import pytest

import ui
from main import PlaylistCLI, dispatch_command
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm


@pytest.fixture(autouse=True)
def _reset_modes():
    ui.set_output_sink(None)
    ui.set_json_mode(False)
    yield
    ui.set_output_sink(None)
    ui.set_json_mode(False)


def _args(**kwargs):
    return argparse.Namespace(**kwargs)


def _seed_cli(tmp_path):
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('wild nothing', 'Wild Nothing')")
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [
            ("wild nothing|||a", "Alpha", "wild nothing"),
            ("wild nothing|||b", "Beta", "wild nothing"),
            ("wild nothing|||c", "Gamma", "wild nothing"),
        ],
    )
    emb = {
        "wild nothing|||a": [1.0, 0.0],
        "wild nothing|||b": [0.9, 0.1],
        "wild nothing|||c": [0.8, 0.2],
    }
    conn.executemany(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (tid, "all-mpnet-base-v2", encode_vector(v), len(v), vector_norm(v))
            for tid, v in emb.items()
        ],
    )
    conn.commit()
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    return cli


class TestUiJsonMode:
    def test_emit_suppressed_in_json_mode(self, capsys):
        ui.set_json_mode(True)
        ui.info("should not appear")
        ui.section("nope")
        ui.table(["a"], [["b"]])
        ui.key_value_table([["k", "v"]])
        ui.bar_chart(["x"], [1.0])
        assert capsys.readouterr().out == ""

    def test_emit_json_is_valid_clean_json(self, capsys):
        ui.emit_json({"a": 1, "b": ["x", "y"]})
        out = capsys.readouterr().out
        assert json.loads(out) == {"a": 1, "b": ["x", "y"]}

    def test_rendering_restored_when_mode_off(self, capsys):
        ui.set_json_mode(True)
        ui.set_json_mode(False)
        ui.info("hello again")
        assert "hello again" in capsys.readouterr().out


class TestProfileJson:
    def test_profile_json_payload(self, tmp_path, capsys):
        cli = _seed_cli(tmp_path)
        rc = dispatch_command(cli, "profile", _args(top=15, json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["tracks"] == 3
        assert payload["artists"] == 1
        assert payload["coverage_growth"] == []  # no rotation generations seeded
        assert {a["name"] for a in payload["top_artists"]} == {"Wild Nothing"}
        # Liner-notes keys are additive (3 embedded tracks, nothing else seeded).
        assert payload["coverage"] == {
            "embeddings": {"have": 3, "total": 3},
            "context": {"have": 0, "total": 3},
            "sonic": {"have": 0, "total": 3},
            "spotify_id": {"have": 0, "total": 3},
        }
        assert payload["backfill"] == {
            "missing_embeddings": 0,
            "missing_context": 3,
            "missing_sonic": 3,
            "missing_spotify_id": 3,
        }
        assert payload["concentration"] == {
            "buckets": [{"tracks_per_artist": 3, "artists": 1}],
            "top10_track_share_pct": 100.0,
        }
        assert payload["one_track_artists"] == {"count": 0, "pct": 0.0}
        assert payload["ingest_months"] == []

    def test_profile_without_json_renders_tables(self, tmp_path, capsys):
        cli = _seed_cli(tmp_path)
        dispatch_command(cli, "profile", _args(top=15, json=False))
        out = capsys.readouterr().out
        assert "Library Profile" in out  # human rendering, not JSON
        assert not out.lstrip().startswith("{")


class TestTasteJson:
    def test_taste_json_payload(self, tmp_path, capsys):
        cli = _seed_cli(tmp_path)
        rc = dispatch_command(cli, "taste", _args(top=2, json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["built_from"] == 3
        assert payload["enriched"] is False  # no track_context -> text-based signal
        assert len(payload["most_representative"]) == 2
        assert "track_id" in payload["most_representative"][0]
        # Liner-notes keys are additive; all gated off on this sparse fixture.
        assert payload["taste_title"] is None
        assert payload["context_coverage"] == {"with_context": 0, "seed": 3}
        assert payload["facets"] is None  # no context in the seed
        assert payload["decades"] is None
        assert payload["bpm_spread"] is None
        assert payload["superlatives"] is None
        assert payload["core_vs_frontier"] is None
        assert payload["insights"] == []
        # List items gained sonic_informed/tags, key-additively.
        for row in payload["most_representative"]:
            assert row["sonic_informed"] is False
            assert row["tags"] == []


class TestSearchJson:
    def test_search_json_payload(self, capsys):
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._repos = None
        results = [
            {
                "song": "S",
                "artist": "A",
                "year": "2012",
                "score": 0.5,
                "strict_ratio": 0.4,
                "providers": [],
                "sources": [],
                "track_id": "a|||s",
            }
        ]

        def _fake_search(query):
            cli.last_search_results = results
            cli.last_search_query = "late night"
            cli.last_search_track_ids = ["a|||s"]

        cli.search_songs = _fake_search
        rc = dispatch_command(
            cli,
            "search",
            _args(
                query=["late", "night"],
                to_playlist=None,
                replace=False,
                save=False,
                limit=None,
                json=True,
            ),
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["query"] == "late night"
        assert payload["count"] == 1
        assert payload["results"][0]["track_id"] == "a|||s"
