"""Import Spotify GDPR "Extended streaming history" exports into ``listen_events``.

Spotify's *extended* streaming history (requested from the privacy page; NOT the
basic "Account data" export) arrives as a ZIP — or, once extracted, a folder —
of ``Streaming_History_Audio_*.json`` files. Each record is one stream:

    ts (UTC stream-END, e.g. "2023-01-05T20:01:25Z"), ms_played,
    master_metadata_track_name, master_metadata_album_artist_name,
    master_metadata_album_album_name, spotify_track_uri
    ("spotify:track:<base62id>"), skipped, shuffle, platform, …

Podcast rows carry ``episode_name``/``spotify_episode_uri`` instead and have a
null ``spotify_track_uri``; they are skipped (this ledger is tracks-only).

Dedup contract
--------------
Event ids are minted with the *identical* recipe the live ``recently_played``
polling path uses (see ``PlaylistCLI.sync_listen_history``)::

    event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{spotify_id}|{played_at}").hex

so re-importing the same export upserts onto the same rows — the
COALESCE-enrich conflict clause in ``ListenEventsRepo.upsert`` means a
re-import (or a GDPR import over an API-polled event with the same id) adds
``ms_played``/``skipped`` without duplicating or clobbering. ``imported``
therefore counts records *upserted*, not net-new rows: exact duplicates
collapse into a single ``listen_events`` row.

Track/artist rows are ensured FK-first (``foreign_keys=ON``) but never
overwritten: a track already enriched by the API keeps its metadata; only
unknown tracks get a minimal row from the export's names.

Stdlib-only by design (zipfile/json/pathlib/uuid) — safe to import anywhere.
"""

from __future__ import annotations

import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple, Union

from models import track_id_for
from storage.repos import Repositories

GDPR_SOURCE = "gdpr_export"

# Filename prefix that identifies extended-streaming-history music files.
# (The basic Account-data export ships StreamingHistory0.json instead — a
# different shape with no track URIs — and is rejected with a friendly error.)
AUDIO_FILE_PREFIX = "Streaming_History_Audio"

_NO_FILES_HINT = (
    "No Streaming_History_Audio*.json files found in {path}. "
    "This importer needs Spotify's EXTENDED streaming history "
    "(privacy settings -> 'Extended streaming history'), not the basic "
    "Account-data export."
)

ProgressCallback = Callable[[int, int], None]


class GdprImportError(Exception):
    """A user-actionable problem with the export path/contents (not a bug)."""


def _utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with trailing 'Z' (project convention)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_audio_history_name(name: str) -> bool:
    base = Path(name).name
    return base.startswith(AUDIO_FILE_PREFIX) and base.endswith(".json")


def _parse_records(name: str, raw: Union[bytes, str]) -> List[Dict[str, Any]]:
    """Decode one export file: must be a JSON array; items pass through as-is."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GdprImportError(f"Could not parse {name}: {exc}") from exc
    if not isinstance(data, list):
        raise GdprImportError(
            f"{name} is not a streaming-history file (expected a JSON array of records)."
        )
    return data


def _iter_zip(path: Path) -> Iterator[Tuple[str, Dict[str, Any]]]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise GdprImportError(f"{path} is not a valid zip archive: {exc}") from exc
    with archive:
        names = sorted(
            (n for n in archive.namelist() if _is_audio_history_name(n)),
            key=lambda n: (Path(n).name, n),
        )
        if not names:
            raise GdprImportError(_NO_FILES_HINT.format(path=path))
        for name in names:
            base = Path(name).name
            for record in _parse_records(base, archive.read(name)):
                yield base, record


def _iter_dir(path: Path) -> Iterator[Tuple[str, Dict[str, Any]]]:
    files = sorted(
        (f for f in path.rglob("*.json") if f.is_file() and _is_audio_history_name(f.name)),
        key=lambda f: (f.name, str(f)),
    )
    if not files:
        raise GdprImportError(_NO_FILES_HINT.format(path=path))
    for file in files:
        for record in _parse_records(file.name, file.read_text(encoding="utf-8")):
            yield file.name, record


def _iter_file(path: Path) -> Iterator[Tuple[str, Dict[str, Any]]]:
    if not _is_audio_history_name(path.name):
        raise GdprImportError(_NO_FILES_HINT.format(path=path))
    for record in _parse_records(path.name, path.read_text(encoding="utf-8")):
        yield path.name, record


def iter_streaming_records(path: Union[str, Path]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield ``(filename, record)`` for every extended-streaming-history record.

    ``path`` may be the export ZIP, the extracted folder (searched
    recursively), or a single ``Streaming_History_Audio*.json`` file. Files are
    visited sorted by filename so imports are deterministic. Raises
    :class:`GdprImportError` for a missing path, an unreadable archive, or a
    path with no ``Streaming_History_Audio*.json`` files (e.g. the basic
    Account-data export).
    """
    resolved = Path(path)
    if not resolved.exists():
        raise GdprImportError(f"Path not found: {resolved}")
    if resolved.is_dir():
        return _iter_dir(resolved)
    if resolved.suffix.lower() == ".zip" or zipfile.is_zipfile(resolved):
        return _iter_zip(resolved)
    if resolved.suffix.lower() == ".json":
        return _iter_file(resolved)
    raise GdprImportError(
        f"Unsupported path: {resolved} (expected the export .zip, its extracted "
        "folder, or a Streaming_History_Audio*.json file)."
    )


