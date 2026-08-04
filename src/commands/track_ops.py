"""Track quick-ops vertical: /add /remove /move resolution + the undo stack
(PR 7 of the decomposition).

Bodies moved verbatim from PlaylistCLI methods (self -> cli rename only, and
_stored_spotify_uri's @staticmethod dropped — it is a plain module function
here). PlaylistCLI keeps one-line delegates so every call site and fixture is
unchanged; the undo stack stays a plain list at ``cli._undo_stack`` because
tests assert list equality on it (an UndoStack class is a possible later
quality pass, not a move concern). add_search_to_playlist (search vertical)
pushes onto the SAME stack via the cli delegates.
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from models import Song, track_id_for
from ui import info, section, warning

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _snapshot_playlist(cli, playlist_name: str) -> List[Dict[str, Any]]:
    """Capture a playlist's current tracks (name/artist/uri) for undo.

    Returns an empty list if the playlist doesn't exist yet (a write that
    creates it) — undoing such a write then correctly clears it.
    """
    try:
        return list(cli.spotify.get_playlist_tracks(playlist_name) or [])
    except Exception as exc:  # pragma: no cover - defensive; Spotify call
        logger.warning(f"Could not snapshot '{playlist_name}' for undo: {exc}")
        return []


def _record_undo(cli, playlist_name: str, tracks: List[Dict[str, Any]]) -> None:
    """Push a pre-write snapshot onto the session undo stack."""
    cli._undo_stack.append({"playlist": playlist_name, "tracks": list(tracks)})


def undo_last_write(cli) -> bool:
    """Restore the most recent playlist write made this session.

    Uses the ID-preserving replace, so undo keeps the same playlist — it never
    deletes and recreates it. The snapshot is popped only when the restore
    succeeds, so a failed undo can be retried.
    """
    if not cli._undo_stack:
        info("Nothing to undo.")
        return False
    entry = cli._undo_stack[-1]
    playlist_name = entry["playlist"]
    tracks = entry["tracks"]
    section("Undo", playlist_name)
    songs = [
        Song(
            id=track_id_for(track.get("artist") or "", track.get("name") or ""),
            name=track.get("name") or "",
            artist=track.get("artist") or "",
            spotify_uri=track.get("uri"),
            first_added=datetime.now(),
        )
        for track in tracks
    ]
    success = cli.spotify.replace_playlist_items(playlist_name, songs)
    if success:
        cli._undo_stack.pop()
        if tracks:
            info(f"Restored '{playlist_name}' to its previous {len(tracks)} track(s).")
        else:
            info(f"Cleared '{playlist_name}' (it had no tracks before the last change).")
    else:
        warning(f"Failed to undo the last change to '{playlist_name}'.")
    return success


def _mirror_playlist_id(cli, playlist_name: str) -> Optional[str]:
    """spotify_playlist_id of a mirrored playlist by (case-insensitive) name.

    Local-only: reads the /pull mirror (spotify_playlists), never the API.
    Returns None when the playlist hasn't been mirrored yet.
    """
    lowered = playlist_name.lower()
    for row in cli.repos.spotify_playlists.list_all():
        if (row.get("name") or "").lower() == lowered:
            return row.get("spotify_playlist_id")
    return None


def _track_candidate(cli, track_id: str) -> Optional[Dict[str, Any]]:
    """Hydrate one tracks-mirror row into a resolution candidate dict."""
    record = cli.repos.tracks.get(track_id)
    if not record:
        return None
    artist_name = record.get("artist_id") or ""
    artist_record = cli.repos.artists.get(record.get("artist_id") or "")
    if artist_record and artist_record.get("name"):
        artist_name = artist_record.get("name")
    return {
        "track_id": track_id,
        "name": record.get("name") or "",
        "artist": artist_name,
        "spotify_id": record.get("spotify_id"),
    }


def _track_candidates(cli, source_playlist: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fuzzy-match candidates from the tracks mirror.

    With ``source_playlist`` (the /remove and /move case) candidates are
    restricted to that playlist's mirrored membership; a playlist the
    mirror doesn't know yet falls back to the whole mirror with a note
    (its membership may simply predate the last /pull).
    """
    if source_playlist:
        mirror_id = cli._mirror_playlist_id(source_playlist)
        if mirror_id:
            rows = cli.repos.playlist_tracks.list_for_playlist(mirror_id)
            hydrated = (cli._track_candidate(row["track_id"]) for row in rows)
            return [candidate for candidate in hydrated if candidate]
        info(
            f"'{source_playlist}' isn't in the local mirror (run /pull) — "
            "matching against the whole library."
        )
    rows = cli.repos.conn.execute(
        """
        SELECT t.track_id, t.name, t.spotify_id,
               COALESCE(a.name, t.artist_id, '') AS artist
        FROM tracks t
        LEFT JOIN artists a ON a.artist_id = t.artist_id
        ORDER BY t.track_id
        """
    ).fetchall()
    return [
        {
            "track_id": row["track_id"],
            "name": row["name"] or "",
            "artist": row["artist"] or "",
            "spotify_id": row["spotify_id"],
        }
        for row in rows
    ]


