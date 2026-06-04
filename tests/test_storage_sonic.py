"""Tests for the sonic feature foundation: the AcousticBrainz->vector builder,
the v5 track_sonic table, and the repo. Offline; no network.
"""

from __future__ import annotations

import pytest

from storage.db import Database
from storage.migrations import ensure_schema
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
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 5

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