def _ensure_artist(repos: Repositories, artist_name: str, now: str, known_artists: Set[str]) -> str:
    """Insert a minimal artist row iff absent (never clobber API-enriched rows)."""
    artist_id = artist_name.lower()
    if artist_id not in known_artists:
        if repos.artists.get(artist_id) is None:
            repos.artists.upsert(artist_id=artist_id, name=artist_name, updated_at=now)
        known_artists.add(artist_id)
    return artist_id


def _ensure_track(
    repos: Repositories,
    track_id: str,
    artist_id: str,
    track_name: str,
    album_name: Optional[str],
    spotify_uri: str,
    now: str,
    known_tracks: Set[str],
) -> None:
    """Insert a minimal track row iff absent (never clobber API-enriched rows)."""
    if track_id in known_tracks:
        return
    if repos.tracks.get(track_id) is None:
        repos.tracks.upsert(
            {
                "track_id": track_id,
                # tracks.spotify_id stores the full URI (matches the
                # _upsert_spotify_track convention used by ingest/polling).
                "spotify_id": spotify_uri,
                "name": track_name,
                "artist_id": artist_id,
                "album_name": album_name,
                "status": "candidate",
                "created_at": now,
                "updated_at": now,
            }
        )
    known_tracks.add(track_id)


def import_streaming_history(
    repos: Repositories,
    records: Iterable[Tuple[str, Dict[str, Any]]],
    dry_run: bool = False,
    commit_every: int = 500,
    on_progress: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Stream GDPR records into ``listen_events``; return a summary dict.

    Skips podcast/episode rows (null ``spotify_track_uri``) and rows missing
    artist/track names or ``ts``. Upserts artist+track rows FK-first, then the
    event with ``source='gdpr_export'``, ``ms_played`` and ``skipped`` (bool ->
    0/1) attached and no ``context_uri``. Commits on the repos connection every
    ``commit_every`` upserted events (plus a final commit); ``dry_run`` counts
    without touching the database. ``on_progress`` (if given) is called with
    ``(records_seen, imported_so_far)`` after every record.

    Returns ``{"files", "records_total", "episodes_skipped",
    "missing_metadata", "imported", "dry_run"}``. ``imported`` counts records
    upserted (or countable, in dry-run) — exact duplicates collapse into one
    row via the deterministic event_id, so the table can gain fewer rows.
    """
    now = _utc_now_iso()
    files_seen: Set[str] = set()
    known_artists: Set[str] = set()
    known_tracks: Set[str] = set()
    records_total = 0
    episodes_skipped = 0
    missing_metadata = 0
    imported = 0

    for filename, record in records:
        files_seen.add(filename)
        records_total += 1
        try:
            if not isinstance(record, dict):
                missing_metadata += 1
                continue
            uri = record.get("spotify_track_uri")
            if not uri:
                # Podcast/audiobook rows carry spotify_episode_uri instead.
                episodes_skipped += 1
                continue
            track_name = record.get("master_metadata_track_name")
            artist_name = record.get("master_metadata_album_artist_name")
            played_at = record.get("ts")
            if not track_name or not artist_name or not played_at:
                missing_metadata += 1
                continue

            spotify_id = str(uri).rsplit(":", 1)[-1]
            # MUST match the recently_played polling recipe exactly so a
            # re-import enriches instead of duplicating.
            event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{spotify_id}|{played_at}").hex
            raw_ms = record.get("ms_played")
            ms_played = raw_ms if isinstance(raw_ms, int) and not isinstance(raw_ms, bool) else None
            raw_skipped = record.get("skipped")
            skipped = None if raw_skipped is None else (1 if raw_skipped else 0)

            imported += 1
            if dry_run:
                continue

            artist_id = _ensure_artist(repos, str(artist_name), now, known_artists)
            track_id = track_id_for(str(artist_name), str(track_name))
            album_name = record.get("master_metadata_album_album_name")
            _ensure_track(
                repos,
                track_id,
                artist_id,
                str(track_name),
                str(album_name) if album_name else None,
                str(uri),
                now,
                known_tracks,
            )
            repos.listen_events.upsert(
                {
                    "event_id": event_id,
                    "track_id": track_id,
                    "spotify_id": spotify_id,
                    "played_at": str(played_at),
                    "source": GDPR_SOURCE,
                    "created_at": now,
                    "ms_played": ms_played,
                    "skipped": skipped,
                    "context_uri": None,
                }
            )
            if commit_every > 0 and imported % commit_every == 0:
                repos.conn.commit()
        finally:
            if on_progress is not None:
                on_progress(records_total, imported)

    if not dry_run:
        repos.conn.commit()

    return {
        "files": len(files_seen),
        "records_total": records_total,
        "episodes_skipped": episodes_skipped,
        "missing_metadata": missing_metadata,
        "imported": imported,
        "dry_run": dry_run,
    }
