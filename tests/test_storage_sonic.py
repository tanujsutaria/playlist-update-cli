"""Tests for the sonic feature foundation: the AcousticBrainz->vector builder,
the v5 track_sonic table, the repo, and the cohort-targeted /sonic candidate
selection. Offline; no network — the backfill itself is stubbed in the cohort
tests, only the SQL and output shape are under test.
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

import main as main_mod
import ui
from main import PlaylistCLI
from storage.db import Database
from storage.migrations import LATEST_VERSION, ensure_schema
from storage.repos import Repositories
from storage.sonic import SONIC_DIM, SONIC_FEATURES, build_sonic_vector
from storage.vectors import decode_vector, encode_vector

# Real AcousticBrainz shape (from Radiohead - Creep), abbreviated.
HL = {
    "danceability": {"value": "not_danceable", "probability": 1.0},
    "mood_acoustic": {"value": "not_acoustic", "probability": 0.904},
    "mood_aggressive": {"value": "not_aggressive", "probability": 1.0},
    "mood_electronic": {"value": "electronic", "probability": 0.979},
    "mood_happy": {"value": "not_happy", "probability": 0.671},
    "mood_party": {"value": "not_party", "probability": 1.0},
    "mood_relaxed": {"value": "relaxed", "probability": 0.809},
    "mood_sad": {"value": "sad", "probability": 0.565},
    "timbre": {"value": "dark", "probability": 0.966},
    "tonal_atonal": {"value": "atonal", "probability": 0.981},
    "voice_instrumental": {"value": "instrumental", "probability": 1.0},
}
LL = {
    "rhythm": {"bpm": 104.0},
    "lowlevel": {"average_loudness": 0.3147, "dissonance": {"mean": 0.441}},
    "tonal": {"key_strength": 0.6689},
}


def _idx(name):
    return SONIC_FEATURES.index(name)


class TestBuildSonicVector:
    def test_dimension_and_range(self):
        vec = build_sonic_vector(HL, LL)
        assert len(vec) == SONIC_DIM
        assert all(0.0 <= v <= 1.0 for v in vec)

    def test_binary_classifiers_folded_to_positive_prob(self):
        vec = build_sonic_vector(HL, LL)
        # "not_happy" p=0.671 -> happiness 0.329; "electronic" stays 0.979.
        assert vec[_idx("mood_happy")] == pytest.approx(1 - 0.671)
        assert vec[_idx("mood_electronic")] == pytest.approx(0.979)
        # "instrumental" is the negative of the "voice" axis -> 0.0.
        assert vec[_idx("voice_instrumental")] == pytest.approx(0.0)
        assert vec[_idx("danceability")] == pytest.approx(0.0)  # "not_danceable" p=1.0

    def test_low_level_scalars(self):
        vec = build_sonic_vector(HL, LL)
        assert vec[_idx("bpm_norm")] == pytest.approx((104.0 - 40.0) / (220.0 - 40.0))
        assert vec[_idx("average_loudness")] == pytest.approx(0.3147)
        assert vec[_idx("dissonance")] == pytest.approx(0.441)  # from {"mean": ...}
        assert vec[_idx("key_strength")] == pytest.approx(0.6689)

    def test_missing_payload_degrades_to_zeros(self):
        vec = build_sonic_vector({}, {})
        assert len(vec) == SONIC_DIM
        assert all(v == 0.0 for v in vec)

    def test_partial_payload_does_not_raise(self):
        vec = build_sonic_vector(HL, {})  # high-level only, no low-level
        assert len(vec) == SONIC_DIM
        assert vec[_idx("bpm_norm")] == 0.0
        assert vec[_idx("mood_electronic")] == pytest.approx(0.979)


class TestSonicSchemaAndRepo:
    def _conn(self, tmp_path):
        conn = Database(tmp_path / "tunr.db").connect()
        ensure_schema(conn)
        return conn

    def test_v5_creates_track_sonic(self, tmp_path):
        conn = self._conn(tmp_path)
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "track_sonic" in names
        # track_sonic lands in v5; ensure_schema migrates fully to the latest version.
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == LATEST_VERSION

    def test_upsert_get_roundtrip(self, tmp_path):
        conn = self._conn(tmp_path)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
        conn.execute(
            "INSERT INTO tracks (track_id, name, artist_id, status) "
            "VALUES ('a|||x', 'X', 'a', 'candidate')"
        )
        repos = Repositories(conn)
        vec = build_sonic_vector(HL, LL)
        repos.sonic.upsert(
            {
                "track_id": "a|||x",
                "mbid": "mb-123",
                "sonic_blob": encode_vector(vec),
                "sonic_dim": SONIC_DIM,
                "features_json": "{}",
                "source": "acousticbrainz",
                "created_at": "2026-06-02T00:00:00Z",
            }
        )
        conn.commit()
        got = repos.sonic.get("a|||x")
        assert got is not None
        assert got["mbid"] == "mb-123"
        assert got["sonic_dim"] == SONIC_DIM
        assert got["source"] == "acousticbrainz"
        assert decode_vector(got["sonic_blob"]) == pytest.approx(vec)
        assert repos.sonic.get("nope|||nope") is None


class TestDescribeSonic:
    def test_decodes_named_features_and_bpm(self):
        from storage.sonic import describe_sonic

        vec = build_sonic_vector(HL, LL)
        prof = describe_sonic(vec)
        # named features map straight through...
        assert prof["mood_relaxed"] == pytest.approx(0.809)
        assert prof["mood_electronic"] == pytest.approx(0.979)
        # ...and bpm_norm is denormalized to an approximate BPM (104 in the sample).
        assert "bpm_norm" not in prof
        assert prof["bpm"] == 104


def _rendered(captured, width: int = 120) -> str:
    buf = StringIO()
    console = Console(file=buf, width=width)
    for renderable in captured:
        console.print(renderable)
    return buf.getvalue()


class TestSonicCohorts:
    """Cohort-targeted /sonic: candidate selection joins the chosen source
    (played/liked/rotation/--playlist NAME); no flag stays whole-library.
    `backfill_sonic` is stubbed — no MusicBrainz/AcousticBrainz calls."""

    TRACKS = [("a|||one", "One"), ("a|||three", "Three"), ("a|||two", "Two")]

    @pytest.fixture(autouse=True)
    def _sink(self):
        self.captured = []
        ui.set_output_sink(self.captured.append)
        yield
        ui.set_output_sink(None)

    def _cli(self, tmp_path):
        conn = Database(tmp_path / "tunr.db").connect()
        ensure_schema(conn)
        conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
        conn.executemany(
            "INSERT INTO tracks (track_id, name, artist_id, status) "
            "VALUES (?, ?, 'a', 'candidate')",
            self.TRACKS,
        )
        conn.commit()
        cli = PlaylistCLI.__new__(PlaylistCLI)
        cli._repos = Repositories(conn)
        return cli

    def _stub_backfill(self, monkeypatch, stored_n=None):
        """Replace the real backfill; records the candidate list it was given."""
        seen = []

        def stub(repos, candidates, on_result=None, **kwargs):
            seen.extend(candidates)
            n = len(candidates) if stored_n is None else min(stored_n, len(candidates))
            return {"stored": n, "no_mbid": len(candidates) - n, "no_data": 0, "failed": 0}

        monkeypatch.setattr(main_mod, "backfill_sonic", stub)
        return seen

    def test_played_cohort_only(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)
        # Two events for the same track: DISTINCT keeps the cohort deduped.
        cli._repos.conn.executemany(
            "INSERT INTO listen_events (event_id, track_id) VALUES (?, ?)",
            [("ev-1", "a|||two"), ("ev-2", "a|||two")],
        )
        cli._repos.conn.commit()
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10, cohort="played") == 1
        assert [tid for tid, *_ in seen] == ["a|||two"]

    def test_liked_cohort_only(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)
        cli._repos.conn.execute("INSERT INTO liked_tracks (track_id) VALUES ('a|||one')")
        cli._repos.conn.commit()
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10, cohort="liked") == 1
        assert [tid for tid, *_ in seen] == ["a|||one"]

    def test_rotation_cohort_only(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)
        conn = cli._repos.conn
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, current_generation) VALUES ('p1', 'R', 0)"
        )
        conn.executemany(
            "INSERT INTO rotation_generations (generation_id, playlist_id, generation_index) "
            "VALUES (?, 'p1', ?)",
            [("g1", 0), ("g2", 1)],
        )
        # The same track in two generations must not duplicate the cohort.
        conn.executemany(
            "INSERT INTO generation_tracks (generation_id, track_id, position) VALUES (?, ?, 0)",
            [("g1", "a|||three"), ("g2", "a|||three")],
        )
        conn.commit()
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10, cohort="rotation") == 1
        assert [tid for tid, *_ in seen] == ["a|||three"]

    def test_playlist_cohort_matches_case_insensitively(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)
        repos = cli._repos
        repos.spotify_playlists.upsert(spotify_playlist_id="sp1", name="Daily Mix")
        repos.conn.executemany(
            "INSERT INTO playlist_tracks (spotify_playlist_id, track_id, position) "
            "VALUES ('sp1', ?, ?)",
            [("a|||one", 0), ("a|||two", 1)],
        )
        repos.conn.commit()
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10, cohort="playlist", playlist="daily mix") == 2
        assert [tid for tid, *_ in seen] == ["a|||one", "a|||two"]

    def test_empty_cohort_calls_nothing(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)  # no liked_tracks seeded
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10, cohort="liked") == 0
        assert seen == []
        assert "Cohort liked: 0 track(s)" in _rendered(self.captured)

    def test_playlist_miss_suggests_and_never_executes(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)
        cli._repos.spotify_playlists.upsert(spotify_playlist_id="sp1", name="Daily Mix")
        cli._repos.conn.commit()
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10, cohort="playlist", playlist="Daly Mix") == 0
        assert seen == []  # suggest-only: a near miss never runs the backfill
        rendered = _rendered(self.captured)
        assert "no playlist 'Daly Mix'" in rendered
        assert "Daily Mix" in rendered

    def test_coverage_readout_and_hit_rate_shape(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)
        repos = cli._repos
        repos.conn.executemany(
            "INSERT INTO liked_tracks (track_id) VALUES (?)", [("a|||one",), ("a|||two",)]
        )
        # One cohort track already has sonic features -> 50% covered, 1 missing.
        repos.sonic.upsert(
            {
                "track_id": "a|||one",
                "mbid": "mb-1",
                "sonic_blob": encode_vector(build_sonic_vector(HL, LL)),
                "sonic_dim": SONIC_DIM,
                "features_json": "{}",
                "source": "acousticbrainz",
                "created_at": "2026-01-01T00:00:00Z",
            }
        )
        repos.conn.commit()
        self._stub_backfill(monkeypatch, stored_n=0)  # AcousticBrainz misses it
        assert cli.sonic_backfill(limit=10, cohort="liked") == 0
        rendered = _rendered(self.captured)
        assert "Cohort liked: 2 track(s), 1 missing sonic features (50.0% covered)." in rendered
        assert "resolved 0/1 via AcousticBrainz" in rendered

    def test_cohort_dry_run_calls_nothing(self, tmp_path, monkeypatch):
        cli = self._cli(tmp_path)
        cli._repos.conn.execute("INSERT INTO liked_tracks (track_id) VALUES ('a|||one')")
        cli._repos.conn.commit()
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10, dry_run=True, cohort="liked") == 0
        assert seen == []
        assert "would resolve: One" in _rendered(self.captured)

    def test_no_cohort_stays_whole_library(self, tmp_path, monkeypatch):
        # No flag = today's behavior: all tracks, no cohort/hit-rate lines.
        cli = self._cli(tmp_path)
        seen = self._stub_backfill(monkeypatch)
        assert cli.sonic_backfill(limit=10) == 3
        assert [tid for tid, *_ in seen] == ["a|||one", "a|||three", "a|||two"]
        rendered = _rendered(self.captured)
        assert "Cohort" not in rendered
        assert "via AcousticBrainz" not in rendered
