"""Contract tests for /doctor — the offline integrity and consistency audit.

Each check gets a deliberately broken tmp database (orphans planted with
PRAGMA foreign_keys=OFF, wrong version rows, zero-norm blobs) and its verdict
is asserted, plus the dispatch exit code and the --json payload shape.
Offline; no Spotify, no network, no embedding model.
"""

from __future__ import annotations

import argparse
import json

import pytest

import doctor as doctor_module
import ui
from doctor import (
    CheckResult,
    check_backups,
    check_embeddings,
    check_referential_orphans,
    check_schema_version,
    check_search_runs,
    check_sqlite_integrity,
    check_sync_cursor,
    check_wal,
    has_failures,
    results_payload,
    run_checks,
)
from main import PlaylistCLI, dispatch_command
from storage.db import Database
from storage.migrations import LATEST_VERSION, ensure_schema
from storage.repos import Repositories
from storage.vectors import encode_vector, vector_norm

CHECK_NAMES = [
    "sqlite_integrity",
    "schema_version",
    "referential_orphans",
    "embeddings",
    "sync_cursor",
    "search_runs",
    "backups",
    "wal",
]


@pytest.fixture(autouse=True)
def _reset_modes():
    ui.set_output_sink(None)
    ui.set_json_mode(False)
    yield
    ui.set_output_sink(None)
    ui.set_json_mode(False)


def _fresh(tmp_path, name: str = "tunr.db"):
    db = Database(tmp_path / name)
    conn = db.connect()
    ensure_schema(conn)
    return db, conn


def _cli_over(db, conn) -> PlaylistCLI:
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._storage = db  # pre-set: the lazy property must NOT re-run ensure_schema
    cli._repos = Repositories(conn)
    cli._db = None
    cli._spotify = object()
    cli._rotation_managers = {}
    return cli


def _args(**overrides):
    base = {"json": False}
    base.update(overrides)
    return argparse.Namespace(**base)


def _seed_track(conn, track_id: str = "a|||t0"):
    conn.execute("INSERT OR IGNORE INTO artists (artist_id, name) VALUES ('a', 'Artist')")
    conn.execute(
        "INSERT INTO tracks (track_id, name, artist_id, status) VALUES (?, ?, 'a', 'candidate')",
        (track_id, track_id.split("|||")[-1]),
    )
    conn.commit()


def _plant_orphan(conn, table: str, sql: str, params=()):
    """INSERT a row that violates the tracks FK (constraint toggled off)."""
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(sql, params)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


def _insert_embedding(conn, track_id: str, vec, *, dim=None, model="all-mpnet-base-v2", norm=None):
    conn.execute(
        "INSERT INTO track_embeddings "
        "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            track_id,
            model,
            encode_vector(vec),
            dim if dim is not None else len(vec),
            norm if norm is not None else vector_norm(vec),
        ),
    )
    conn.commit()


class TestSqliteIntegrity:
    def test_healthy_db_is_ok(self, tmp_path):
        db, conn = _fresh(tmp_path)
        result = check_sqlite_integrity(conn, db.path)
        assert result.status == "ok"

    def test_fk_violation_fails_and_names_the_table(self, tmp_path):
        db, conn = _fresh(tmp_path)
        _plant_orphan(
            conn,
            "track_context",
            "INSERT INTO track_context (track_id) VALUES ('ghost|||gone')",
        )
        result = check_sqlite_integrity(conn, db.path)
        assert result.status == "fail"
        assert "foreign-key violation" in result.detail
        assert "track_context" in result.detail
        assert "/restore" in result.remedy


class TestSchemaVersion:
    def test_latest_version_is_ok(self, tmp_path):
        _, conn = _fresh(tmp_path)
        result = check_schema_version(conn)
        assert result.status == "ok"
        assert f"v{LATEST_VERSION}" in result.detail

    def test_behind_version_warns_with_migration_remedy(self, tmp_path):
        _, conn = _fresh(tmp_path)
        conn.execute("UPDATE schema_version SET version = 3")
        conn.commit()
        result = check_schema_version(conn)
        assert result.status == "warn"
        assert "v3" in result.detail
        assert "restart tunr" in result.remedy

    def test_missing_version_row_warns(self, tmp_path):
        _, conn = _fresh(tmp_path)
        conn.execute("DELETE FROM schema_version")
        conn.commit()
        result = check_schema_version(conn)
        assert result.status == "warn"
        assert "missing" in result.detail

    def test_future_version_fails(self, tmp_path):
        _, conn = _fresh(tmp_path)
        conn.execute("UPDATE schema_version SET version = 99")
        conn.commit()
        result = check_schema_version(conn)
        assert result.status == "fail"
        assert "v99" in result.detail


