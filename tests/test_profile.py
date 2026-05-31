"""Integration tests for PlaylistCLI.show_profile (the /profile visualization).

Builds a real (temp) SQLite store with a tiny corpus + rotation history and
asserts the rendered profile reports honest coverage numbers. Offline; no
Spotify, no embedding model.
"""

from __future__ import annotations

import pytest

import ui
from main import PlaylistCLI
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories


def _cli_over(conn) -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    return cli


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
