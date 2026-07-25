"""Tests for /pull — the read-only mirror of the user's real Spotify library.

Drives the REAL parser + dispatch_command against a PlaylistCLI wired to a
temp SQLite DB and a deterministic FakeSpotify (the only mock). Asserts real
SQLite state: spotify_playlists / playlist_tracks (WITH added_at) / liked_tracks
rows, snapshot_id skip on a second run, prune/delete of unfollowed playlists
and unliked tracks, sync_state bookkeeping, and the 403 scope hint surface.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

import pytest

import spotify_manager
import ui
from arg_parse import parse_tokens
from main import PlaylistCLI, dispatch_command
from storage.migrations import ensure_schema
from storage.repos import Repositories


def _spotify_track(name: str, artist: str) -> Dict[str, Any]:
    uri = f"spotify:track:{(artist + name).lower().replace(' ', '')[:22]}"
    return {
        "name": name,
        "id": uri.split(":")[-1],
        "uri": uri,
        "artists": [{"name": artist}],
        "album": {"name": f"{name} Album", "release_date": "2025-01-01"},
        "duration_ms": 180000,
        "explicit": False,
        "popularity": 42,
        "external_urls": {"spotify": f"https://open.spotify.com/track/{uri}"},
    }


def _playlist_item(name: str, artist: str, added_at: str) -> Dict[str, Any]:
    return {"added_at": added_at, "track": _spotify_track(name, artist)}


class FakeSp:
    """Fake spotipy client for the /pull endpoints."""

    def __init__(self, owner: "FakeSpotify") -> None:
        self._owner = owner
        self.saved_pages_served = 0

    def current_user_playlists(self, limit: int = 50) -> Dict[str, Any]:
        return {"items": list(self._owner.remote_playlists), "next": None}

    def next(self, results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return None

    def current_user_saved_tracks(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        self.saved_pages_served += 1
        items = self._owner.liked_items[offset : offset + limit]
        return {"items": items, "total": len(self._owner.liked_items)}


class FakeSpotify:
    """SpotifyManager-shaped double exposing only what /pull touches."""

    def __init__(self) -> None:
        self.sp = FakeSp(self)
        self.user_id = "me"
        # playlist_id -> list of playlist items (added_at + track)
        self.playlist_items: Dict[str, List[Dict[str, Any]]] = {}
        self.remote_playlists: List[Dict[str, Any]] = []
        self.liked_items: List[Dict[str, Any]] = []
        self.items_full_calls: List[str] = []

    def add_playlist(
        self,
        playlist_id: str,
        name: str,
        snapshot_id: str,
        items: List[Dict[str, Any]],
        owner_id: str = "me",
        owner_display: Optional[str] = None,
    ) -> None:
        self.remote_playlists.append(
            {
                "id": playlist_id,
                "name": name,
                "snapshot_id": snapshot_id,
                "owner": {"id": owner_id, "display_name": owner_display},
                "tracks": {"total": len(items)},
            }
        )
        self.playlist_items[playlist_id] = items

    def remove_playlist(self, playlist_id: str) -> None:
        self.remote_playlists = [p for p in self.remote_playlists if p["id"] != playlist_id]
        self.playlist_items.pop(playlist_id, None)

    def current_user_id(self) -> Optional[str]:
        return self.user_id

    def get_playlist_items_full(
        self,
        playlist_id: str,
        on_page: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        self.items_full_calls.append(playlist_id)
        items = list(self.playlist_items.get(playlist_id, []))
        # Mirror the real client's paging contract: on_page fires once per
        # (simulated 100-item) page with (page_number, items_so_far).
        if on_page is not None:
            pages = max(1, -(-len(items) // 100))
            for page_no in range(1, pages + 1):
                on_page(page_no, min(page_no * 100, len(items)))
        return items


@pytest.fixture(autouse=True)
def _no_sink():
    """Reset every process-global sink these tests can touch (see test_ui.py's
    reset_sinks): output + status sinks and the spotify_manager retry-notice
    callback, so nothing leaks across test ordering."""
    ui.set_output_sink(None)
    ui.set_status_sink(None)
    spotify_manager.set_retry_status_callback(None)
    yield
    ui.set_output_sink(None)
    ui.set_status_sink(None)
    spotify_manager.set_retry_status_callback(None)


@pytest.fixture
def status_sink() -> List[str]:
    seen: List[str] = []
    ui.set_status_sink(lambda stage: seen.append(stage) if stage is not None else None)
    return seen


@pytest.fixture
def cli() -> PlaylistCLI:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    c = PlaylistCLI.__new__(PlaylistCLI)
    c._repos = Repositories(conn)
    c._spotify = FakeSpotify()
    return c


def _run(cli: PlaylistCLI, command_str: str) -> int:
    command, args, error = parse_tokens(command_str.split())
    assert error is None, error
    assert command is not None
    return dispatch_command(cli, command, args)


def _seed_remote(cli: PlaylistCLI) -> None:
    cli.spotify.add_playlist(
        "pl-1",
        "Beach Mix",
        "snap-1",
        [
            _playlist_item("Song One", "Artist A", "2026-06-01T00:00:00Z"),
            _playlist_item("Song Two", "Artist B", "2026-06-02T00:00:00Z"),
        ],
        owner_display="Tanuj",
    )
    cli.spotify.add_playlist(
        "pl-2",
        "Algo Mix",
        "snap-x",
        [_playlist_item("Song Three", "Artist C", "2026-06-03T00:00:00Z")],
        owner_id="spotify",
        owner_display="Spotify",
    )
    cli.spotify.liked_items = [
        {"added_at": "2026-06-04T00:00:00Z", "track": _spotify_track("Song One", "Artist A")},
        {"added_at": "2026-06-05T00:00:00Z", "track": _spotify_track("Liked Song", "Artist D")},
    ]


class TestPullPlaylists:
    def test_writes_playlists_and_memberships_with_added_at(self, cli):
        _seed_remote(cli)
        rc = _run(cli, "pull")
        assert rc == 0

        stored = cli.repos.spotify_playlists.get("pl-1")
        assert stored is not None
        assert stored["name"] == "Beach Mix"
        assert stored["owner"] == "Tanuj"
        assert stored["is_owned"] == 1
        assert stored["snapshot_id"] == "snap-1"
        assert stored["total_tracks"] == 2

        algo = cli.repos.spotify_playlists.get("pl-2")
        assert algo is not None
        assert algo["is_owned"] == 0  # owned by 'spotify', not the user

        rows = cli.repos.playlist_tracks.list_for_playlist("pl-1")
        assert [r["track_id"] for r in rows] == [
            "artist a|||song one",
            "artist b|||song two",
        ]
        assert rows[0]["added_at"] == "2026-06-01T00:00:00Z"
        assert rows[0]["position"] == 0
        assert rows[1]["position"] == 1
        assert rows[0]["synced_at"]

        # Tracks were upserted (FK safety) before memberships.
        assert cli.repos.tracks.get("artist a|||song one") is not None

    def test_snapshot_skip_on_second_run(self, cli):
        _seed_remote(cli)
        _run(cli, "pull")
        assert cli.spotify.items_full_calls == ["pl-1", "pl-2"]

        payload = cli.pull_spotify_library()
        # Second run: nothing re-fetched, both skipped as unchanged.
        assert cli.spotify.items_full_calls == ["pl-1", "pl-2"]
        assert payload["playlists"]["synced"] == 0
        assert payload["playlists"]["skipped"] == 2

    def test_full_forces_refetch(self, cli):
        _seed_remote(cli)
        _run(cli, "pull")
        rc = _run(cli, "pull --full")
        assert rc == 0
        assert cli.spotify.items_full_calls == ["pl-1", "pl-2", "pl-1", "pl-2"]

    def test_changed_snapshot_refetches_and_replaces(self, cli):
        _seed_remote(cli)
        _run(cli, "pull")

        # Remote playlist changed: new snapshot, one track dropped.
        cli.spotify.remove_playlist("pl-1")
        cli.spotify.add_playlist(
            "pl-1",
            "Beach Mix",
            "snap-2",
            [_playlist_item("Song Two", "Artist B", "2026-06-02T00:00:00Z")],
            owner_display="Tanuj",
        )
        _run(cli, "pull")

        rows = cli.repos.playlist_tracks.list_for_playlist("pl-1")
        assert [r["track_id"] for r in rows] == ["artist b|||song two"]
        stored = cli.repos.spotify_playlists.get("pl-1")
        assert stored is not None
        assert stored["snapshot_id"] == "snap-2"

    def test_unfollowed_playlist_removed(self, cli):
        _seed_remote(cli)
        _run(cli, "pull")
        assert cli.repos.spotify_playlists.get("pl-2") is not None

        cli.spotify.remove_playlist("pl-2")
        payload = cli.pull_spotify_library()
        assert payload["playlists"]["removed"] == 1
        assert cli.repos.spotify_playlists.get("pl-2") is None
        # Memberships cascade with the playlist row.
        assert cli.repos.playlist_tracks.list_for_playlist("pl-2") == []

    def test_pull_does_not_clobber_curated_track_state(self, cli):
        """The mirror must enrich, not clobber: status/last_decision/
        decision_reason/created_at set by curation survive a /pull."""
        _seed_remote(cli)
        first_seen = "2026-01-01T00:00:00Z"
        cli.repos.artists.upsert(artist_id="artist a", name="Artist A")
        cli.repos.tracks.upsert(
            {
                "track_id": "artist a|||song one",
                "name": "Song One",
                "artist_id": "artist a",
                "status": "accepted",
                "last_decision": "accepted",
                "decision_reason": "search: beach vibes",
                "created_at": first_seen,
                "updated_at": first_seen,
            }
        )
        cli.repos.conn.commit()

        rc = _run(cli, "pull")
        assert rc == 0

        track = cli.repos.tracks.get("artist a|||song one")
        assert track is not None
        assert track["status"] == "accepted"
        assert track["last_decision"] == "accepted"
        assert track["decision_reason"] == "search: beach vibes"
        assert track["created_at"] == first_seen  # first-seen provenance kept
        # …while Spotify metadata was still refreshed by the mirror.
        assert track["popularity"] == 42
        assert track["album_name"] == "Song One Album"
        assert track["updated_at"] != first_seen

    def test_duplicate_track_in_playlist_kept_once(self, cli):
        cli.spotify.add_playlist(
            "pl-dup",
            "Dupes",
            "snap-d",
            [
                _playlist_item("Same Song", "Artist A", "2026-06-01T00:00:00Z"),
                _playlist_item("Same Song", "Artist A", "2026-06-02T00:00:00Z"),
            ],
        )
        rc = _run(cli, "pull --playlists-only")
        assert rc == 0
        rows = cli.repos.playlist_tracks.list_for_playlist("pl-dup")
        assert len(rows) == 1  # PK is (playlist, track); first occurrence wins


class TestPullLiked:
    def test_liked_rows_with_added_at(self, cli):
        _seed_remote(cli)
        rc = _run(cli, "pull --liked-only")
        assert rc == 0

        rows = cli.repos.liked_tracks.list_all()
        assert {r["track_id"] for r in rows} == {
            "artist a|||song one",
            "artist d|||liked song",
        }
        by_id = {r["track_id"]: r for r in rows}
        assert by_id["artist d|||liked song"]["added_at"] == "2026-06-05T00:00:00Z"
        # liked-only never touched playlists.
        assert cli.spotify.items_full_calls == []
        assert cli.repos.spotify_playlists.list_all() == []

    def test_unliked_track_pruned(self, cli):
        _seed_remote(cli)
        _run(cli, "pull --liked-only")
        assert cli.repos.liked_tracks.count() == 2

        cli.spotify.liked_items = cli.spotify.liked_items[:1]  # unliked one
        payload = cli.pull_spotify_library(liked_only=True)
        assert payload["liked"]["pruned"] == 1
        assert cli.repos.liked_tracks.count() == 1

    def test_duplicate_liked_versions_counted_once(self, cli):
        """A single + album version of one song canonicalize to one track_id;
        the liked count and keep set must not be inflated by the duplicate."""
        cli.spotify.liked_items = [
            {"added_at": "2026-06-04T00:00:00Z", "track": _spotify_track("Song One", "Artist A")},
            {"added_at": "2026-06-05T00:00:00Z", "track": _spotify_track("Song One", "Artist A")},
        ]
        payload = cli.pull_spotify_library(liked_only=True)
        assert payload["liked"]["liked"] == 1
        assert cli.repos.liked_tracks.count() == 1
        # First occurrence wins (same convention as playlist memberships).
        row = cli.repos.liked_tracks.list_all()[0]
        assert row["added_at"] == "2026-06-04T00:00:00Z"

    def test_liked_pagination(self, cli):
        cli.spotify.liked_items = [
            {
                "added_at": f"2026-06-01T00:00:{i % 60:02d}Z",
                "track": _spotify_track(f"Song {i}", f"Artist {i}"),
            }
            for i in range(75)
        ]
        _run(cli, "pull --liked-only")
        assert cli.repos.liked_tracks.count() == 75
        assert cli.spotify.sp.saved_pages_served >= 2


class TestPullBookkeeping:
    def test_sync_state_rows_written(self, cli):
        _seed_remote(cli)
        _run(cli, "pull")
        for source in ("pull_playlists", "pull_liked"):
            state = cli.repos.sync_state.get(source)
            assert state is not None, source
            assert state["cursor"] is None
            assert state["last_synced_at"]

    def test_playlists_only_skips_liked_state(self, cli):
        _seed_remote(cli)
        _run(cli, "pull --playlists-only")
        assert cli.repos.sync_state.get("pull_playlists") is not None
        assert cli.repos.sync_state.get("pull_liked") is None

    def test_honesty_caption_rendered(self, cli, capsys):
        _seed_remote(cli)
        _run(cli, "pull")
        out = capsys.readouterr().out
        assert "mirror is read-only; local edits are not pushed" in out

    def test_json_payload_shape(self, cli, capsys):
        import json

        _seed_remote(cli)
        rc = _run(cli, "pull --json")
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["playlists"]["synced"] == 2
        assert payload["playlists"]["skipped"] == 0
        assert payload["playlists"]["removed"] == 0
        assert payload["playlists"]["memberships"] == 3
        assert payload["liked"]["liked"] == 2
        assert payload["liked"]["pruned"] == 0
        assert payload["synced_at"]


class TestPullErrors:
    class _ScopeError(Exception):
        def __init__(self) -> None:
            super().__init__("http status: 403, code:-1 - Insufficient client scope")
            self.http_status = 403

    def test_scope_error_surfaces_reauth_hint(self, cli, capsys):
        def _boom(limit=50):
            raise self._ScopeError()

        cli.spotify.sp.current_user_playlists = _boom
        rc = _run(cli, "pull")
        assert rc == 1
        out = capsys.readouterr().out
        assert "Re-authorize" in out
        assert "Pull failed" not in out

    def test_generic_error_keeps_generic_message(self, cli, capsys):
        def _boom(limit=50):
            raise RuntimeError("network down")

        cli.spotify.sp.current_user_playlists = _boom
        rc = _run(cli, "pull")
        assert rc == 1
        out = capsys.readouterr().out
        assert "Pull failed" in out
        assert "network down" in out

    def test_pagination_failure_never_persists_truncated_mirror(self, cli):
        """A mid-pagination failure aborts the pull (rollback): no snapshot_id
        is stored for the truncated playlist, so the next pull re-fetches it
        instead of snapshot-skipping a frozen partial membership."""
        _seed_remote(cli)
        real_items_full = cli.spotify.get_playlist_items_full

        def _flaky(playlist_id, on_page=None):
            cli.spotify.items_full_calls.append(playlist_id)
            raise RuntimeError("page 2 fetch failed")

        cli.spotify.get_playlist_items_full = _flaky
        rc = _run(cli, "pull")
        assert rc == 1
        # Whole pull rolled back: nothing persisted, snapshot_id not stored.
        assert cli.repos.spotify_playlists.get("pl-1") is None
        assert cli.repos.playlist_tracks.count() == 0

        # Next pull re-fetches every playlist (no stale snapshot skip).
        cli.spotify.get_playlist_items_full = real_items_full
        rc = _run(cli, "pull")
        assert rc == 0
        assert cli.spotify.items_full_calls[-2:] == ["pl-1", "pl-2"]
        stored = cli.repos.spotify_playlists.get("pl-1")
        assert stored is not None
        assert stored["snapshot_id"] == "snap-1"


class TestPullProgress:
    """Live progress for /pull: top-bar stage strings via the ui status sink,
    throttled scrollback lines, visible rate-limit backoff — no per-item spam."""

    def test_status_stages_emitted_in_order(self, cli, status_sink):
        _seed_remote(cli)
        rc = _run(cli, "pull")
        assert rc == 0

        # Playlist-list paging, per-playlist counts, per-playlist track fetch,
        # then liked paging — in that order.
        assert "playlists p1" in status_sink
        assert "playlists 1/2" in status_sink
        assert "playlists 2/2" in status_sink
        assert any(s.startswith("tracks: Beach Mix") for s in status_sink)
        assert any(s.startswith("tracks: Algo Mix") for s in status_sink)
        assert any(s.startswith("liked ") for s in status_sink)

        first_playlist = status_sink.index("playlists 1/2")
        first_tracks = min(i for i, s in enumerate(status_sink) if s.startswith("tracks: "))
        first_liked = min(i for i, s in enumerate(status_sink) if s.startswith("liked "))
        assert first_playlist < first_tracks < first_liked

    def test_liked_status_shows_running_count_over_total(self, cli, status_sink):
        cli.spotify.liked_items = [
            {
                "added_at": "2026-06-01T00:00:00Z",
                "track": _spotify_track(f"Song {i}", f"Artist {i}"),
            }
            for i in range(130)
        ]
        _run(cli, "pull --liked-only")
        assert "liked 50/130" in status_sink
        assert "liked 100/130" in status_sink

    def test_playlist_track_paging_reaches_status_bar(self, cli, status_sink):
        # 250 tracks -> 3 simulated pages from the fake's on_page contract.
        cli.spotify.add_playlist(
            "pl-big",
            "Big Mix",
            "snap-big",
            [
                _playlist_item(f"Song {i}", f"Artist {i}", "2026-06-01T00:00:00Z")
                for i in range(250)
            ],
        )
        _run(cli, "pull --playlists-only")
        assert "tracks: Big Mix p2" in status_sink
        assert "tracks: Big Mix p3" in status_sink

    def test_scrollback_progress_is_throttled_not_per_item(self, cli, capsys):
        # 130 liked tracks across 3 pages must produce exactly ONE throttled
        # "liked:" progress line (the 100-item crossing), never one per item.
        cli.spotify.liked_items = [
            {
                "added_at": "2026-06-01T00:00:00Z",
                "track": _spotify_track(f"Song {i}", f"Artist {i}"),
            }
            for i in range(130)
        ]
        _run(cli, "pull --liked-only")
        out = capsys.readouterr().out
        assert out.count("liked:") == 1
        # Sanity: the whole scrollback stays bounded (summary + a few lines),
        # nowhere near one line per track.
        assert len(out.splitlines()) < 30

    def test_status_never_emitted_per_item(self, cli, status_sink):
        cli.spotify.liked_items = [
            {
                "added_at": "2026-06-01T00:00:00Z",
                "track": _spotify_track(f"Song {i}", f"Artist {i}"),
            }
            for i in range(130)
        ]
        _run(cli, "pull --liked-only")
        # 3 pages -> a handful of stage strings, not 130.
        assert len(status_sink) <= 6

    def test_final_summary_unchanged(self, cli, capsys):
        _seed_remote(cli)
        rc = _run(cli, "pull")
        assert rc == 0
        out = capsys.readouterr().out
        for label in (
            "Playlists synced",
            "Skipped (unchanged)",
            "Removed (unfollowed)",
            "Memberships written",
            "Liked tracks",
            "Liked pruned",
        ):
            assert label in out
        assert "mirror is read-only; local edits are not pushed" in out

    def test_rate_limit_backoff_reaches_status_sink(self, status_sink):
        """The retry helper surfaces '429 -> sleeping' through the callback /pull
        installs, instead of only a logger.warning nobody sees in the TUI."""

        class _RateLimited(Exception):
            http_status = 429

        calls = {"n": 0}

        def _flaky_once():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _RateLimited("429 rate limited")
            return "ok"

        spotify_manager.set_retry_status_callback(ui.emit_status)
        result = spotify_manager._retry_with_backoff(_flaky_once, base_delay=0.0)
        assert result == "ok"
        assert any(s.startswith("rate limited — retrying in") for s in status_sink)

    def test_pull_installs_and_uninstalls_retry_callback(self, cli):
        _seed_remote(cli)
        seen_during: List[Any] = []
        real_liked = cli.spotify.sp.current_user_saved_tracks

        def _spy(limit=50, offset=0):
            seen_during.append(spotify_manager._retry_status_callback)
            return real_liked(limit=limit, offset=offset)

        cli.spotify.sp.current_user_saved_tracks = _spy
        cli.pull_spotify_library(liked_only=True)
        # Installed while the pull's Spotify calls run…
        assert seen_during and all(cb is not None for cb in seen_during)
        # …and uninstalled afterwards so other commands stay logger-only.
        assert spotify_manager._retry_status_callback is None

    def test_pull_error_still_uninstalls_retry_callback(self, cli):
        def _boom(limit=50):
            raise RuntimeError("network down")

        cli.spotify.sp.current_user_playlists = _boom
        rc = _run(cli, "pull")
        assert rc == 1
        assert spotify_manager._retry_status_callback is None


class TestPullParsing:
    def test_flags_parse(self):
        command, args, error = parse_tokens(["pull", "--full", "--json"])
        assert error is None
        assert command == "pull"
        assert args.full is True
        assert args.json is True
        assert args.liked_only is False
        assert args.playlists_only is False

    def test_liked_and_playlists_only_are_exclusive(self):
        command, args, error = parse_tokens(["pull", "--liked-only", "--playlists-only"])
        assert command is None
        assert error is not None