class TestReferentialOrphans:
    def test_clean_db_is_ok(self, tmp_path):
        db, conn = _fresh(tmp_path)
        result = check_referential_orphans(conn, db.path)
        assert result.status == "ok"

    def test_orphans_warn_with_per_table_counts(self, tmp_path):
        db, conn = _fresh(tmp_path)
        _plant_orphan(
            conn,
            "track_context",
            "INSERT INTO track_context (track_id) VALUES ('ghost|||gone')",
        )
        _plant_orphan(
            conn,
            "listen_events",
            "INSERT INTO listen_events (event_id, track_id) VALUES ('e1', 'ghost|||gone')",
        )
        _plant_orphan(
            conn,
            "listen_events",
            "INSERT INTO listen_events (event_id, track_id) VALUES ('e2', 'ghost|||gone')",
        )
        result = check_referential_orphans(conn, db.path)
        assert result.status == "warn"
        assert "track_context: 1" in result.detail
        assert "listen_events: 2" in result.detail
        assert "DELETE FROM" in result.remedy


class TestEmbeddings:
    def test_no_embeddings_is_ok(self, tmp_path):
        _, conn = _fresh(tmp_path)
        result = check_embeddings(conn)
        assert result.status == "ok"

    def test_healthy_embedding_is_ok(self, tmp_path):
        _, conn = _fresh(tmp_path)
        _seed_track(conn)
        _insert_embedding(conn, "a|||t0", [0.1] * 768)
        result = check_embeddings(conn)
        assert result.status == "ok"
        assert "1 embeddings healthy" in result.detail

    def test_zero_norm_blob_fails(self, tmp_path):
        _, conn = _fresh(tmp_path)
        _seed_track(conn)
        _insert_embedding(conn, "a|||t0", [0.0] * 768, norm=0.0)
        result = check_embeddings(conn)
        assert result.status == "fail"
        assert "1 zero-norm" in result.detail
        assert "/enrich" in result.remedy

    def test_null_norm_healthy_blob_is_decoded_and_passes(self, tmp_path):
        """A NULL stored norm forces the decode path — a healthy blob survives."""
        _, conn = _fresh(tmp_path)
        _seed_track(conn)
        conn.execute(
            "INSERT INTO track_embeddings "
            "(track_id, model_name, embedding_blob, embedding_dim, embedding_norm) "
            "VALUES ('a|||t0', 'all-mpnet-base-v2', ?, 768, NULL)",
            (encode_vector([0.1] * 768),),
        )
        conn.commit()
        result = check_embeddings(conn)
        assert result.status == "ok"

    def test_wrong_dim_fails(self, tmp_path):
        _, conn = _fresh(tmp_path)
        _seed_track(conn)
        _insert_embedding(conn, "a|||t0", [0.1] * 384)
        result = check_embeddings(conn)
        assert result.status == "fail"
        assert "wrong-dim (expected 768)" in result.detail

    def test_truncated_blob_fails(self, tmp_path):
        _, conn = _fresh(tmp_path)
        _seed_track(conn)
        _insert_embedding(conn, "a|||t0", [0.1] * 4, dim=768)
        result = check_embeddings(conn)
        assert result.status == "fail"
        assert "blob/dim mismatch" in result.detail

    def test_foreign_model_alone_warns(self, tmp_path):
        _, conn = _fresh(tmp_path)
        _seed_track(conn)
        _insert_embedding(conn, "a|||t0", [0.1] * 768, model="all-MiniLM-L6-v2")
        result = check_embeddings(conn)
        assert result.status == "warn"
        assert "model-mismatch (expected all-mpnet-base-v2)" in result.detail

    def test_expected_model_override(self, tmp_path):
        _, conn = _fresh(tmp_path)
        _seed_track(conn)
        _insert_embedding(conn, "a|||t0", [0.1] * 768, model="custom-model")
        result = check_embeddings(conn, expected_model="custom-model")
        assert result.status == "ok"


