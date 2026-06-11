"""Tests for the cursor-based /listen-sync upgrade.

Covers: the persisted sync_state cursor is replayed as ``after=`` on the next
call, the response's cursors.after is stored, an empty page with no cursors
leaves the cursor untouched (but still bumps last_synced_at), context_uri is
captured per event, event upserts are idempotent, and quiet mode emits at most
one dim caption line. All offline against a temp SQLite DB and a fake spotipy
client.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

import pytest

import ui
from main import PlaylistCLI
from storage.migrations import ensure_schema
from storage.repos import Repositories


def _spotify_track(name: str, artist: str) -> Dict[str, Any]:
    uri = f"spotify:track:{(artist + name).lower().replace(' ', '')[:22]}"
    return {
        "name": name,
        "uri": uri,
        "artists": [{"name": artist}],
        "album": {"name": f"{name} Album", "release_date": "2025-01-01"},
        "duration_ms": 180000,
        "explicit": False,
        "popularity": 42,
        "external_urls": {"spotify": f"https://open.spotify.com/track/{uri}"},
    }


def _item(name: str, artist: str, played_at: str, context_uri: Optional[str] = None):
    item: Dict[str, Any] = {"track": _spotify_track(name, artist), "played_at": played_at}
    if context_uri:
        item["context"] = {"uri": context_uri}
    return item


class FakeSp:
    """Recording fake for the spotipy client's recently-played endpoint."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def current_user_recently_played(self, limit: int = 50, **kwargs: Any) -> Dict[str, Any]:
        call = {"limit": limit}
        call.update(kwargs)
        self.calls.append(call)
        return self.responses.pop(0)


class FakeSpotify:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.sp = FakeSp(responses)


def _make_cli(responses: List[Dict[str, Any]]) -> PlaylistCLI:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    cli = PlaylistCLI.__new__(PlaylistCLI)
    cli._repos = Repositories(conn)
    cli._spotify = FakeSpotify(responses)
    return cli


@pytest.fixture(autouse=True)
def _no_sink():
    ui.set_output_sink(None)
    yield
    ui.set_output_sink(None)


def _event_rows(cli: PlaylistCLI) -> List[sqlite3.Row]:
    return cli.repos.conn.execute("SELECT * FROM listen_events ORDER BY played_at;").fetchall()


class TestCursorFlow:
    def test_first_sync_has_no_after_and_persists_cursor(self):
        cli = _make_cli(
            [
                {
                    "items": [_item("Song One", "Artist A", "2026-06-10T01:00:00Z")],
                    "cursors": {"after": "1770000000000"},
                }
            ]
        )
        cli.sync_listen_history()

        assert cli.spotify.sp.calls == [{"limit": 50}]  # no after on first pull
        state = cli.repos.sync_state.get("recently_played")
        assert state is not None
        assert state["cursor"] == "1770000000000"
        assert state["last_synced_at"]  # recorded

    def test_second_sync_passes_cursor_as_after_int(self):
        cli = _make_cli(
            [
                {
                    "items": [_item("Song One", "Artist A", "2026-06-10T01:00:00Z")],
                    "cursors": {"after": "1770000000000"},
                },
                {
                    "items": [_item("Song Two", "Artist A", "2026-06-10T02:00:00Z")],
                    "cursors": {"after": "1770000099999"},
                },
            ]
        )
        cli.sync_listen_history()
        cli.sync_listen_history()

        assert cli.spotify.sp.calls[1] == {"limit": 50, "after": 1770000000000}
        state = cli.repos.sync_state.get("recently_played")
        assert state is not None
        assert state["cursor"] == "1770000099999"

    def test_empty_page_without_cursors_leaves_cursor_untouched(self):
        cli = _make_cli(
            [
                {
                    "items": [_item("Song One", "Artist A", "2026-06-10T01:00:00Z")],
                    "cursors": {"after": "1770000000000"},
                },
                {"items": [], "cursors": None},
            ]
        )
        cli.sync_listen_history()
        first_state = cli.repos.sync_state.get("recently_played")
        assert first_state is not None

        cli.sync_listen_history()
        state = cli.repos.sync_state.get("recently_played")
        assert state is not None
        assert state["cursor"] == "1770000000000"  # untouched
        assert state["last_synced_at"]  # still recorded

    def test_corrupt_cursor_falls_back_to_cursorless_pull(self):
        cli = _make_cli([{"items": [], "cursors": None}])
        cli.repos.sync_state.set("recently_played", "not-a-number", None)
        cli.repos.conn.commit()

        cli.sync_listen_history()
        assert cli.spotify.sp.calls == [{"limit": 50}]


