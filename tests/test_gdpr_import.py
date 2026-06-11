"""Tests for the GDPR extended-streaming-history import (src/gdpr_import.py).

Covers the three input shapes (zip / extracted folder / single json), the
record-level skip rules (episodes, missing metadata), telemetry storage
(ms_played, skipped), the uuid5 dedup contract (in-file duplicates collapse;
polled-then-imported events are enriched, not duplicated), dry-run, the
friendly wrong-export error, and the /import-history command handler.
Offline: everything runs against a tmp_path SQLite database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import ui
from gdpr_import import (
    GdprImportError,
    import_streaming_history,
    iter_streaming_records,
)
from main import PlaylistCLI, dispatch_command
from plays import play_counts
from storage.db import Database
from storage.migrations import ensure_schema
from storage.repos import Repositories


@pytest.fixture(autouse=True)
def _reset_ui_modes():
    ui.set_output_sink(None)
    ui.set_json_mode(False)
    yield
    ui.set_output_sink(None)
    ui.set_json_mode(False)


# ---------------------------------------------------------------------------
# Fixture data: two export files, 7 records.
#   file 1: a full play, its exact duplicate, a sub-30s skipped play, a podcast
#   file 2: a missing-metadata row, a second full play, the "polled" play


def _play(
    ts: str,
    ms: int,
    track: Optional[str],
    artist: Optional[str],
    album: Optional[str],
    uri: Optional[str],
    skipped: bool = False,
) -> Dict[str, Any]:
    return {
        "ts": ts,
        "platform": "ios",
        "ms_played": ms,
        "conn_country": "US",
        "master_metadata_track_name": track,
        "master_metadata_album_artist_name": artist,
        "master_metadata_album_album_name": album,
        "spotify_track_uri": uri,
        "episode_name": None,
        "episode_show_name": None,
        "spotify_episode_uri": None,
        "reason_start": "trackdone",
        "reason_end": "trackdone",
        "shuffle": False,
        "skipped": skipped,
        "offline": False,
        "incognito_mode": False,
    }


KOLA = _play(
    "2023-01-05T20:01:25Z",
    215000,
    "Kola",
    "Hermanos Gutiérrez",
    "El Bueno Y El Malo",
    "spotify:track:kola111",
)
SKIP = _play(
    "2023-01-05T20:02:00Z",
    8000,
    "Skipped Song",
    "Artist B",
    "Album B",
    "spotify:track:skip222",
    skipped=True,
)
EPISODE = {
    "ts": "2023-01-06T08:00:00Z",
    "platform": "ios",
    "ms_played": 1200000,
    "conn_country": "US",
    "master_metadata_track_name": None,
    "master_metadata_album_artist_name": None,
    "master_metadata_album_album_name": None,
    "spotify_track_uri": None,
    "episode_name": "Some Pod Episode",
    "episode_show_name": "Some Pod",
    "spotify_episode_uri": "spotify:episode:ep111",
    "reason_start": "trackdone",
    "reason_end": "trackdone",
    "shuffle": False,
    "skipped": False,
    "offline": False,
    "incognito_mode": False,
}
MISSING = _play("2023-01-07T12:00:00Z", 60000, None, None, None, "spotify:track:miss333")
SONG_C = _play(
    "2023-02-01T10:00:00Z", 180000, "Song C", "Artist C", "Album C", "spotify:track:ccc444"
)
POLLED = _play(
    "2023-03-01T09:30:00Z", 45000, "Polled Song", "Artist D", "Album D", "spotify:track:ddd555"
)

FILE1_NAME = "Streaming_History_Audio_2022_0.json"
FILE2_NAME = "Streaming_History_Audio_2023_1.json"
FILE1 = [KOLA, dict(KOLA), SKIP, EPISODE]  # dict(KOLA): exact duplicate event
FILE2 = [MISSING, SONG_C, POLLED]

EXPECTED_SUMMARY = {
    "files": 2,
    "records_total": 7,
    "episodes_skipped": 1,
    "missing_metadata": 1,
    "imported": 5,
    "dry_run": False,
}


def _event_id(spotify_id: str, played_at: str) -> str:
    """The shared recipe (identical to the recently_played polling path)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{spotify_id}|{played_at}").hex


