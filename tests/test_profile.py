"""Integration tests for PlaylistCLI.show_profile (the /profile visualization).

Builds a real (temp) SQLite store with a tiny corpus + rotation history and
asserts the rendered profile reports honest coverage numbers. Offline; no
Spotify, no embedding model.
"""

from __future__ import annotations

import re

import pytest

import ui
from main import PlaylistCLI, _concentration_verdict
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(out: str) -> str:
    """Strip ANSI codes and panel borders, then collapse whitespace, so phrases
    wrapped across console lines (or folded inside a panel) can still be
    matched as substrings."""
    out = _ANSI.sub("", out)
    out = re.sub(r"[│╭╮╰╯]", " ", out)
    return re.sub(r"\s+", " ", out)


def _cli_over(conn) -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    return cli


def _fresh_cli(tmp_path, name: str = "tunr.db"):
    db = Database(tmp_path / name)
    conn = db.connect()
    ensure_schema(conn)
    return _cli_over(conn), conn


@pytest.fixture(autouse=True)
def _no_sink():
    """show_profile renders via ui._emit; with no sink it prints to the console
    so capsys can read it. Ensure no stray sink leaks in from another test."""
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


@pytest.fixture
def seeded_cli(tmp_path):
    """6 tracks across 3 artists; 2 generations rotating only tracks a, b, c
    (so 3 distinct rotated, 3 never rotated)."""
    db = Database(tmp_path / "tunr.db")
    conn = db.connect()
    ensure_schema(conn)

    conn.executemany(
        "INSERT INTO artists (artist_id, name) VALUES (?, ?)",
        [("a_wild", "Wild Nothing"), ("a_beach", "Beach Fossils"), ("a_blond", "Blondshell")],
    )
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
        [
            ("wild nothing|||a", "a", "a_wild"),
            ("wild nothing|||b", "b", "a_wild"),
            ("wild nothing|||c", "c", "a_wild"),
            ("beach fossils|||d", "d", "a_beach"),
            ("beach fossils|||e", "e", "a_beach"),
            ("blondshell|||f", "f", "a_blond"),
        ],
    )
    conn.execute(
        "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('pl', 'mix', 1)"
    )
    conn.executemany(
        "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
        "VALUES (?, 'pl', ?)",
        [("g0", 0), ("g1", 1)],
    )
    conn.executemany(
        "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES (?, ?, ?)",
        [
            ("g0", "wild nothing|||a", 0),
            ("g0", "wild nothing|||b", 1),
            ("g1", "wild nothing|||b", 0),
            ("g1", "wild nothing|||c", 1),
        ],
    )
    conn.commit()
    return _cli_over(conn)


class TestShowProfile:
    def test_reports_library_and_coverage(self, seeded_cli, capsys):
        seeded_cli.show_profile(top=10)
        out = capsys.readouterr().out
        assert "Library Profile" in out
        # 6 tracks, 3 distinct rotated, 3 never rotated -> both halves are 50%.
        assert "Never rotated" in out
        assert "50%" in out

    def test_top_artist_is_charted(self, seeded_cli, capsys):
        seeded_cli.show_profile(top=10)
        out = capsys.readouterr().out
        assert "Top artists" in out
        assert "Wild Nothing" in out  # 3 tracks -> the peak artist
        assert "█" in out  # a full bar for the peak

    def test_coverage_growth_section_present(self, seeded_cli, capsys):
        seeded_cli.show_profile(top=10)
        out = capsys.readouterr().out
        # Two generations -> a coverage-growth curve is rendered.
        assert "Rotation coverage growth" in out

    def test_empty_library_prompts_to_add(self, tmp_path, capsys):
        db = Database(tmp_path / "empty.db")
        conn = db.connect()
        ensure_schema(conn)
        cli = _cli_over(conn)
        cli.show_profile()
        out = capsys.readouterr().out
        assert "No tracks" in out

    def test_rotation_runway_line(self, seeded_cli, capsys):
        seeded_cli.show_profile(top=10)
        out = _flat(capsys.readouterr().out)
        assert "rotation runway" in out
        assert "3 played · 3 in the crate" in out


