"""Tests for src/plays.py — pure aggregations over the listen_events ledger.

Seeds a real (temp/in-memory) SQLite DB via ensure_schema and asserts the
play definition (ms_played IS NULL OR ms_played >= 30000), since-filtering,
clock/weekday bucketing, the honesty-caption meta feed, and recency-weight
monotonicity. All offline.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from plays import (
    PLAY_MS_THRESHOLD,
    daily_counts,
    last_played_map,
    listening_clock,
    parse_played_at,
    play_counts,
    plays_meta,
    recency_weights,
    top_played,
    weekday_histogram,
)
from storage.migrations import ensure_schema


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def _seed_track(conn: sqlite3.Connection, artist: str, name: str) -> str:
    track_id = f"{artist.lower()}|||{name.lower()}"
    conn.execute(
        "INSERT OR IGNORE INTO artists (artist_id, name) VALUES (?, ?);",
        (artist.lower(), artist),
    )
    conn.execute(
        "INSERT OR IGNORE INTO tracks (track_id, name, artist_id) VALUES (?, ?, ?);",
        (track_id, name, artist.lower()),
    )
    return track_id


def _add_event(
    conn: sqlite3.Connection,
    event_id: str,
    track_id: str,
    played_at: str,
    ms_played=None,
    source: str = "recently_played",
) -> None:
    conn.execute(
        """
        INSERT INTO listen_events (event_id, track_id, played_at, source, created_at, ms_played)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (event_id, track_id, played_at, source, played_at, ms_played),
    )


@pytest.fixture
def conn() -> sqlite3.Connection:
    return _connect()


# ---------------------------------------------------------------------------
# play_counts: the 30s rule + since filter
# ---------------------------------------------------------------------------


class TestPlayCounts:
    def test_30s_rule(self, conn):
        track = _seed_track(conn, "Artist A", "Song One")
        _add_event(conn, "e1", track, "2026-06-01T10:00:00Z", ms_played=None)  # counts
        _add_event(conn, "e2", track, "2026-06-01T11:00:00Z", ms_played=PLAY_MS_THRESHOLD)  # counts
        _add_event(conn, "e3", track, "2026-06-01T12:00:00Z", ms_played=29999)  # skip
        _add_event(conn, "e4", track, "2026-06-01T13:00:00Z", ms_played=0)  # skip

        assert play_counts(conn) == {track: 2}

    def test_since_filters_inclusive(self, conn):
        track = _seed_track(conn, "Artist A", "Song One")
        _add_event(conn, "e1", track, "2026-05-01T00:00:00Z")
        _add_event(conn, "e2", track, "2026-06-01T00:00:00Z")
        _add_event(conn, "e3", track, "2026-06-02T00:00:00Z")

        assert play_counts(conn, since="2026-06-01T00:00:00Z") == {track: 2}
        assert play_counts(conn) == {track: 3}

    def test_empty_ledger(self, conn):
        assert play_counts(conn) == {}


# ---------------------------------------------------------------------------
# last_played_map
# ---------------------------------------------------------------------------


class TestLastPlayedMap:
    def test_max_played_at_per_track_with_play_rule(self, conn):
        a = _seed_track(conn, "Artist A", "One")
        b = _seed_track(conn, "Artist B", "Two")
        _add_event(conn, "a1", a, "2026-06-01T10:00:00Z")
        _add_event(conn, "a2", a, "2026-06-03T10:00:00Z")
        # A later sub-30s skip must NOT become the track's last played.
        _add_event(conn, "a3", a, "2026-06-05T10:00:00Z", ms_played=5000)
        _add_event(conn, "b1", b, "2026-06-02T10:00:00Z", ms_played=PLAY_MS_THRESHOLD)

        assert last_played_map(conn) == {
            a: "2026-06-03T10:00:00Z",
            b: "2026-06-02T10:00:00Z",
        }

    def test_track_with_only_sub30s_events_absent(self, conn):
        a = _seed_track(conn, "Artist A", "One")
        _add_event(conn, "a1", a, "2026-06-01T10:00:00Z", ms_played=1000)
        assert last_played_map(conn) == {}

    def test_null_played_at_rows_ignored(self, conn):
        a = _seed_track(conn, "Artist A", "One")
        _add_event(conn, "a1", a, None)
        assert last_played_map(conn) == {}

    def test_empty_ledger(self, conn):
        assert last_played_map(conn) == {}