def _write_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "my_spotify_data.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # Real exports nest the files in a folder inside the zip.
        zf.writestr(f"Spotify Extended Streaming History/{FILE1_NAME}", json.dumps(FILE1))
        zf.writestr(f"Spotify Extended Streaming History/{FILE2_NAME}", json.dumps(FILE2))
    return zip_path


def _write_dir(tmp_path: Path) -> Path:
    root = tmp_path / "export"
    inner = root / "Spotify Extended Streaming History"
    inner.mkdir(parents=True)
    (inner / FILE1_NAME).write_text(json.dumps(FILE1), encoding="utf-8")
    (inner / FILE2_NAME).write_text(json.dumps(FILE2), encoding="utf-8")
    return root


def _connect(tmp_path: Path) -> sqlite3.Connection:
    db = Database(tmp_path / "test.db")
    conn = db.connect()
    ensure_schema(conn)
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table};").fetchone()["n"])


# ---------------------------------------------------------------------------
# iter_streaming_records


class TestIterStreamingRecords:
    def test_zip_yields_all_records_sorted_by_filename(self, tmp_path):
        records = list(iter_streaming_records(_write_zip(tmp_path)))
        assert len(records) == 7
        assert [name for name, _ in records] == [FILE1_NAME] * 4 + [FILE2_NAME] * 3
        assert records[0][1] == KOLA
        assert records[-1][1] == POLLED

    def test_directory_yields_same_records_as_zip(self, tmp_path):
        from_zip = list(iter_streaming_records(_write_zip(tmp_path)))
        from_dir = list(iter_streaming_records(_write_dir(tmp_path)))
        assert from_dir == from_zip

    def test_single_json_file(self, tmp_path):
        file_path = tmp_path / FILE2_NAME
        file_path.write_text(json.dumps(FILE2), encoding="utf-8")
        records = list(iter_streaming_records(file_path))
        assert [name for name, _ in records] == [FILE2_NAME] * 3

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(GdprImportError, match="not found"):
            list(iter_streaming_records(tmp_path / "nope.zip"))

    def test_basic_account_export_zip_raises_friendly_error(self, tmp_path):
        # The basic Account-data export ships StreamingHistory0.json — a
        # different shape with no track URIs. Point the user at the extended one.
        zip_path = tmp_path / "basic.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("MyData/StreamingHistory0.json", json.dumps([]))
        with pytest.raises(GdprImportError, match="EXTENDED"):
            list(iter_streaming_records(zip_path))

    def test_directory_without_audio_files_raises_friendly_error(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "Userdata.json").write_text("{}", encoding="utf-8")
        with pytest.raises(GdprImportError, match="EXTENDED"):
            list(iter_streaming_records(empty))

    def test_wrong_single_file_raises_friendly_error(self, tmp_path):
        file_path = tmp_path / "StreamingHistory0.json"
        file_path.write_text(json.dumps([]), encoding="utf-8")
        with pytest.raises(GdprImportError, match="EXTENDED"):
            list(iter_streaming_records(file_path))

    def test_non_array_file_raises(self, tmp_path):
        file_path = tmp_path / FILE1_NAME
        file_path.write_text(json.dumps({"oops": True}), encoding="utf-8")
        with pytest.raises(GdprImportError, match="JSON array"):
            list(iter_streaming_records(file_path))


# ---------------------------------------------------------------------------
# import_streaming_history