class TestArtistConcentration:
    @pytest.fixture
    def concentrated_cli(self, tmp_path):
        """12 tracks over 6 artists: one 6-track anchor, one duo, four
        one-track artists -> buckets 1:4 / 2:1 / 4+:1."""
        cli, conn = _fresh_cli(tmp_path)
        artists = [(f"a{i}", f"Artist {i}") for i in range(6)]
        conn.executemany("INSERT INTO artists (artist_id, name) VALUES (?, ?)", artists)
        rows = []
        for j in range(6):
            rows.append((f"a0|||t{j}", f"t{j}", "a0"))
        rows.append(("a1|||t0", "t0", "a1"))
        rows.append(("a1|||t1", "t1", "a1"))
        for i in range(2, 6):
            rows.append((f"a{i}|||t0", "t0", f"a{i}"))
        conn.executemany(
            "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, ?, 'candidate')",
            rows,
        )
        conn.commit()
        return cli

    def test_buckets_and_verdict_render(self, concentrated_cli, capsys):
        payload = concentrated_cli.show_profile(top=3)
        out = _flat(capsys.readouterr().out)
        assert "Artist concentration" in out
        assert "1 track" in out
        assert "2 tracks" in out
        assert "4+ tracks" in out
        # 6 artists, 12 tracks: the top 10 artists ARE all of them -> 100%.
        assert "your top 10 artists hold 12 tracks — 100.0% of the library" in out
        assert "concentrated on favorites" in out
        assert payload["concentration"]["buckets"] == [
            {"tracks_per_artist": 1, "artists": 4},
            {"tracks_per_artist": 2, "artists": 1},
            {"tracks_per_artist": 4, "artists": 1},
        ]
        assert payload["concentration"]["top10_track_share_pct"] == 100.0
        assert payload["one_track_artists"] == {"count": 4, "pct": 66.7}

    def test_skipped_below_five_artists(self, seeded_cli, capsys):
        seeded_cli.show_profile(top=10)
        out = capsys.readouterr().out
        assert "Artist concentration" not in out

    def test_verdict_threshold_boundaries(self):
        assert _concentration_verdict(9.9) == "no artist dominates"
        assert _concentration_verdict(10.0) == "a few favorites"
        assert _concentration_verdict(29.9) == "a few favorites"
        assert _concentration_verdict(30.0) == "concentrated on favorites"


