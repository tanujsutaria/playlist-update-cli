"""Tests for the AcousticBrainz sonic backfill (nextgen.acoustic). Offline:
the two network functions (resolve_mbid, fetch_features) / _get_json are
monkeypatched, so no request is ever made. Includes a hard guard that the
outbound User-Agent never carries personal info.
"""

from __future__ import annotations

from nextgen import acoustic as ac
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories
from storage.sonic import SONIC_DIM
from storage.vectors import decode_vector

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
    "lowlevel": {"average_loudness": 0.31, "dissonance": {"mean": 0.44}},
    "tonal": {"key_strength": 0.67},
}


class TestUserAgentNoPII:
    def test_default_carries_no_personal_info(self, monkeypatch):
        monkeypatch.delenv("TUNR_MUSICBRAINZ_UA", raising=False)
        ua = ac.user_agent()
        assert ua  # non-empty (MusicBrainz needs a UA)
        assert "@" not in ua  # never an email
        assert "tanujsutaria" not in ua.lower()  # never the user's handle/name

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TUNR_MUSICBRAINZ_UA", "myapp/2.0 ( https://example.com )")
        assert ac.user_agent() == "myapp/2.0 ( https://example.com )"


class TestResolveMbid:
    def test_top_match_above_score(self, monkeypatch):
        monkeypatch.setattr(
            ac, "_get_json", lambda url, **kw: {"recordings": [{"id": "mbid-1", "score": 100}]}
        )
        assert ac.resolve_mbid("Radiohead", "Creep") == "mbid-1"

    def test_low_score_rejected(self, monkeypatch):
        monkeypatch.setattr(
            ac, "_get_json", lambda url, **kw: {"recordings": [{"id": "x", "score": 50}]}
        )
        assert ac.resolve_mbid("a", "b") is None

    def test_no_recordings(self, monkeypatch):
        monkeypatch.setattr(ac, "_get_json", lambda url, **kw: {"recordings": []})
        assert ac.resolve_mbid("a", "b") is None


class TestFetchFeatures:
    def test_returns_highlevel_and_lowlevel(self, monkeypatch):
        def fake(url, **kw):
            return {"highlevel": HL} if "high-level" in url else LL

        monkeypatch.setattr(ac, "_get_json", fake)
        hl, ll = ac.fetch_features("mbid-1")
        assert hl == HL and ll == LL

    def test_no_ab_data_returns_none(self, monkeypatch):
        monkeypatch.setattr(ac, "_get_json", lambda url, **kw: None)  # high-level 404
        assert ac.fetch_features("mbid-1") is None


def _repos(tmp_path, tracks):
    conn = Database(tmp_path / "tunr.db").connect()
    ensure_schema(conn)
    conn.execute("INSERT INTO artists (artist_id, name) VALUES ('a', 'A')")
    conn.executemany(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, 'a', 'candidate')",
        tracks,
    )
    conn.commit()
    return Repositories(conn)


class TestBackfillSonic:
    def test_mixed_outcomes(self, tmp_path, monkeypatch):
        repos = _repos(tmp_path, [("a|||s", "S"), ("a|||n", "N"), ("a|||d", "D"), ("a|||f", "F")])

        def fake_resolve(artist, name, **kw):
            return {"S": "mb-s", "N": None, "D": "mb-d", "F": "boom"}[name]

        def fake_fetch(mbid):
            if mbid == "boom":
                raise RuntimeError("network down")
            if mbid == "mb-d":
                return None  # no AcousticBrainz data
            return (HL, LL)

        monkeypatch.setattr(ac, "resolve_mbid", fake_resolve)
        monkeypatch.setattr(ac, "fetch_features", fake_fetch)
        counts = ac.backfill_sonic(
            repos,
            [("a|||s", "S", "A"), ("a|||n", "N", "A"), ("a|||d", "D", "A"), ("a|||f", "F", "A")],
            throttle=0,
        )
        assert counts == {"stored": 1, "no_mbid": 1, "no_data": 1, "failed": 1}
        stored = repos.sonic.get("a|||s")
        assert stored is not None
        assert stored["mbid"] == "mb-s"
        assert stored["sonic_dim"] == SONIC_DIM
        assert len(decode_vector(stored["sonic_blob"])) == SONIC_DIM
        assert repos.sonic.get("a|||n") is None  # no_mbid wrote nothing
        assert repos.sonic.get("a|||d") is None  # no_data wrote nothing

    def test_on_result_callback(self, tmp_path, monkeypatch):
        repos = _repos(tmp_path, [("a|||s", "S")])
        monkeypatch.setattr(ac, "resolve_mbid", lambda artist, name, **kw: "mb")
        monkeypatch.setattr(ac, "fetch_features", lambda mbid: (HL, LL))
        seen = []
        ac.backfill_sonic(
            repos,
            [("a|||s", "S", "A")],
            on_result=lambda status, name, artist: seen.append(status),
            throttle=0,
        )
        assert seen == ["stored"]

    def test_empty(self, tmp_path):
        repos = _repos(tmp_path, [("a|||s", "S")])
        assert ac.backfill_sonic(repos, [], throttle=0) == {
            "stored": 0,
            "no_mbid": 0,
            "no_data": 0,
            "failed": 0,
        }
