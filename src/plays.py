"""Pure play-ledger aggregations over ``listen_events``.

This module is deliberately dependency-light: every function takes a plain
``sqlite3.Connection`` (rows keyed by name via ``sqlite3.Row`` or tuples) and
returns plain Python data — no Rich, no Spotify, no repos. It is the single
home of the *play definition* so every consumer (dashboards, rotation,
recency weighting) counts plays the same way:

    a ``listen_events`` row counts as a play iff
    ``ms_played IS NULL OR ms_played >= 30000``

(``ms_played`` is only known for GDPR-export rows; live ``recently_played``
polling never reports it, and Spotify itself only ledger-counts ≥30s plays.)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Spotify's own ledger rule: a stream under 30 seconds is not a play.
PLAY_MS_THRESHOLD = 30000

# The canonical play predicate (see module docstring).
_PLAY_PREDICATE = f"(ms_played IS NULL OR ms_played >= {PLAY_MS_THRESHOLD})"


def _since_clause(since: Optional[str]) -> Tuple[str, List[str]]:
    """SQL fragment + params for an optional ``played_at >= since`` filter.

    ``played_at`` is stored as UTC ISO-8601 (trailing ``Z``), so lexicographic
    comparison is chronological comparison.
    """
    if since is None:
        return "", []
    return " AND played_at >= ?", [since]


def parse_played_at(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ``played_at`` into an aware UTC datetime (None if unparseable)."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def play_counts(conn: sqlite3.Connection, since: Optional[str] = None) -> Dict[str, int]:
    """track_id -> number of plays (30s rule), optionally since an ISO timestamp."""
    clause, params = _since_clause(since)
    rows = conn.execute(
        f"""
        SELECT track_id, COUNT(*) AS plays
        FROM listen_events
        WHERE {_PLAY_PREDICATE}{clause}
        GROUP BY track_id;
        """,
        params,
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def top_played(
    conn: sqlite3.Connection,
    limit: int = 20,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Most-played tracks joined to their display names.

    Each row: ``{track_id, name, artist, plays, last_played}``. Tracks missing
    from ``tracks`` (orphan events) still appear, with empty name/artist.
    """
    clause, params = _since_clause(since)
    rows = conn.execute(
        f"""
        SELECT le.track_id AS track_id,
               COALESCE(t.name, '') AS name,
               COALESCE(a.name, t.artist_id, '') AS artist,
               COUNT(*) AS plays,
               MAX(le.played_at) AS last_played
        FROM listen_events le
        LEFT JOIN tracks t ON t.track_id = le.track_id
        LEFT JOIN artists a ON a.artist_id = t.artist_id
        WHERE {_PLAY_PREDICATE}{clause}
        GROUP BY le.track_id
        ORDER BY plays DESC, last_played DESC
        LIMIT ?;
        """,
        params + [int(limit)],
    ).fetchall()
    return [
        {
            "track_id": str(row[0]),
            "name": str(row[1]),
            "artist": str(row[2] or ""),
            "plays": int(row[3]),
            "last_played": row[4],
        }
        for row in rows
    ]


def daily_counts(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
) -> List[Tuple[str, int]]:
    """Plays per UTC calendar day: ``[('2026-07-24', 12), …]`` ascending by day.

    The day is the ``played_at`` date prefix (UTC — the stored form), the play
    rule applies, and days with zero plays simply don't appear (the ledger has
    gaps while tunr is closed; fabricating zero-days would imply coverage).
    Events without a ``played_at`` are skipped.
    """
    clause, params = _since_clause(since)
    rows = conn.execute(
        f"""
        SELECT substr(played_at, 1, 10) AS day, COUNT(*) AS plays
        FROM listen_events
        WHERE {_PLAY_PREDICATE}{clause}
          AND played_at IS NOT NULL AND length(played_at) >= 10
        GROUP BY day
        ORDER BY day ASC;
        """,
        params,
    ).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows]


def _played_at_values(conn: sqlite3.Connection, since: Optional[str]) -> List[Optional[str]]:
    clause, params = _since_clause(since)
    rows = conn.execute(
        f"""
        SELECT played_at FROM listen_events
        WHERE {_PLAY_PREDICATE}{clause};
        """,
        params,
    ).fetchall()
    return [row[0] for row in rows]


def listening_clock(conn: sqlite3.Connection, since: Optional[str] = None) -> List[int]:
    """24 hourly play buckets (index = UTC hour of ``played_at``)."""
    buckets = [0] * 24
    for value in _played_at_values(conn, since):
        parsed = parse_played_at(value)
        if parsed is None:
            continue
        buckets[parsed.hour] += 1
    return buckets


def weekday_histogram(conn: sqlite3.Connection, since: Optional[str] = None) -> List[int]:
    """7 play buckets, Monday..Sunday (``datetime.weekday()`` order), in UTC."""
    buckets = [0] * 7
    for value in _played_at_values(conn, since):
        parsed = parse_played_at(value)
        if parsed is None:
            continue
        buckets[parsed.weekday()] += 1
    return buckets


def plays_meta(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Provenance summary for honesty captions.

    Returns ``{total_events, counted_plays, first_played_at, last_played_at,
    sources}`` where ``sources`` maps source -> event count. ``total_events``
    is ALL ledger rows; ``counted_plays`` applies the 30s play rule — the gap
    between the two is exactly the sub-30s/skipped telemetry.
    """
    totals = conn.execute(
        f"""
        SELECT COUNT(*) AS total_events,
               SUM(CASE WHEN {_PLAY_PREDICATE} THEN 1 ELSE 0 END) AS counted_plays,
               MIN(played_at) AS first_played_at,
               MAX(played_at) AS last_played_at
        FROM listen_events;
        """
    ).fetchone()
    source_rows = conn.execute(
        """
        SELECT COALESCE(source, 'unknown') AS source, COUNT(*) AS events
        FROM listen_events
        GROUP BY COALESCE(source, 'unknown');
        """
    ).fetchall()
    return {
        "total_events": int(totals[0] or 0),
        "counted_plays": int(totals[1] or 0),
        "first_played_at": totals[2],
        "last_played_at": totals[3],
        "sources": {str(row[0]): int(row[1]) for row in source_rows},
    }


def recency_weights(
    conn: sqlite3.Connection,
    half_life_days: float = 90.0,
    now: Optional[datetime] = None,
) -> Dict[str, float]:
    """track_id -> recency-weighted play mass (exponential decay, summed).

    Each play contributes ``0.5 ** (age_days / half_life_days)``: a play right
    now weighs 1.0, a play one half-life ago weighs 0.5. Pass ``now`` (aware
    datetime) for deterministic tests; defaults to the current UTC time.
    Events with an unparseable ``played_at`` are skipped; events "from the
    future" (clock skew) are clamped to age 0.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    rows = conn.execute(
        f"""
        SELECT track_id, played_at FROM listen_events
        WHERE {_PLAY_PREDICATE};
        """
    ).fetchall()
    weights: Dict[str, float] = {}
    for row in rows:
        parsed = parse_played_at(row[1])
        if parsed is None:
            continue
        age_days = max(0.0, (reference - parsed).total_seconds() / 86400.0)
        weight = 0.5 ** (age_days / half_life_days)
        track_id = str(row[0])
        weights[track_id] = weights.get(track_id, 0.0) + weight
    return weights