class TestIngestHistory:
    def _seed_months(self, conn, months):
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
        rows = []
        for index, month in enumerate(months):
            rows.append((f"a|||t{index}", f"t{index}", "a", f"{month}-01T00:00:00"))
        conn.executemany(
            "INSERT INTO tracks (track_id, name, artist_id, status, created_at) "
            "VALUES (?, ?, ?, 'candidate', ?)",
            rows,
        )
        conn.commit()

    def test_months_render_with_waves_line(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        self._seed_months(conn, ["2025-03", "2025-03", "2026-06"])
        payload = cli.show_profile(top=5)
        out = capsys.readouterr().out
        assert "Ingest history" in out
        assert "2025-03" in out
        assert "Two ingest waves so far — first ingest Mar 2025, latest Jun 2026." in out
        assert payload["ingest_months"] == [
            {"month": "2025-03", "tracks": 2},
            {"month": "2026-06", "tracks": 1},
        ]

    def test_no_waves_line_beyond_four_months(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        self._seed_months(conn, ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05"])
        cli.show_profile(top=5)
        out = capsys.readouterr().out
        assert "Ingest history" in out
        assert "ingest waves so far" not in out

    def test_section_absent_without_created_at(self, seeded_cli, capsys):
        seeded_cli.show_profile(top=10)
        out = capsys.readouterr().out
        assert "Ingest history" not in out


class TestBackfillRunway:
    def test_gaps_render_in_panel(self, seeded_cli, capsys):
        payload = seeded_cli.show_profile(top=10)
        out = _flat(capsys.readouterr().out)
        assert "Backfill runway" in out
        assert "6 tracks awaiting embeddings" in out
        assert "6 unenriched" in out
        assert "6 without sonic data" in out
        assert "6 missing spotify ids" in out
        # Per-gap remedies: /enrich closes context+embedding gaps only; sonic
        # and spotify ids each name their own (honest) path.
        assert "/enrich backfills context + embeddings" in out
        assert "/sonic fetches AcousticBrainz features (partial by nature)" in out
        assert "spotify ids resolve on the next playlist match" in out
        assert payload["coverage"]["embeddings"] == {"have": 0, "total": 6}
        assert payload["backfill"] == {
            "missing_embeddings": 6,
            "missing_context": 6,
            "missing_sonic": 6,
            "missing_spotify_id": 6,
        }

    def _seed_complete(self, conn, with_sonic: bool):
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
        vec = [1.0, 0.0]
        for index in range(2):
            tid = f"a|||t{index}"
            conn.execute(
                "INSERT INTO tracks (track_id, name, artist_id, status, spotify_id) "
                "VALUES (?, ?, 'a', 'candidate', ?)",
                (tid, f"t{index}", f"sp{index}"),
            )
            conn.execute(
                "INSERT INTO track_embeddings "
                "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
                "VALUES (?, 'm', ?, 2, ?)",
                (tid, encode_vector(vec), vector_norm(vec)),
            )
            conn.execute(
                "INSERT INTO track_context (track_id, fields_json) VALUES (?, '[]')",
                (tid,),
            )
            if with_sonic:
                conn.execute(
                    "INSERT INTO track_sonic (track_id, sonic_blob, sonic_dim) VALUES (?, ?, 2)",
                    (tid, encode_vector(vec)),
                )
        conn.commit()

    def test_all_clear_renders_complete_info(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        self._seed_complete(conn, with_sonic=True)
        cli.show_profile(top=5)
        out = capsys.readouterr().out
        assert "Backfill complete — every track has embeddings, context, and ids." in out
        assert "Backfill runway" not in out

    def test_only_sonic_gap_is_partial_by_nature(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        self._seed_complete(conn, with_sonic=False)
        cli.show_profile(top=5)
        out = capsys.readouterr().out
        assert "2 tracks without sonic data — AcousticBrainz coverage is partial by nature." in out
        assert "Backfill complete" not in out
        assert "Backfill runway" not in out


class TestCoverageGrowthNotes:
    def test_timestamp_note_when_generations_backfilled_in_one_batch(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
        conn.executemany(
            "INSERT INTO tracks (track_id, name, artist_id, status) "
            "VALUES (?, ?, 'a', 'candidate')",
            [("a|||x", "x"), ("a|||y", "y")],
        )
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('pl', 'pl', 1)"
        )
        conn.executemany(
            "INSERT INTO rotation_generations "
            "(generation_id, playlist_id, generation_index, created_at) VALUES (?, 'pl', ?, ?)",
            [("g0", 0, "2026-01-01T00:00:00"), ("g1", 1, "2026-01-01T00:00:00")],
        )
        conn.executemany(
            "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES (?, ?, 0)",
            [("g0", "a|||x"), ("g1", "a|||y")],
        )
        conn.commit()
        cli.show_profile(top=5)
        out = capsys.readouterr().out
        assert "Rotation coverage growth" in out
        assert "generation timestamps were backfilled in one batch" in out
        assert "First → latest: 1 → 2 distinct tracks rotated" in out

    def test_no_timestamp_note_with_distinct_timestamps(self, tmp_path, capsys):
        cli, conn = _fresh_cli(tmp_path)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
        conn.executemany(
            "INSERT INTO tracks (track_id, name, artist_id, status) "
            "VALUES (?, ?, 'a', 'candidate')",
            [("a|||x", "x"), ("a|||y", "y")],
        )
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('pl', 'pl', 1)"
        )
        conn.executemany(
            "INSERT INTO rotation_generations "
            "(generation_id, playlist_id, generation_index, created_at) VALUES (?, 'pl', ?, ?)",
            [("g0", 0, "2026-01-01T00:00:00"), ("g1", 1, "2026-02-01T00:00:00")],
        )
        conn.executemany(
            "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES (?, ?, 0)",
            [("g0", "a|||x"), ("g1", "a|||y")],
        )
        conn.commit()
        cli.show_profile(top=5)
        out = capsys.readouterr().out
        assert "Rotation coverage growth" in out
        assert "backfilled in one batch" not in out


class TestProfilePayloadAdditions:
    def test_existing_keys_unchanged_and_new_keys_present(self, seeded_cli):
        payload = seeded_cli.show_profile(top=10)
        # Old contract, untouched.
        assert payload["tracks"] == 6
        assert payload["artists"] == 3
        assert payload["rotated"] == 3
        assert payload["never_rotated"] == 3
        assert payload["generations"] == 2
        # New keys, additive.
        assert payload["coverage"]["sonic"] == {"have": 0, "total": 6}
        assert payload["backfill"]["missing_embeddings"] == 6
        assert payload["concentration"]["buckets"] == [
            {"tracks_per_artist": 1, "artists": 1},
            {"tracks_per_artist": 2, "artists": 1},
            {"tracks_per_artist": 3, "artists": 1},
        ]
        assert payload["one_track_artists"] == {"count": 1, "pct": 33.3}
        assert payload["ingest_months"] == []

    def test_empty_library_payload_gains_zeroed_keys(self, tmp_path):
        cli, _conn = _fresh_cli(tmp_path)
        payload = cli.show_profile()
        assert payload["tracks"] == 0
        assert payload["coverage"] == {
            key: {"have": 0, "total": 0} for key in ("embeddings", "context", "sonic", "spotify_id")
        }
        assert payload["backfill"] == {
            "missing_embeddings": 0,
            "missing_context": 0,
            "missing_sonic": 0,
            "missing_spotify_id": 0,
        }
        assert payload["concentration"] == {"buckets": [], "top10_track_share_pct": 0.0}
        assert payload["one_track_artists"] == {"count": 0, "pct": 0.0}
        assert payload["ingest_months"] == []