def _resolve_track(
    cli,
    query: str,
    track_id: Optional[str] = None,
    source_playlist: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve a freeform query (or exact --id) to one mirrored track.

    Exact first: an explicit ``track_id``, a raw ``artist|||name`` query,
    or an "artist - name" query whose ``models.track_id_for`` id is in the
    mirror. Otherwise difflib scoring over 'artist - name' rows (restricted
    to the source playlist's membership for /remove and /move). A miss or
    an ambiguous match fails loudly with a top-3 near-miss listing — no
    wizard.
    """
    query = (query or "").strip()
    if track_id:
        candidate = cli._track_candidate(track_id)
        if candidate is None:
            warning(f"No track with id '{track_id}' in the local mirror.")
        return candidate
    if not query:
        warning("Provide a track query or --id TRACK_ID.")
        return None
    if "|||" in query:
        candidate = cli._track_candidate(query.lower())
        if candidate is None:
            warning(f"No track with id '{query.lower()}' in the local mirror.")
        return candidate
    if " - " in query:
        artist, name = query.split(" - ", 1)
        candidate = cli._track_candidate(track_id_for(artist.strip(), name.strip()))
        if candidate is not None:
            return candidate

    candidates = cli._track_candidates(source_playlist)
    if not candidates:
        warning("No local tracks to match against. Run /pull to mirror your library first.")
        return None
    lowered = query.lower()
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for candidate in candidates:
        label = f"{candidate['artist']} - {candidate['name']}".lower()
        score = max(
            difflib.SequenceMatcher(None, lowered, label).ratio(),
            difflib.SequenceMatcher(None, lowered, candidate["name"].lower()).ratio(),
        )
        scored.append((score, candidate))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    runner_score = scored[1][0] if len(scored) > 1 else 0.0
    cutoff, ambiguity_gap = 0.6, 0.05
    if best_score >= cutoff and (best_score - runner_score) >= ambiguity_gap:
        return best
    if best_score < cutoff:
        warning(f"No confident match for '{query}'. Closest near-misses:")
    else:
        warning(f"'{query}' is ambiguous. Top matches:")
    for score, candidate in scored[:3]:
        info(
            f"  {candidate['artist']} — {candidate['name']}  "
            f"(id: {candidate['track_id']}, score {score:.2f})"
        )
    info("Re-run with a more specific query or --id TRACK_ID.")
    return None


def _lives_in_line(cli, track_id: str) -> str:
    """'lives in: A, B' from the mirror — first app caller of playlists_for_track."""
    names = [
        playlist.get("name") or ""
        for playlist in cli.repos.playlist_tracks.playlists_for_track(track_id)
    ]
    names = [name for name in names if name]
    return f"lives in: {', '.join(names)}" if names else "lives in: no mirrored playlist"


def _stored_spotify_uri(raw: Optional[str]) -> Optional[str]:
    """A stored ``tracks.spotify_id`` (URI / bare id / URL) -> track URI, or None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("spotify:track:"):
        return raw
    if raw.startswith("http"):
        tail = raw.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        return f"spotify:track:{tail}" if tail else None
    if ":" not in raw and "/" not in raw:
        return f"spotify:track:{raw}"
    return None


def _song_from_candidate(cli, candidate: Dict[str, Any]) -> Song:
    return Song(
        id=candidate["track_id"],
        name=candidate["name"],
        artist=candidate["artist"],
        spotify_uri=cli._stored_spotify_uri(candidate.get("spotify_id")),
        first_added=datetime.now(),
    )


def _remove_uri_for(cli, candidate: Dict[str, Any]) -> Optional[str]:
    """URI for a playlist remove: tracks.spotify_id, else a live search_song
    fallback (pre-capture rows carry no Spotify identity yet)."""
    uri = cli._stored_spotify_uri(candidate.get("spotify_id"))
    if uri:
        return uri
    uri = cli.spotify.search_song(cli._song_from_candidate(candidate))
    if not uri:
        warning(
            f"No Spotify URI for '{candidate['name']}' — not stored locally and "
            "the live search found no confident match."
        )
    return uri


def _patch_mirror_membership(cli, playlist_name: str, track_id: str, present: bool) -> None:
    """Patch one playlist_tracks mirror row after a successful Spotify write.

    Keeps /status and follow-up /remove or /move resolutions fresh until the
    next /pull re-mirrors the playlist. No-op when the playlist isn't
    mirrored yet (the next /pull captures it, membership row included).
    """
    mirror_id = cli._mirror_playlist_id(playlist_name)
    if not mirror_id:
        return
    now = datetime.utcnow().isoformat() + "Z"
    if present:
        cli.repos.conn.execute(
            """
            INSERT OR IGNORE INTO playlist_tracks (
              spotify_playlist_id, track_id, added_at, position, synced_at
            )
            VALUES (
              ?, ?, ?,
              (SELECT COALESCE(MAX(position) + 1, 0) FROM playlist_tracks
               WHERE spotify_playlist_id = ?),
              ?
            );
            """,
            (mirror_id, track_id, now, mirror_id, now),
        )
    else:
        cli.repos.conn.execute(
            "DELETE FROM playlist_tracks WHERE spotify_playlist_id = ? AND track_id = ?;",
            (mirror_id, track_id),
        )
    cli.repos.conn.commit()


def add_track_to_playlist(
    cli, query: str, playlist_name: str, track_id: Optional[str] = None
) -> bool:
    """Add one mirrored track to a Spotify playlist (undoable).

    Fuzzy-resolves ``query`` against the whole tracks mirror (or takes the
    exact ``track_id``), then reuses the proven write choreography:
    snapshot -> append (URI-less rows fall back to append_to_playlist's
    live search) -> record undo -> patch the mirror row.
    """
    candidate = cli._resolve_track(query, track_id=track_id)
    if candidate is None:
        return False
    section("Add Track", playlist_name)
    info(
        f"{candidate['artist']} — {candidate['name']} ({cli._lives_in_line(candidate['track_id'])})"
    )
    prior = cli._snapshot_playlist(playlist_name)
    song = cli._song_from_candidate(candidate)
    success = cli.spotify.append_to_playlist(playlist_name, [song])
    if success:
        cli._record_undo(playlist_name, prior)
        cli._patch_mirror_membership(playlist_name, candidate["track_id"], present=True)
        info(f"Added '{candidate['name']}' to playlist '{playlist_name}'.")
        info("Run /undo to revert this change.")
    else:
        warning(f"Failed to add '{candidate['name']}' to playlist '{playlist_name}'.")
    return success


def remove_track_from_playlist(
    cli, query: str, playlist_name: str, track_id: Optional[str] = None
) -> bool:
    """Remove one track from a Spotify playlist (undoable).

    Resolution is restricted to the source playlist's mirrored membership.
    The underlying call is playlist_remove_all_occurrences_of_items, so ALL
    duplicate occurrences vanish — the undo snapshot restores them.
    """
    candidate = cli._resolve_track(query, track_id=track_id, source_playlist=playlist_name)
    if candidate is None:
        return False
    section("Remove Track", playlist_name)
    info(
        f"{candidate['artist']} — {candidate['name']} ({cli._lives_in_line(candidate['track_id'])})"
    )
    uri = cli._remove_uri_for(candidate)
    if not uri:
        return False
    prior = cli._snapshot_playlist(playlist_name)
    success = cli.spotify.remove_from_playlist(playlist_name, [uri])
    if success:
        cli._record_undo(playlist_name, prior)
        cli._patch_mirror_membership(playlist_name, candidate["track_id"], present=False)
        info(f"Removed '{candidate['name']}' from playlist '{playlist_name}' (all occurrences).")
        info("Run /undo to revert this change.")
    else:
        warning(f"Failed to remove '{candidate['name']}' from playlist '{playlist_name}'.")
    return success


def move_track(
    cli,
    query: str,
    from_playlist: str,
    to_playlist: str,
    track_id: Optional[str] = None,
) -> bool:
    """Move one track between playlists: remove from source, append to dest.

    BOTH playlists are snapshotted before either write, and each successful
    write pushes its own undo entry — LIFO, so the first /undo reverts the
    destination append and a second /undo reverts the source removal.
    """
    candidate = cli._resolve_track(query, track_id=track_id, source_playlist=from_playlist)
    if candidate is None:
        return False
    section("Move Track", f"{from_playlist} → {to_playlist}")
    info(
        f"{candidate['artist']} — {candidate['name']} ({cli._lives_in_line(candidate['track_id'])})"
    )
    uri = cli._remove_uri_for(candidate)
    if not uri:
        return False
    # Snapshot BOTH playlists before either write, so a mid-move failure
    # still leaves an accurate restore point for each side.
    prior_from = cli._snapshot_playlist(from_playlist)
    prior_to = cli._snapshot_playlist(to_playlist)
    if not cli.spotify.remove_from_playlist(from_playlist, [uri]):
        warning(
            f"Failed to remove '{candidate['name']}' from '{from_playlist}' — nothing was moved."
        )
        return False
    cli._record_undo(from_playlist, prior_from)
    cli._patch_mirror_membership(from_playlist, candidate["track_id"], present=False)
    song = cli._song_from_candidate(candidate)
    if not cli.spotify.append_to_playlist(to_playlist, [song]):
        warning(
            f"Removed '{candidate['name']}' from '{from_playlist}' but failed to add "
            f"it to '{to_playlist}'. Run /undo to restore '{from_playlist}'."
        )
        return False
    cli._record_undo(to_playlist, prior_to)
    cli._patch_mirror_membership(to_playlist, candidate["track_id"], present=True)
    info(f"Moved '{candidate['name']}' from '{from_playlist}' to '{to_playlist}'.")
    info("Run /undo to revert (twice to revert both playlists).")
    return True
