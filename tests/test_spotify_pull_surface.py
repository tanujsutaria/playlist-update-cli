"""Tests for the SpotifyManager surface /pull relies on.

get_playlist_items_full must paginate, keep added_at (which the older
get_playlist_tracks field filter drops), request the rich track fields, and
propagate the initial fetch error (so /pull can surface scope hints).
current_user_id returns the cached id and only refetches when missing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from spotify_manager import SpotifyManager


def _manager(sp) -> SpotifyManager:
    manager = SpotifyManager.__new__(SpotifyManager)
    manager.sp = sp
    manager.user_id = "test_user_id"
    manager.playlists = {}
    return manager


def _item(name: str, added_at: str):
    return {
        "added_at": added_at,
        "track": {
            "name": name,
            "id": f"id-{name}",
            "uri": f"spotify:track:{name}",
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
            "duration_ms": 1000,
            "explicit": False,
            "popularity": 1,
            "external_urls": {"spotify": "https://open.spotify.com/track/x"},
        },
    }


class TestGetPlaylistItemsFull:
    def test_paginates_and_keeps_added_at(self):
        sp = MagicMock()
        page1 = {"items": [_item("one", "2026-06-01T00:00:00Z")], "next": "page2"}
        page2 = {"items": [_item("two", "2026-06-02T00:00:00Z")], "next": None}
        sp.playlist_items.return_value = page1
        sp.next.return_value = page2

        items = _manager(sp).get_playlist_items_full("pl-1")

        assert [i["added_at"] for i in items] == [
            "2026-06-01T00:00:00Z",
            "2026-06-02T00:00:00Z",
        ]
        sp.next.assert_called_once_with(page1)

    def test_requests_rich_fields_including_added_at(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {"items": [], "next": None}
        _manager(sp).get_playlist_items_full("pl-1")

        _, kwargs = sp.playlist_items.call_args
        fields = kwargs["fields"]
        for needed in ("added_at", "duration_ms", "explicit", "popularity", "external_urls"):
            assert needed in fields
        assert "next" in fields  # sp.next() can't paginate without it

    def test_skips_null_tracks(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {
            "items": [{"added_at": "2026-06-01T00:00:00Z", "track": None}, None],
            "next": None,
        }
        assert _manager(sp).get_playlist_items_full("pl-1") == []

    def test_initial_error_propagates(self):
        sp = MagicMock()
        sp.playlist_items.side_effect = RuntimeError("403 scope")
        with pytest.raises(RuntimeError):
            _manager(sp).get_playlist_items_full("pl-1")

    def test_next_page_error_propagates(self):
        """A mid-pagination failure must raise — degrading to a partial list
        would let /pull persist a truncated membership under the current
        snapshot_id, freezing the truncation until the playlist changes."""
        sp = MagicMock()
        sp.playlist_items.return_value = {
            "items": [_item("one", "2026-06-01T00:00:00Z")],
            "next": "page2",
        }
        sp.next.side_effect = RuntimeError("flaky")
        with pytest.raises(RuntimeError, match="flaky"):
            _manager(sp).get_playlist_items_full("pl-1")


class TestCurrentUserId:
    def test_returns_cached_id_without_refetch(self):
        sp = MagicMock()
        manager = _manager(sp)
        assert manager.current_user_id() == "test_user_id"
        sp.current_user.assert_not_called()

    def test_refetches_when_missing(self):
        sp = MagicMock()
        sp.current_user.return_value = {"id": "fetched_user"}
        manager = _manager(sp)
        manager.user_id = None
        assert manager.current_user_id() == "fetched_user"

    def test_fetch_failure_returns_none(self):
        sp = MagicMock()
        sp.current_user.side_effect = RuntimeError("down")
        manager = _manager(sp)
        manager.user_id = None
        assert manager.current_user_id() is None