class TestImportStreamingHistory:
    def test_full_import_summary_and_rows(self, tmp_path):
        conn = _connect(tmp_path)
        repos = Repositories(conn)

        summary = import_streaming_history(repos, iter_streaming_records(_write_zip(tmp_path)))

        assert summary == EXPECTED_SUMMARY
        # 5 imported records collapse to 4 rows: the exact duplicate dedupes.
        assert _count(conn, "listen_events") == 4

        kola = _rows(
            conn,
            "SELECT * FROM listen_events WHERE event_id = ?;",
            (_event_id("kola111", "2023-01-05T20:01:25Z"),),
        )
        assert len(kola) == 1
        assert kola[0]["track_id"] == "hermanos gutiérrez|||kola"
        assert kola[0]["spotify_id"] == "kola111"  # bare base62 id on the event
        assert kola[0]["played_at"] == "2023-01-05T20:01:25Z"  # ts verbatim
        assert kola[0]["source"] == "gdpr_export"
        assert kola[0]["ms_played"] == 215000
        assert kola[0]["skipped"] == 0
        assert kola[0]["context_uri"] is None

        skip = _rows(
            conn,
            "SELECT * FROM listen_events WHERE event_id = ?;",
            (_event_id("skip222", "2023-01-05T20:02:00Z"),),
        )
        assert skip[0]["ms_played"] == 8000
        assert skip[0]["skipped"] == 1

        # No episode/missing-metadata rows leaked into the ledger.
        assert _rows(conn, "SELECT * FROM listen_events WHERE spotify_id LIKE 'ep%';") == []
        assert _rows(conn, "SELECT * FROM listen_events WHERE spotify_id = 'miss333';") == []

    def test_artist_and_track_rows_created_fk_safe(self, tmp_path):
        conn = _connect(tmp_path)
        repos = Repositories(conn)
        import_streaming_history(repos, iter_streaming_records(_write_zip(tmp_path)))

        track = repos.tracks.get("artist c|||song c")
        assert track is not None
        assert track["artist_id"] == "artist c"
        assert track["name"] == "Song C"
        assert track["album_name"] == "Album C"
        assert track["spotify_id"] == "spotify:track:ccc444"  # tracks keep the full URI
        artist = repos.artists.get("artist c")
        assert artist is not None and artist["name"] == "Artist C"
        assert _count(conn, "tracks") == 4
        assert _count(conn, "artists") == 4

    def test_sub_30s_play_stored_but_excluded_by_play_counts(self, tmp_path):
        conn = _connect(tmp_path)
        import_streaming_history(Repositories(conn), iter_streaming_records(_write_zip(tmp_path)))
        counts = play_counts(conn)
        assert "artist b|||skipped song" not in counts  # 8s < 30s rule
        assert counts["hermanos gutiérrez|||kola"] == 1

    def test_reimport_is_idempotent(self, tmp_path):
        conn = _connect(tmp_path)
        repos = Repositories(conn)
        zip_path = _write_zip(tmp_path)
        first = import_streaming_history(repos, iter_streaming_records(zip_path))
        second = import_streaming_history(repos, iter_streaming_records(zip_path))
        assert first == second == EXPECTED_SUMMARY
        assert _count(conn, "listen_events") == 4

    def test_dry_run_counts_without_writing(self, tmp_path):
        conn = _connect(tmp_path)
        summary = import_streaming_history(
            Repositories(conn), iter_streaming_records(_write_zip(tmp_path)), dry_run=True
        )
        assert summary == {**EXPECTED_SUMMARY, "dry_run": True}
        assert _count(conn, "listen_events") == 0
        assert _count(conn, "tracks") == 0
        assert _count(conn, "artists") == 0

    def test_polled_event_gets_enriched_not_duplicated(self, tmp_path):
        conn = _connect(tmp_path)
        repos = Repositories(conn)
        # Seed what the recently_played polling path would have written: a bare
        # event (no ms_played/skipped) under the same deterministic event_id.
        repos.artists.upsert(artist_id="artist d", name="Artist D")
        repos.tracks.upsert(
            {
                "track_id": "artist d|||polled song",
                "name": "Polled Song",
                "artist_id": "artist d",
                "created_at": "2023-03-01T09:31:00Z",
                "updated_at": "2023-03-01T09:31:00Z",
            }
        )
        event_id = _event_id("ddd555", "2023-03-01T09:30:00Z")
        repos.listen_events.upsert(
            {
                "event_id": event_id,
                "track_id": "artist d|||polled song",
                "spotify_id": "ddd555",
                "played_at": "2023-03-01T09:30:00Z",
                "source": "recently_played",
                "created_at": "2023-03-01T09:31:00Z",
            }
        )
        conn.commit()

        import_streaming_history(repos, iter_streaming_records(_write_zip(tmp_path)))

        rows = _rows(conn, "SELECT * FROM listen_events WHERE event_id = ?;", (event_id,))
        assert len(rows) == 1  # enriched in place, not duplicated
        assert rows[0]["ms_played"] == 45000
        assert rows[0]["skipped"] == 0
        assert _count(conn, "listen_events") == 4

    def test_existing_track_metadata_is_not_clobbered(self, tmp_path):
        conn = _connect(tmp_path)
        repos = Repositories(conn)
        repos.artists.upsert(
            artist_id="hermanos gutiérrez", name="Hermanos Gutiérrez", popularity=61
        )
        repos.tracks.upsert(
            {
                "track_id": "hermanos gutiérrez|||kola",
                "spotify_id": "spotify:track:kola111",
                "name": "Kola",
                "artist_id": "hermanos gutiérrez",
                "album_name": "El Bueno Y El Malo",
                "popularity": 55,
                "status": "accepted",
                "created_at": "2023-01-01T00:00:00Z",
                "updated_at": "2023-01-01T00:00:00Z",
            }
        )
        conn.commit()

        import_streaming_history(repos, iter_streaming_records(_write_zip(tmp_path)))

        track = repos.tracks.get("hermanos gutiérrez|||kola")
        assert track is not None
        assert track["popularity"] == 55
        assert track["status"] == "accepted"
        artist = repos.artists.get("hermanos gutiérrez")
        assert artist is not None and artist["popularity"] == 61

    def test_on_progress_called_per_record(self, tmp_path):
        conn = _connect(tmp_path)
        calls: List[tuple] = []
        import_streaming_history(
            Repositories(conn),
            iter_streaming_records(_write_zip(tmp_path)),
            on_progress=lambda seen, imported: calls.append((seen, imported)),
        )
        assert len(calls) == 7
        assert calls[-1] == (7, 5)

    def test_commit_every_one_still_collapses_duplicates(self, tmp_path):
        conn = _connect(tmp_path)
        summary = import_streaming_history(
            Repositories(conn), iter_streaming_records(_write_zip(tmp_path)), commit_every=1
        )
        assert summary == EXPECTED_SUMMARY
        assert _count(conn, "listen_events") == 4