# ---------------------------------------------------------------------------
# top_played
# ---------------------------------------------------------------------------


class TestTopPlayed:
    def test_joins_names_and_orders_by_plays(self, conn):
        hot = _seed_track(conn, "Artist A", "Hot Song")
        cold = _seed_track(conn, "Artist B", "Cold Song")
        for i in range(3):
            _add_event(conn, f"hot-{i}", hot, f"2026-06-0{i + 1}T10:00:00Z")
        _add_event(conn, "cold-1", cold, "2026-06-05T10:00:00Z")

        rows = top_played(conn)
        assert [r["track_id"] for r in rows] == [hot, cold]
        assert rows[0]["plays"] == 3
        assert rows[0]["name"] == "Hot Song"
        assert rows[0]["artist"] == "Artist A"
        assert rows[0]["last_played"] == "2026-06-03T10:00:00Z"

    def test_limit_and_since(self, conn):
        a = _seed_track(conn, "Artist A", "One")
        b = _seed_track(conn, "Artist B", "Two")
        _add_event(conn, "a1", a, "2026-05-01T00:00:00Z")
        _add_event(conn, "a2", a, "2026-05-02T00:00:00Z")
        _add_event(conn, "b1", b, "2026-06-01T00:00:00Z")

        rows = top_played(conn, limit=1)
        assert len(rows) == 1
        assert rows[0]["track_id"] == a

        rows = top_played(conn, since="2026-06-01T00:00:00Z")
        assert [r["track_id"] for r in rows] == [b]

    def test_sub30s_events_do_not_count(self, conn):
        a = _seed_track(conn, "Artist A", "One")
        _add_event(conn, "a1", a, "2026-06-01T00:00:00Z", ms_played=5000)
        assert top_played(conn) == []


# ---------------------------------------------------------------------------
# listening_clock / weekday_histogram
# ---------------------------------------------------------------------------


class TestDailyCounts:
    def test_groups_by_utc_day_ascending_with_play_rule(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "e1", track, "2026-06-02T10:00:00Z")
        _add_event(conn, "e2", track, "2026-06-02T22:00:00Z")
        _add_event(conn, "e3", track, "2026-06-01T09:00:00Z")
        _add_event(conn, "skip", track, "2026-06-01T10:00:00Z", ms_played=5_000)  # never counts
        assert daily_counts(conn) == [("2026-06-01", 1), ("2026-06-02", 2)]

    def test_since_filter_and_gap_days_absent(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "e1", track, "2026-06-01T10:00:00Z")
        _add_event(conn, "e2", track, "2026-06-05T10:00:00Z")  # 3-day gap: no zero rows
        assert daily_counts(conn) == [("2026-06-01", 1), ("2026-06-05", 1)]
        assert daily_counts(conn, since="2026-06-02T00:00:00Z") == [("2026-06-05", 1)]

    def test_empty_ledger(self, conn):
        assert daily_counts(conn) == []


