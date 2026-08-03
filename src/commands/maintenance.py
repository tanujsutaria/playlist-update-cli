"""Maintenance vertical: stats export, legacy playlist sync/extract,
backups, and database cleaning (PR 6 of the decomposition).

Bodies moved verbatim from PlaylistCLI methods (self -> cli rename only);
PlaylistCLI keeps one-line delegates so every call site and test fixture is
unchanged. The backup trio anchors state dirs via config.project_root()
(module-qualified — the durable test seam from Phase 0 moves WITH the code).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import config
from playlist_resolver import report_playlist_miss
from ui import info, key_value_table, section, table

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def export_stats(cli, playlist_name: Optional[str], export_format: str, output_file: Optional[str]):
    """Export database and playlist stats to a file."""
    db_stats = cli.db.get_stats()
    export_payload = {
        "database": db_stats,
        "playlist": None,
        "generated_at": datetime.now().isoformat(),
    }

    if playlist_name:
        rm = cli._get_rotation_manager(playlist_name)
        stats = rm.get_rotation_stats()
        export_payload["playlist"] = {
            "name": playlist_name,
            "total_songs": stats.total_songs,
            "unique_songs_used": stats.unique_songs_used,
            "songs_never_used": stats.songs_never_used,
            "generations_count": stats.generations_count,
            "complete_rotation_achieved": stats.complete_rotation_achieved,
            "current_strategy": stats.current_strategy,
        }

    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = "json" if export_format == "json" else "csv"
        output_file = f"stats_export_{timestamp}.{suffix}"

    if export_format == "json":
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(export_payload, f, indent=2)
        info(f"Exported stats to {output_file}")
        return

    # CSV export: flattened key/value pairs
    rows = []
    for key, value in db_stats.items():
        rows.append(["database", key, value])

    if export_payload["playlist"]:
        for key, value in export_payload["playlist"].items():
            rows.append(["playlist", key, value])

    with open(output_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "key", "value"])
        writer.writerows(rows)
    info(f"Exported stats to {output_file}")


def sync_playlist(cli, playlist_name: str):
    """Sync a playlist with all songs in the database by adding new songs and removing songs no longer in the database"""
    try:
        # Suggest-on-miss gate: without it a typo would sync the whole
        # database into a brand-new misspelled playlist.
        if cli.spotify.get_playlist_id(playlist_name) is None:
            report_playlist_miss(cli, playlist_name)
            return
        logger.info(f"Starting database sync with playlist '{playlist_name}'...")

        # Get all songs from database
        all_songs = cli.db.get_all_songs()
        if not all_songs:
            logger.error("No songs found in database")
            return

        logger.info(f"Found {len(all_songs)} songs in database")

        # Get existing tracks in the playlist
        existing_tracks = cli.spotify.get_playlist_tracks(playlist_name)
        if existing_tracks is None:
            logger.error(f"Failed to retrieve tracks from playlist '{playlist_name}'")
            return

        # Create a set of existing URIs for quick lookup
        existing_uris = set()
        for track in existing_tracks:
            if "uri" in track:
                existing_uris.add(track["uri"])

        logger.info(f"Found {len(existing_uris)} existing tracks in playlist")

        # Create a set of database URIs for quick lookup
        database_uris = set()
        songs_to_add = []

        for song in all_songs:
            # If song doesn't have a URI yet, try to find it
            if not song.spotify_uri:
                song.spotify_uri = cli.spotify.search_song(song)

            if song.spotify_uri:
                database_uris.add(song.spotify_uri)

                # Only add songs with URIs that aren't already in the playlist
                if song.spotify_uri not in existing_uris:
                    songs_to_add.append(song)

        # Find tracks to remove (in playlist but not in database)
        uris_to_remove = existing_uris - database_uris

        logger.info(f"Found {len(songs_to_add)} new songs to add to playlist")
        logger.info(f"Found {len(uris_to_remove)} songs to remove from playlist")

        # Add new songs if needed
        if songs_to_add:
            add_success = cli.spotify.append_to_playlist(playlist_name, songs_to_add)
            if add_success:
                # Persist any URI changes discovered during Spotify search
                cli.db._save_state()
                logger.info(
                    f"Successfully added {len(songs_to_add)} new songs to playlist '{playlist_name}'"
                )
            else:
                logger.error("Failed to add new songs to playlist")
        else:
            logger.info("No new songs to add")

        # Remove songs if needed
        if uris_to_remove:
            remove_success = cli.spotify.remove_from_playlist(playlist_name, list(uris_to_remove))
            if remove_success:
                logger.info(
                    f"Successfully removed {len(uris_to_remove)} songs from playlist '{playlist_name}'"
                )
            else:
                logger.error("Failed to remove songs from playlist")
        else:
            logger.info("No songs to remove")

        if not songs_to_add and not uris_to_remove:
            logger.info("Playlist is already in sync with the database")

    except Exception as e:
        logger.error(f"Error syncing playlist: {str(e)}")
        logger.debug("Full error:", exc_info=True)


def extract_playlist(cli, playlist_name: str, output_file: Optional[str] = None):
    """Extract playlist contents to a CSV file"""
    try:
        # Distinguish "no such playlist" (miss + suggestions) from a
        # playlist that exists but is empty (the message below).
        if cli.spotify.get_playlist_id(playlist_name) is None:
            report_playlist_miss(cli, playlist_name)
            return False
        # Get tracks from playlist
        tracks = cli.spotify.get_playlist_tracks(playlist_name)

        if not tracks:
            logger.error(f"No tracks found in playlist '{playlist_name}'")
            return False

        # Generate output filename if not provided
        if output_file is None:
            output_file = f"{playlist_name}_songs.csv"

        # Ensure file extension is .csv
        if not output_file.endswith(".csv"):
            output_file += ".csv"

        # Write to file
        logger.info(f"Writing {len(tracks)} tracks to {output_file}")
        with open(output_file, "w", encoding="utf-8") as f:
            for track in tracks:
                f.write(f"{track['name']},{track['artist']}\n")

        logger.info(f"Successfully exported playlist to {output_file}")
        return True

    except Exception as e:
        logger.error(f"Error extracting playlist: {str(e)}")
        return False


def backup_data(cli, backup_name: Optional[str] = None):
    """
    Create a backup of the entire data/ folder in a new backups/ directory
    at the same level as src/.
    """
    project_root = config.project_root()
    data_dir = project_root / "data"
    backups_dir = project_root / "backups"
    backups_dir.mkdir(exist_ok=True)

    # Generate a backup folder name
    if not backup_name:
        # Use YYYYMMDD_HHMMSS format
        backup_name = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_folder = backups_dir / backup_name

    if backup_folder.exists():
        logger.warning(f"Backup folder '{backup_folder.name}' already exists. Aborting.")
        return

    logger.info(f"Creating backup '{backup_folder.name}' from data folder...")
    try:
        shutil.copytree(str(data_dir), str(backup_folder))
        logger.info(f"Backup '{backup_folder.name}' created successfully.")
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        if backup_folder.exists():
            shutil.rmtree(str(backup_folder), ignore_errors=True)
            logger.info("Cleaned up partial backup.")


def restore_data(cli, backup_name: str) -> bool:
    """
    Restore data/ from the chosen backup in backups/.

    Atomic: the restored copy is built in a staging directory BEFORE the
    live data/ is touched, then swapped in via rename. On any failure the
    live data is left intact (or rolled back), and on success the
    moved-aside old data dir is removed so it does not leak.
    """
    project_root = config.project_root()
    data_dir = project_root / "data"
    backups_dir = project_root / "backups"
    backup_folder = backups_dir / backup_name

    if not backup_folder.exists():
        logger.error(f"No such backup folder: '{backup_folder.name}'")
        return False

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    staging = project_root / f".data_restore_{ts}"

    # Build the new copy first; live data/ is untouched if this fails.
    logger.info(f"Restoring backup '{backup_folder.name}' to data/ ...")
    try:
        shutil.copytree(str(backup_folder), str(staging))
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        shutil.rmtree(str(staging), ignore_errors=True)
        return False

    old = None
    try:
        if data_dir.exists():
            logger.info("Renaming existing data folder...")
            old = project_root / f"data_old_{ts}"
            data_dir.rename(old)  # move live aside
            logger.info(f"Renamed existing data/ to {old.name}")
        staging.rename(data_dir)  # atomic swap (same filesystem)
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        if old is not None and old.exists() and not data_dir.exists():
            old.rename(data_dir)  # rollback
            logger.info("Rolled back to previous data directory.")
        shutil.rmtree(str(staging), ignore_errors=True)
        return False

    if old is not None:
        # Success: remove the moved-aside copy (fixes the data_old_<ts> leak).
        shutil.rmtree(str(old), ignore_errors=True)

    logger.info(f"Data successfully restored from '{backup_folder.name}'.")
    return True


def list_backups(cli):
    """List all available backups with their sizes and dates"""
    project_root = config.project_root()
    backups_dir = project_root / "backups"

    if not backups_dir.exists():
        info("No backups directory found.")
        return

    # Get all backup folders
    backup_folders = sorted(backups_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)

    if not backup_folders:
        info("No backups found.")
        return

    section("Available Backups")

    # Prepare table data
    table_data = []
    for backup in backup_folders:
        if backup.is_dir():
            # Calculate folder size
            total_size = sum(f.stat().st_size for f in backup.rglob("*") if f.is_file())
            size_mb = total_size / (1024 * 1024)

            # Get modification time
            mod_time = datetime.fromtimestamp(backup.stat().st_mtime)
            date_str = mod_time.strftime("%Y-%m-%d %H:%M:%S")

            table_data.append([backup.name, f"{size_mb:.2f} MB", date_str])

    if table_data:
        table(["Backup Name", "Size", "Created"], table_data)
        info(f"Total backups: {len(table_data)}")
        info("Use 'restore <backup_name>' to restore a backup.")
    else:
        info("No backup folders found.")


def clean_database(cli, dry_run: bool = False):
    """Clean database by removing songs that no longer exist in Spotify
    or whose artists have 1 million or more monthly listeners

    Args:
        dry_run: If True, only show what would be removed without actually removing
    """
    try:
        logger.info("Starting database cleaning process...")

        # Get all songs from database
        all_songs = cli.db.get_all_songs()
        if not all_songs:
            logger.info("No songs found in database")
            return

        logger.info(f"Checking {len(all_songs)} songs in database")

        # Initialize Spotify for validation
        spotify = cli.spotify

        # Track statistics
        stats = {
            "total": len(all_songs),
            "checked": 0,
            "not_found": 0,
            "popular_artist": 0,
            "kept": 0,
        }

        # Songs to remove
        songs_to_remove = []

        # Check each song
        from tqdm import tqdm

        for song in tqdm(
            all_songs,
            desc="Checking songs",
            disable=os.getenv("TUNR_INTERACTIVE") == "1",
        ):
            stats["checked"] += 1

            # Skip songs that already have a Spotify URI (optimization)
            if song.spotify_uri:
                # Verify the URI still works
                try:
                    track_info = spotify.get_track_info(song.spotify_uri)
                    if track_info:
                        # Check artist popularity
                        track = spotify.sp.track(song.spotify_uri)
                        if not track.get("artists"):
                            logger.warning(f"No artist data for track: {song.name}")
                            # Continue to search fallback
                        else:
                            artist_id = track["artists"][0]["id"]
                            artist_info = spotify.sp.artist(artist_id)
                            follower_count = (artist_info.get("followers") or {}).get("total", 0)

                            if follower_count >= 1000000:
                                logger.warning(
                                    f"Artist too popular ({follower_count:,} followers): {song.artist}"
                                )
                                songs_to_remove.append(song)
                                stats["popular_artist"] += 1
                                continue

                            stats["kept"] += 1
                            continue
                except Exception as e:
                    # URI no longer valid, continue with search
                    logger.debug(f"URI validation failed for {song.name}: {e}")

            # Search for the song on Spotify
            query = f"track:{song.name} artist:{song.artist}"
            results = spotify.sp.search(query, type="track", limit=1)

            if not results.get("tracks", {}).get("items", []):
                # Song not found in Spotify
                logger.warning(f"Song not found in Spotify: {song.name} by {song.artist}")
                songs_to_remove.append(song)
                stats["not_found"] += 1
            else:
                # Song found, update URI if needed
                track = results.get("tracks", {}).get("items", [])[0]
                if not song.spotify_uri:
                    song.spotify_uri = track["uri"]
                    cli.db._save_state()  # Save the updated URI

                # Check artist popularity
                if not track.get("artists"):
                    logger.warning(f"No artist data for track: {song.name}")
                    stats["kept"] += 1
                else:
                    artist_id = track["artists"][0]["id"]
                    artist_info = spotify.sp.artist(artist_id)
                    follower_count = (artist_info.get("followers") or {}).get("total", 0)

                    if follower_count >= 1000000:
                        logger.warning(
                            f"Artist too popular ({follower_count:,} followers): {song.artist}"
                        )
                        songs_to_remove.append(song)
                        stats["popular_artist"] += 1
                    else:
                        stats["kept"] += 1

        # Remove songs if not in dry run mode
        if songs_to_remove:
            if dry_run:
                logger.info(f"DRY RUN: Would remove {len(songs_to_remove)} songs")
                for song in songs_to_remove:
                    logger.info(f"  - {song.name} by {song.artist}")
            else:
                logger.info(f"Removing {len(songs_to_remove)} songs from database")
                for song in songs_to_remove:
                    logger.info(f"Removing: {song.name} by {song.artist}")
                    cli.db.remove_song(song.id)

        # Display cleaning statistics
        section("Database Cleaning Results")
        key_value_table(
            [
                ["Total songs checked", stats["checked"]],
                ["Songs kept", stats["kept"]],
                ["Songs not found in Spotify", stats["not_found"]],
                ["Songs with popular artists (>=1M followers)", stats["popular_artist"]],
            ]
        )
        if dry_run and songs_to_remove:
            info("DRY RUN: No songs were actually removed.")
        elif songs_to_remove:
            info(f"Songs removed: {len(songs_to_remove)}")
        else:
            info("No songs needed to be removed.")

    except Exception as e:
        logger.error(f"Error cleaning database: {str(e)}")
        logger.debug("Full error:", exc_info=True)