class TestEventRows:
    def test_events_carry_context_uri_and_source(self):
        cli = _make_cli(
            [
                {
                    "items": [
                        _item(
                            "Song One",
                            "Artist A",
                            "2026-06-10T01:00:00Z",
                            context_uri="spotify:playlist:abc",
                        ),
                        _item("Song Two", "Artist B", "2026-06-10T02:00:00Z"),
                    ],
                    "cursors": {"after": "1770000000000"},
                }
            ]
        )
        cli.sync_listen_history()

        rows = _event_rows(cli)
        assert len(rows) == 2
        assert rows[0]["context_uri"] == "spotify:playlist:abc"
        assert rows[1]["context_uri"] is None
        assert {row["source"] for row in rows} == {"recently_played"}
        assert rows[0]["ms_played"] is None  # polling never knows duration

    def test_event_identity_uses_bare_base62_id(self):
        """Polled events store the BARE base62 id and mint the event_id from it
        — the identical recipe gdpr_import uses, so the two sources share one
        convention. (tracks.spotify_id keeps the full URI.)"""
        import uuid

        cli = _make_cli(
            [
                {
                    "items": [_item("Song One", "Artist A", "2026-06-10T01:00:00Z")],
                    "cursors": {"after": "1770000000000"},
                }
            ]
        )
        cli.sync_listen_history()

        rows = _event_rows(cli)
        assert len(rows) == 1
        bare_id = "spotify:track:artistasongone"[len("spotify:track:") :]
        assert rows[0]["spotify_id"] == bare_id  # no 'spotify:track:' prefix
        expected = uuid.uuid5(uuid.NAMESPACE_URL, f"{bare_id}|2026-06-10T01:00:00Z").hex
        assert rows[0]["event_id"] == expected
        # tracks.spotify_id keeps the full-URI convention.
        track = cli.repos.tracks.get("artist a|||song one")
        assert track is not None
        assert track["spotify_id"] == "spotify:track:artistasongone"

    def test_resync_same_items_does_not_duplicate(self):
        page = {
            "items": [_item("Song One", "Artist A", "2026-06-10T01:00:00Z")],
            "cursors": {"after": "1770000000000"},
        }
        cli = _make_cli([page, dict(page)])
        cli.sync_listen_history()
        cli.sync_listen_history()
        assert len(_event_rows(cli)) == 1  # uuid5(event) is deterministic


class TestQuietMode:
    def test_quiet_emits_exactly_one_line_when_new_plays(self, capsys):
        cli = _make_cli(
            [
                {
                    "items": [
                        _item("Song One", "Artist A", "2026-06-10T01:00:00Z"),
                        _item("Song Two", "Artist B", "2026-06-10T02:00:00Z"),
                    ],
                    "cursors": {"after": "1770000000000"},
                }
            ]
        )
        cli.sync_listen_history(quiet=True)
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1
        assert "auto-sync: 2 new plays" in lines[0]
        assert "Listen Ledger" not in out  # no section header

    def test_quiet_emits_nothing_when_no_new_plays(self, capsys):
        cli = _make_cli([{"items": [], "cursors": None}])
        cli.sync_listen_history(quiet=True)
        assert capsys.readouterr().out.strip() == ""

    def test_quiet_reraises_api_errors(self):
        class Boom(Exception):
            pass

        cli = _make_cli([])
        cli._spotify.sp.responses = []

        def _raise(**kwargs):
            raise Boom("api down")

        cli._spotify.sp.current_user_recently_played = _raise
        with pytest.raises(Boom):
            cli.sync_listen_history(quiet=True)

    def test_loud_mode_keeps_header_and_summary(self, capsys):
        cli = _make_cli(
            [
                {
                    "items": [_item("Song One", "Artist A", "2026-06-10T01:00:00Z")],
                    "cursors": {"after": "1770000000000"},
                }
            ]
        )
        cli.sync_listen_history()
        out = capsys.readouterr().out
        assert "Listen Ledger" in out
        assert "Recorded 1 listen events." in out
