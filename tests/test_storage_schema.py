from __future__ import annotations

from pathlib import Path

from storage.db import Database
from storage.migrations import ensure_schema


def _table_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    return {row[0] if isinstance(row, tuple) else row["name"] for row in rows}


def test_schema_bootstrap(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    conn = db.connect()
    ensure_schema(conn)

    names = _table_names(conn)
    assert "tracks" in names
    assert "artists" in names
    assert "track_context" in names
    assert "track_embeddings" in names
    assert "queries" in names
    assert "search_runs" in names
    assert "search_candidates" in names
    assert "track_sources" in names
    assert "listen_events" in names
