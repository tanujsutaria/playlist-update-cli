"""The data-scope registry: every denominator a view may claim, defined once.

Since the 2026-07-26 /pull import, the ``tracks`` table is a raw Spotify
library MIRROR (~15k rows), of which only the enriched slice (~8%) is the
CURATED CORE the taste/enrichment views are built on. Every count, share, and
caption must name which population it describes — this module is the single
place those populations (and their canonical caption strings) are defined, so
/dash, /taste, /stats, and /profile can never disagree about a denominator.

Scopes (all JOIN-counted against live ``tracks`` rows, so an orphan side-table
row never inflates a count):

* **mirror** — every row in ``tracks``: the raw /pull library mirror plus the
  curated core. The honest denominator for storage/coverage panels only.
* **curated** — tracks with enriched context (``track_context``): the curated
  core. Taste facets, era histograms, and headlines scope HERE, deliberately —
  mirror rows must never dilute a facet share.
* **embedded** — tracks with an embedding: the taste-centroid population.
* **sonic** — tracks with AcousticBrainz features: the measured-sound scope.
* **liked** — Spotify hearts mirrored into ``liked_tracks``.
* **rotation** — distinct tracks that ever appeared in a rotation generation.

The plays ledger is NOT a track scope — it is an event window that starts at
the first ``/listen-sync`` poll and grows only while tunr runs; its caption
lives here too (``plays_scope_caption``) so no view can imply lifetime
coverage.

No Rich imports and no emission — pure counts and strings, offline-testable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class LibraryScopes:
    """Track-population sizes, one field per nameable scope."""

    mirror: int  # every live row in `tracks` (raw /pull mirror + curated core)
    curated: int  # tracks with enriched context — the curated core
    embedded: int  # tracks with an embedding
    sonic: int  # tracks with AcousticBrainz features
    liked: int  # Spotify hearts (liked_tracks)
    rotation: int  # distinct tracks ever rotated


def _join_count(conn: sqlite3.Connection, side_table: str) -> int:
    """Tracks with a row in `side_table` — JOIN-scoped to live tracks so an
    orphan side-table row (its track deleted) never inflates the count."""
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM tracks t JOIN {side_table} x ON x.track_id = t.track_id"
    ).fetchone()
    return int(row[0])


def library_scopes(conn: sqlite3.Connection) -> LibraryScopes:
    """Count every scope in one pass. Trusted-literal table names only."""
    mirror = int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
    rotation = int(
        conn.execute(
            "SELECT COUNT(DISTINCT gt.track_id) FROM generation_tracks gt "
            "JOIN tracks t ON t.track_id = gt.track_id"
        ).fetchone()[0]
    )
    return LibraryScopes(
        mirror=mirror,
        curated=_join_count(conn, "track_context"),
        embedded=_join_count(conn, "track_embeddings"),
        sonic=_join_count(conn, "track_sonic"),
        liked=_join_count(conn, "liked_tracks"),
        rotation=rotation,
    )


def scopes_payload(scopes: LibraryScopes) -> Dict[str, int]:
    """The additive `--json` fragment (field name -> count)."""
    return asdict(scopes)


def fmt_n(value: int) -> str:
    """Thousands-grouped count for captions: 15468 -> '15,468'."""
    return f"{value:,}"


def enriched_pct_str(scopes: LibraryScopes) -> str:
    """The curated share of the mirror as a whole-percent string.

    A non-zero core never rounds to an outright '0%' — it reads '<1%' —
    and an empty mirror reads '0%' (no division).
    """
    if scopes.mirror <= 0:
        return "0%"
    pct = scopes.curated / scopes.mirror * 100
    if 0 < pct < 1:
        return "<1%"
    return f"{pct:.0f}%"


def curated_scope_caption(scopes: LibraryScopes) -> str:
    """'curated core · 1,244 enriched tracks' — the taste-view denominator."""
    return f"curated core · {fmt_n(scopes.curated)} enriched tracks"


def mirror_scope_caption(scopes: LibraryScopes) -> str:
    """'library mirror · 15,468 tracks · 8% enriched — /enrich grows this'."""
    return (
        f"library mirror · {fmt_n(scopes.mirror)} tracks · "
        f"{enriched_pct_str(scopes)} enriched — /enrich grows this"
    )


def plays_scope_caption(first_played_at: Optional[str]) -> str:
    """'plays · since 2026-07-24 · grows via /listen-sync'.

    The ledger window starts at the first polled event — NEVER lifetime listening
    (the GDPR export was declined) — so the since-date is non-negotiable in
    every plays caption. An empty ledger says how to start one.
    """
    since = (first_played_at or "")[:10]
    if not since:
        return "plays · none yet — /listen-sync starts the ledger"
    return f"plays · since {since} · grows via /listen-sync"
