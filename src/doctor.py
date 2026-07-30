"""Offline integrity audit over the SQLite system of record (/doctor).

This module is deliberately dependency-light, in the ``plays.py`` / ``scopes.py``
mold: every check is a pure function taking a plain ``sqlite3.Connection`` (and
filesystem paths where relevant) and returning a :class:`CheckResult` — no Rich,
no Spotify, no network, and **strictly read-only** (the only PRAGMAs issued are
the read-only ``integrity_check`` / ``foreign_key_check``). ``main._handle_doctor``
renders the rows; nothing here emits.

The check list is FIXED (no sprawl) and each check stays cheap at 15k-track
scale: counts ride the ``track_id`` primary keys, and embedding-blob decoding
is confined to the rows whose stored ``embedding_norm`` already looks wrong.

Verdict grammar (mirrors the ui color contract):

* ``ok``   — the invariant holds.
* ``warn`` — degraded but self-healing or non-fatal (tunr still works); the
  remedy names the exact command that clears it.
* ``fail`` — the system of record is unhealthy; ``/doctor`` exits nonzero.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.migrations import LATEST_VERSION
from storage.vectors import decode_vector, vector_norm

# The canonical embedding space: 768-dim all-mpnet-base-v2 (the re-embed target
# of the legacy migration — see CLAUDE.md). The model NAME is resolved from the
# caller (SEARCH_EMBEDDING_MODEL wins there); the dim is pinned here.
EXPECTED_EMBEDDING_DIM = 768
DEFAULT_EMBEDDING_MODEL = "all-mpnet-base-v2"

# Side tables whose track_id must reference a live tracks row. Trusted literals
# only — these are interpolated into SQL.
ORPHAN_TABLES = (
    "track_embeddings",
    "track_context",
    "track_sonic",
    "listen_events",
    "playlist_tracks",
    "liked_tracks",
    "generation_tracks",
)

# search_runs lifecycle: the pipeline inserts 'ok' and update_status records
# 'error' — anything else (NULL, 'running', ...) is a crash leftover. The cache
# only reuses status='ok' rows, so stuck runs are dead weight, not corruption.
TERMINAL_RUN_STATUSES = ("ok", "error")

# A WAL sidecar past this size means checkpoints haven't been running (a crash
# loop or a wedged reader); the DB is still consistent, hence warn not fail.
WAL_WARN_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class CheckResult:
    """One audit row: verdict + human detail + the exact remedy command."""

    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    remedy: str = ""


def _count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] or 0)


def _fmt_mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def check_sqlite_integrity(conn: sqlite3.Connection, db_path: Path) -> CheckResult:
    """PRAGMA integrity_check + foreign_key_check — the page/constraint truth."""
    row = conn.execute("PRAGMA integrity_check;").fetchone()
    verdict = str(row[0]) if row is not None else "no result"
    fk_rows = conn.execute("PRAGMA foreign_key_check;").fetchall()
    if verdict.lower() == "ok" and not fk_rows:
        return CheckResult("sqlite_integrity", "ok", "integrity_check ok, no FK violations")
    parts: List[str] = []
    if verdict.lower() != "ok":
        parts.append(f"integrity_check: {verdict}")
    if fk_rows:
        tables = sorted({str(r[0]) for r in fk_rows})
        parts.append(f"{len(fk_rows)} foreign-key violation(s) in {', '.join(tables)}")
    return CheckResult(
        "sqlite_integrity",
        "fail",
        "; ".join(parts),
        "/restore <name> (see /list-backups)",
    )


def check_schema_version(conn: sqlite3.Connection) -> CheckResult:
    """The stored schema_version row vs storage.migrations.LATEST_VERSION."""
    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1;").fetchone()
    except sqlite3.OperationalError:  # table absent: pre-schema / empty file
        row = None
    version: Optional[int] = int(row[0]) if row is not None and row[0] is not None else None
    if version == LATEST_VERSION:
        return CheckResult("schema_version", "ok", f"schema v{version} (latest)")
    if version is not None and version > LATEST_VERSION:
        # ensure_schema refuses to run against a future schema — hard stop.
        return CheckResult(
            "schema_version",
            "fail",
            f"schema v{version} is newer than this tunr supports (v{LATEST_VERSION})",
            "update tunr (git pull && make install)",
        )
    found = "missing" if version is None else f"v{version}"
    # Behind/missing self-heals: ensure_schema replays + commits on connect.
    return CheckResult(
        "schema_version",
        "warn",
        f"schema version {found}, latest is v{LATEST_VERSION}",
        "restart tunr — migrations run (and commit) on connect",
    )


def check_referential_orphans(conn: sqlite3.Connection, db_path: Path) -> CheckResult:
    """Side-table rows whose track_id has no live ``tracks`` row.

    JOIN-scoped views (coverage, scopes, facets) already exclude orphans, so
    they inflate nothing — warn, not fail. Tables a behind-schema DB lacks are
    skipped; the schema_version check owns that story.
    """
    counts: Dict[str, int] = {}
    for table in ORPHAN_TABLES:
        try:
            n = _count(
                conn,
                f"SELECT COUNT(*) FROM {table} x "
                "LEFT JOIN tracks t ON t.track_id = x.track_id "
                "WHERE t.track_id IS NULL",
            )
        except sqlite3.OperationalError:
            continue
        if n:
            counts[table] = n
    if not counts:
        return CheckResult("referential_orphans", "ok", "no orphan side-table rows")
    detail = ", ".join(f"{table}: {n}" for table, n in counts.items())
    return CheckResult(
        "referential_orphans",
        "warn",
        f"orphan rows (no tracks row) — {detail}",
        f'per table: sqlite3 {db_path} "DELETE FROM <table> '
        'WHERE track_id NOT IN (SELECT track_id FROM tracks)"',
    )


def check_embeddings(
    conn: sqlite3.Connection,
    expected_model: str = DEFAULT_EMBEDDING_MODEL,
    expected_dim: int = EXPECTED_EMBEDDING_DIM,
) -> CheckResult:
    """Embedding sanity: dimension, model identity, and zero-norm blobs.

    Zero-norm vectors poison cosine similarity silently (fail); a foreign
    model_name may be a deliberate SEARCH_EMBEDDING_MODEL override (warn).
    Blobs are decoded via storage/vectors.py, but only for the rows whose
    stored ``embedding_norm`` is already NULL/<= 0 — the writers all store the
    real norm, so a full 15k-row decode would buy nothing but latency.
    """
    try:
        total = _count(conn, "SELECT COUNT(*) FROM track_embeddings")
    except sqlite3.OperationalError:
        return CheckResult("embeddings", "warn", "track_embeddings table missing (schema behind)")
    if total == 0:
        return CheckResult("embeddings", "ok", "no embeddings stored yet")
    wrong_dim = _count(
        conn, f"SELECT COUNT(*) FROM track_embeddings WHERE embedding_dim != {int(expected_dim)}"
    )
    truncated = _count(
        conn,
        "SELECT COUNT(*) FROM track_embeddings WHERE length(embedding_blob) != embedding_dim * 4",
    )
    wrong_model = int(
        conn.execute(
            "SELECT COUNT(*) FROM track_embeddings WHERE model_name != ?", (expected_model,)
        ).fetchone()[0]
    )
    zero_norm = 0
    suspect_rows = conn.execute(
        "SELECT embedding_blob FROM track_embeddings "
        "WHERE embedding_norm IS NULL OR embedding_norm <= 0"
    ).fetchall()
    for suspect in suspect_rows:
        try:
            vec = decode_vector(suspect[0])
        except (ValueError, TypeError):
            continue  # undecodable blobs are already in the truncated count
        if not vec or vector_norm(vec) == 0.0:
            zero_norm += 1
    parts: List[str] = []
    if wrong_dim:
        parts.append(f"{wrong_dim} wrong-dim (expected {expected_dim})")
    if truncated:
        parts.append(f"{truncated} blob/dim mismatch")
    if zero_norm:
        parts.append(f"{zero_norm} zero-norm")
    if wrong_model:
        parts.append(f"{wrong_model} model-mismatch (expected {expected_model})")
    if not parts:
        return CheckResult("embeddings", "ok", f"{total} embeddings healthy ({expected_model})")
    remedy = "delete the flagged track_embeddings (+ matching track_context) rows, then /enrich"
    status = "fail" if (wrong_dim or truncated or zero_norm) else "warn"
    return CheckResult("embeddings", status, ", ".join(parts), remedy)


def check_sync_cursor(conn: sqlite3.Connection) -> CheckResult:
    """listen_events nonempty but the recently_played cursor missing/unparseable.

    Non-fatal by construction: sync_listen_history falls back to a cursor-less
    pull on a corrupt cursor — the risk is missed plays, not corruption.
    """
    events = _count(conn, "SELECT COUNT(*) FROM listen_events")
    if events == 0:
        return CheckResult("sync_cursor", "ok", "listen ledger empty — no cursor required")
    try:
        row = conn.execute(
            "SELECT cursor FROM sync_state WHERE source = 'recently_played';"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    cursor = row[0] if row is not None else None
    if row is None or cursor is None:
        return CheckResult(
            "sync_cursor",
            "warn",
            f"{events} listen event(s) but no recently_played cursor",
            "/listen-sync",
        )
    try:
        int(str(cursor))
    except ValueError:
        return CheckResult(
            "sync_cursor",
            "warn",
            f"recently_played cursor unparseable ({str(cursor)!r})",
            "/listen-sync",
        )
    return CheckResult("sync_cursor", "ok", f"{events} listen event(s), cursor at {cursor}")


def check_search_runs(conn: sqlite3.Connection, db_path: Path) -> CheckResult:
    """search_runs rows stuck in a non-terminal status (crash leftovers)."""
    placeholders = ", ".join(f"'{status}'" for status in TERMINAL_RUN_STATUSES)
    try:
        stuck = _count(
            conn,
            "SELECT COUNT(*) FROM search_runs "
            f"WHERE status IS NULL OR status NOT IN ({placeholders})",
        )
    except sqlite3.OperationalError:
        return CheckResult("search_runs", "warn", "search_runs table missing (schema behind)")
    if not stuck:
        return CheckResult("search_runs", "ok", "no search runs stuck in a non-terminal status")
    return CheckResult(
        "search_runs",
        "warn",
        f"{stuck} search run(s) stuck in a non-terminal status",
        f'sqlite3 {db_path} "DELETE FROM search_runs '
        f'WHERE status IS NULL OR status NOT IN ({placeholders})"',
    )


def check_backups(backups_dir: Path) -> CheckResult:
    """A restore path exists: backups/ present, readable, and nonempty."""
    if not backups_dir.exists():
        return CheckResult("backups", "warn", f"no backups directory at {backups_dir}", "/backup")
    try:
        entries = [p for p in backups_dir.iterdir() if not p.name.startswith(".")]
    except OSError as exc:
        return CheckResult("backups", "warn", f"backups directory unreadable: {exc}", "/backup")
    if not entries:
        return CheckResult("backups", "warn", "backups directory is empty", "/backup")
    return CheckResult("backups", "ok", f"{len(entries)} backup(s) in {backups_dir}")


def check_wal(db_path: Path, warn_bytes: int = WAL_WARN_BYTES) -> CheckResult:
    """An unexpectedly large -wal sidecar (checkpoints not landing)."""
    wal_path = Path(f"{db_path}-wal")
    if not wal_path.exists():
        return CheckResult("wal", "ok", "no WAL sidecar")
    size = wal_path.stat().st_size
    if size <= warn_bytes:
        return CheckResult("wal", "ok", f"WAL sidecar {_fmt_mb(size)}")
    return CheckResult(
        "wal",
        "warn",
        f"WAL sidecar is {_fmt_mb(size)} (> {_fmt_mb(warn_bytes)})",
        f'close other tunr processes, then: sqlite3 {db_path} "PRAGMA wal_checkpoint(TRUNCATE);"',
    )


def default_backups_dir() -> Path:
    """`<repo_root>/backups` — the same resolution main.backup_data uses."""
    return Path(__file__).resolve().parent.parent / "backups"


def run_checks(
    conn: sqlite3.Connection,
    db_path: Path,
    backups_dir: Path,
    *,
    expected_model: str = DEFAULT_EMBEDDING_MODEL,
) -> List[CheckResult]:
    """Run the fixed check list, in its fixed order."""
    return [
        check_sqlite_integrity(conn, db_path),
        check_schema_version(conn),
        check_referential_orphans(conn, db_path),
        check_embeddings(conn, expected_model=expected_model),
        check_sync_cursor(conn),
        check_search_runs(conn, db_path),
        check_backups(backups_dir),
        check_wal(db_path),
    ]


def has_failures(results: List[CheckResult]) -> bool:
    return any(result.status == "fail" for result in results)


def results_payload(results: List[CheckResult]) -> Dict[str, Any]:
    """The `--json` shape: the rows verbatim plus verdict counts."""
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return {
        "checks": [asdict(result) for result in results],
        "counts": counts,
        "healthy": counts["fail"] == 0,
    }