class TestSyncCursor:
    def _seed_event(self, conn):
        _seed_track(conn)
        conn.execute(
            "INSERT INTO listen_events (event_id, track_id, played_at, source) "
            "VALUES ('e1', 'a|||t0', '2026-07-01T00:00:00Z', 'recently_played')"
        )
        conn.commit()

    def test_empty_ledger_is_ok(self, tmp_path):
        _, conn = _fresh(tmp_path)
        result = check_sync_cursor(conn)
        assert result.status == "ok"

    def test_events_without_cursor_warns(self, tmp_path):
        _, conn = _fresh(tmp_path)
        self._seed_event(conn)
        result = check_sync_cursor(conn)
        assert result.status == "warn"
        assert result.remedy == "/listen-sync"

    def test_unparseable_cursor_warns(self, tmp_path):
        _, conn = _fresh(tmp_path)
        self._seed_event(conn)
        conn.execute(
            "INSERT INTO sync_state (source, cursor) VALUES ('recently_played', 'not-a-number')"
        )
        conn.commit()
        result = check_sync_cursor(conn)
        assert result.status == "warn"
        assert "unparseable" in result.detail

    def test_integer_cursor_is_ok(self, tmp_path):
        _, conn = _fresh(tmp_path)
        self._seed_event(conn)
        conn.execute(
            "INSERT INTO sync_state (source, cursor) VALUES ('recently_played', '1710000000000')"
        )
        conn.commit()
        result = check_sync_cursor(conn)
        assert result.status == "ok"


class TestSearchRuns:
    def _seed_run(self, conn, run_id: str, status):
        conn.execute("INSERT OR IGNORE INTO queries (query_hash, query_text) VALUES ('q1', 'jazz')")
        conn.execute(
            "INSERT INTO search_runs (run_id, query_hash, provider, status) "
            "VALUES (?, 'q1', 'combined', ?)",
            (run_id, status),
        )
        conn.commit()

    def test_no_runs_is_ok(self, tmp_path):
        db, conn = _fresh(tmp_path)
        result = check_search_runs(conn, db.path)
        assert result.status == "ok"

    def test_terminal_statuses_are_ok(self, tmp_path):
        db, conn = _fresh(tmp_path)
        self._seed_run(conn, "r1", "ok")
        self._seed_run(conn, "r2", "error")
        result = check_search_runs(conn, db.path)
        assert result.status == "ok"

    def test_stuck_runs_warn(self, tmp_path):
        db, conn = _fresh(tmp_path)
        self._seed_run(conn, "r1", "running")
        self._seed_run(conn, "r2", None)
        result = check_search_runs(conn, db.path)
        assert result.status == "warn"
        assert "2 search run(s)" in result.detail
        assert "DELETE FROM search_runs" in result.remedy


class TestBackups:
    def test_missing_dir_warns(self, tmp_path):
        result = check_backups(tmp_path / "backups")
        assert result.status == "warn"
        assert result.remedy == "/backup"

    def test_empty_dir_warns(self, tmp_path):
        backups = tmp_path / "backups"
        backups.mkdir()
        result = check_backups(backups)
        assert result.status == "warn"
        assert "empty" in result.detail

    def test_dotfiles_do_not_count_as_backups(self, tmp_path):
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / ".DS_Store").write_bytes(b"")
        result = check_backups(backups)
        assert result.status == "warn"

    def test_populated_dir_is_ok(self, tmp_path):
        backups = tmp_path / "backups"
        (backups / "20260101_000000").mkdir(parents=True)
        result = check_backups(backups)
        assert result.status == "ok"
        assert "1 backup(s)" in result.detail


class TestWal:
    def test_no_sidecar_is_ok(self, tmp_path):
        result = check_wal(tmp_path / "tunr.db")
        assert result.status == "ok"
        assert "no WAL sidecar" in result.detail

    def test_small_sidecar_is_ok(self, tmp_path):
        db_path = tmp_path / "tunr.db"
        (tmp_path / "tunr.db-wal").write_bytes(b"x" * 512)
        result = check_wal(db_path)
        assert result.status == "ok"

    def test_oversized_sidecar_warns(self, tmp_path):
        db_path = tmp_path / "tunr.db"
        (tmp_path / "tunr.db-wal").write_bytes(b"x" * 2048)
        result = check_wal(db_path, warn_bytes=1024)
        assert result.status == "warn"
        assert "wal_checkpoint(TRUNCATE)" in result.remedy


class TestRunChecks:
    def test_fixed_check_list_in_fixed_order(self, tmp_path):
        db, conn = _fresh(tmp_path)
        backups = tmp_path / "backups"
        (backups / "b1").mkdir(parents=True)
        results = run_checks(conn, db.path, backups)
        assert [r.name for r in results] == CHECK_NAMES
        assert all(r.status == "ok" for r in results)
        assert not has_failures(results)

    def test_has_failures_and_payload_counts(self):
        results = [
            CheckResult("a", "ok", "fine"),
            CheckResult("b", "warn", "meh", "/fix"),
            CheckResult("c", "fail", "bad", "/restore"),
        ]
        assert has_failures(results)
        payload = results_payload(results)
        assert payload["counts"] == {"ok": 1, "warn": 1, "fail": 1}
        assert payload["healthy"] is False
        assert payload["checks"][2] == {
            "name": "c",
            "status": "fail",
            "detail": "bad",
            "remedy": "/restore",
        }