class TestClockAndWeekday:
    def test_clock_buckets_by_utc_hour(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "e1", track, "2026-06-01T00:15:00Z")
        _add_event(conn, "e2", track, "2026-06-01T23:59:59Z")
        _add_event(conn, "e3", track, "2026-06-02T23:00:00Z")
        # Offset form normalizes to UTC: 01:30+02:00 == 23:30Z (previous day).
        _add_event(conn, "e4", track, "2026-06-03T01:30:00+02:00")

        clock = listening_clock(conn)
        assert len(clock) == 24
        assert clock[0] == 1
        assert clock[23] == 3
        assert sum(clock) == 4

    def test_weekday_buckets_monday_first(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        # 2026-06-01 is a Monday; 2026-06-07 is a Sunday.
        _add_event(conn, "mon", track, "2026-06-01T10:00:00Z")
        _add_event(conn, "mon2", track, "2026-06-01T11:00:00Z")
        _add_event(conn, "sun", track, "2026-06-07T10:00:00Z")

        histogram = weekday_histogram(conn)
        assert len(histogram) == 7
        assert histogram[0] == 2  # Monday
        assert histogram[6] == 1  # Sunday
        assert sum(histogram) == 3

    def test_since_applies_and_skips_sub30s(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "old", track, "2026-05-01T05:00:00Z")
        _add_event(conn, "new", track, "2026-06-01T06:00:00Z")
        _add_event(conn, "skip", track, "2026-06-01T07:00:00Z", ms_played=1000)

        clock = listening_clock(conn, since="2026-06-01T00:00:00Z")
        assert clock[6] == 1
        assert sum(clock) == 1


# ---------------------------------------------------------------------------
# plays_meta
# ---------------------------------------------------------------------------


class TestPlaysMeta:
    def test_meta_counts_and_sources(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "e1", track, "2026-06-01T10:00:00Z", source="recently_played")
        _add_event(conn, "e2", track, "2026-06-02T10:00:00Z", ms_played=10_000, source="gdpr")
        _add_event(conn, "e3", track, "2026-06-03T10:00:00Z", ms_played=60_000, source="gdpr")

        meta = plays_meta(conn)
        assert meta["total_events"] == 3
        assert meta["counted_plays"] == 2  # the 10s gdpr row is sub-30s
        assert meta["first_played_at"] == "2026-06-01T10:00:00Z"
        assert meta["last_played_at"] == "2026-06-03T10:00:00Z"
        assert meta["sources"] == {"recently_played": 1, "gdpr": 2}

    def test_meta_empty_ledger(self, conn):
        meta = plays_meta(conn)
        assert meta["total_events"] == 0
        assert meta["counted_plays"] == 0
        assert meta["first_played_at"] is None
        assert meta["last_played_at"] is None
        assert meta["sources"] == {}


# ---------------------------------------------------------------------------
# recency_weights
# ---------------------------------------------------------------------------


class TestRecencyWeights:
    NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_newer_plays_weigh_more(self, conn):
        recent = _seed_track(conn, "Artist A", "Recent")
        stale = _seed_track(conn, "Artist B", "Stale")
        _add_event(conn, "r1", recent, self._iso(self.NOW - timedelta(days=1)))
        _add_event(conn, "s1", stale, self._iso(self.NOW - timedelta(days=300)))

        weights = recency_weights(conn, half_life_days=90.0, now=self.NOW)
        assert weights[recent] > weights[stale]

    def test_half_life_semantics(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "e1", track, self._iso(self.NOW - timedelta(days=90)))

        weights = recency_weights(conn, half_life_days=90.0, now=self.NOW)
        assert weights[track] == pytest.approx(0.5, abs=1e-6)

    def test_plays_sum_per_track(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "e1", track, self._iso(self.NOW))
        _add_event(conn, "e2", track, self._iso(self.NOW - timedelta(days=90)))

        weights = recency_weights(conn, half_life_days=90.0, now=self.NOW)
        assert weights[track] == pytest.approx(1.5, abs=1e-6)

    def test_sub30s_skipped_and_future_clamped(self, conn):
        track = _seed_track(conn, "Artist A", "Song")
        _add_event(conn, "skip", track, self._iso(self.NOW), ms_played=2000)
        _add_event(conn, "future", track, self._iso(self.NOW + timedelta(days=5)))

        weights = recency_weights(conn, half_life_days=90.0, now=self.NOW)
        # Sub-30s never counts; the future event clamps to weight 1.0.
        assert weights[track] == pytest.approx(1.0, abs=1e-6)

    def test_invalid_half_life_raises(self, conn):
        with pytest.raises(ValueError):
            recency_weights(conn, half_life_days=0.0, now=self.NOW)


# ---------------------------------------------------------------------------
# parse_played_at
# ---------------------------------------------------------------------------


class TestParsePlayedAt:
    def test_z_suffix_and_offsets(self):
        parsed = parse_played_at("2026-06-01T10:00:00Z")
        assert parsed == datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        parsed = parse_played_at("2026-06-01T12:00:00+02:00")
        assert parsed == datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_fractional_seconds(self):
        parsed = parse_played_at("2026-06-01T10:00:00.123Z")
        assert parsed is not None
        assert parsed.hour == 10

    def test_garbage_returns_none(self):
        assert parse_played_at(None) is None
        assert parse_played_at("") is None
        assert parse_played_at("not-a-date") is None