# ---------------------------------------------------------------------------
# /import-history command handler


def _cli(tmp_path: Path) -> PlaylistCLI:
    conn = _connect(tmp_path)
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    return cli


def _args(**kwargs):
    defaults = {"dry_run": False, "json": False}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestImportHistoryCommand:
    def test_command_imports_and_renders_summary(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        rc = dispatch_command(cli, "import-history", _args(path=str(_write_zip(tmp_path))))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Imported 5 plays from 2 file(s)" in out
        assert "30s rule" in out  # honesty caption
        assert "exact timestamps" in out  # dedup caption
        assert _count(cli.repos.conn, "listen_events") == 4

    def test_command_json_payload(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        rc = dispatch_command(
            cli, "import-history", _args(path=str(_write_zip(tmp_path)), json=True)
        )
        assert rc == 0
        assert json.loads(capsys.readouterr().out) == EXPECTED_SUMMARY

    def test_command_dry_run_writes_nothing(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        rc = dispatch_command(
            cli, "import-history", _args(path=str(_write_zip(tmp_path)), dry_run=True)
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Would import 5 plays" in out
        assert _count(cli.repos.conn, "listen_events") == 0

    def test_command_missing_path_fails_friendly(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        rc = dispatch_command(cli, "import-history", _args(path=str(tmp_path / "missing.zip")))
        assert rc == 1
        assert "not found" in capsys.readouterr().out

    def test_command_wrong_export_fails_friendly(self, tmp_path, capsys):
        zip_path = tmp_path / "basic.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("MyData/StreamingHistory0.json", json.dumps([]))
        cli = _cli(tmp_path)
        rc = dispatch_command(cli, "import-history", _args(path=str(zip_path)))
        assert rc == 1
        assert "EXTENDED" in capsys.readouterr().out

    def test_command_json_error_payload(self, tmp_path, capsys):
        cli = _cli(tmp_path)
        rc = dispatch_command(
            cli, "import-history", _args(path=str(tmp_path / "missing.zip"), json=True)
        )
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert "not found" in payload["error"]


# ---------------------------------------------------------------------------
# argparse wiring


class TestArgParse:
    def test_parse_tokens_import_history(self):
        from arg_parse import parse_tokens

        command, args, err = parse_tokens(["import-history", "export.zip", "--dry-run", "--json"])
        assert err is None
        assert command == "import-history"
        assert args.path == "export.zip"
        assert args.dry_run is True
        assert args.json is True

    def test_parse_tokens_defaults(self):
        from arg_parse import parse_tokens

        command, args, err = parse_tokens(["import-history", "export.zip"])
        assert err is None
        assert args.dry_run is False
        assert args.json is False