def _seed_broken(conn):
    """One DB carrying every planted defect the audit must catch."""
    _seed_track(conn)
    # Zero-norm embedding (embedding sanity -> fail).
    _insert_embedding(conn, "a|||t0", [0.0] * 768, norm=0.0)
    # Orphan side-table row (orphans -> warn; also an FK violation -> fail).
    _plant_orphan(
        conn, "track_context", "INSERT INTO track_context (track_id) VALUES ('ghost|||gone')"
    )
    # Listen event without a recently_played cursor (sync cursor -> warn).
    conn.execute(
        "INSERT INTO listen_events (event_id, track_id, played_at, source) "
        "VALUES ('e1', 'a|||t0', '2026-07-01T00:00:00Z', 'recently_played')"
    )
    # Search run stuck in a non-terminal status (search runs -> warn).
    conn.execute("INSERT INTO queries (query_hash, query_text) VALUES ('q1', 'jazz')")
    conn.execute(
        "INSERT INTO search_runs (run_id, query_hash, provider, status) "
        "VALUES ('r1', 'q1', 'combined', 'running')"
    )
    conn.commit()


class TestDoctorHandler:
    """dispatch_command exit codes, the rendered table, and the --json shape."""

    @pytest.fixture(autouse=True)
    def _sandbox_backups(self, tmp_path, monkeypatch):
        # main._handle_doctor resolves <repo_root>/backups; tests must never
        # read the real state dir. The handler calls doctor.default_backups_dir
        # qualified, so patching the owning module intercepts it durably.
        self.backups_dir = tmp_path / "backups"
        monkeypatch.setattr(doctor_module, "default_backups_dir", lambda: self.backups_dir)
        monkeypatch.delenv("SEARCH_EMBEDDING_MODEL", raising=False)

    def test_healthy_db_exits_zero_and_renders_table(self, tmp_path, capsys):
        db, conn = _fresh(tmp_path)
        (self.backups_dir / "b1").mkdir(parents=True)
        cli = _cli_over(db, conn)
        rc = dispatch_command(cli, "doctor", _args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Doctor" in out
        assert "sqlite_integrity" in out
        assert "All checks passed." in out

    def test_broken_db_exits_one(self, tmp_path, capsys):
        db, conn = _fresh(tmp_path)
        _seed_broken(conn)
        cli = _cli_over(db, conn)
        rc = dispatch_command(cli, "doctor", _args())
        assert rc == 1
        out = capsys.readouterr().out
        assert "zero-norm" in out

    def test_json_payload_shape_and_verdicts(self, tmp_path, capsys):
        db, conn = _fresh(tmp_path)
        _seed_broken(conn)
        cli = _cli_over(db, conn)
        rc = dispatch_command(cli, "doctor", _args(json=True))
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["db_path"] == str(db.path)
        assert payload["healthy"] is False
        assert set(payload["counts"]) == {"ok", "warn", "fail"}
        checks = {c["name"]: c for c in payload["checks"]}
        assert list(checks) == CHECK_NAMES
        assert checks["sqlite_integrity"]["status"] == "fail"
        assert checks["embeddings"]["status"] == "fail"
        assert checks["referential_orphans"]["status"] == "warn"
        assert checks["sync_cursor"]["status"] == "warn"
        assert checks["search_runs"]["status"] == "warn"
        assert checks["backups"]["status"] == "warn"
        assert checks["schema_version"]["status"] == "ok"
        for check in payload["checks"]:
            assert set(check) == {"name", "status", "detail", "remedy"}

    def test_json_healthy_payload(self, tmp_path, capsys):
        db, conn = _fresh(tmp_path)
        (self.backups_dir / "b1").mkdir(parents=True)
        cli = _cli_over(db, conn)
        rc = dispatch_command(cli, "doctor", _args(json=True))
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)  # stdout is pure JSON — tables silenced
        assert payload["healthy"] is True
        assert payload["counts"]["fail"] == 0

    def test_search_model_env_override_respected(self, tmp_path, monkeypatch, capsys):
        """SEARCH_EMBEDDING_MODEL is the canonical name the audit compares to."""
        db, conn = _fresh(tmp_path)
        (self.backups_dir / "b1").mkdir(parents=True)
        _seed_track(conn)
        _insert_embedding(conn, "a|||t0", [0.1] * 768, model="custom-model")
        monkeypatch.setenv("SEARCH_EMBEDDING_MODEL", "custom-model")
        cli = _cli_over(db, conn)
        rc = dispatch_command(cli, "doctor", _args(json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        checks = {c["name"]: c for c in payload["checks"]}
        assert checks["embeddings"]["status"] == "ok"
