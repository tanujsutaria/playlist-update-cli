import logging
import json
import csv
import shutil
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from typing import Optional, Dict, List

from rich.logging import RichHandler

from arg_parse import parse_args
from models import Song
from db_manager import DatabaseManager
from spotify_manager import SpotifyManager, get_cached_token_info, refresh_cached_token
from rotation_manager import RotationManager
from scoring import ScoreConfig
from ui import console, section, subsection, table, key_value_table, info, warning

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(
            console=console,
            show_time=True,
            show_level=True,
            show_path=False,
            rich_tracebacks=True,
            markup=False,
        )
    ],
)
logger = logging.getLogger(__name__)

class PlaylistCLI:
    def __init__(self):
        # Get the project root directory
        project_root = Path(__file__).parent.parent
        load_dotenv(project_root / 'config' / '.env')
        
        # Initialize managers as needed
        self._db = None
        self._spotify = None
        self._rotation_managers = {}

    @property
    def db(self) -> DatabaseManager:
        """Lazy initialization of DatabaseManager"""
        if self._db is None:
            logger.info("Initializing database manager...")
            self._db = DatabaseManager()
            logger.info(f"Loaded {len(self._db.get_all_songs())} songs from database")
        return self._db

    @property
    def spotify(self) -> SpotifyManager:
        """Lazy initialization of SpotifyManager"""
        if self._spotify is None:
            self._spotify = SpotifyManager()
        return self._spotify

    def _get_rotation_manager(self, playlist_name: str) -> RotationManager:
        """Get or create a rotation manager for a playlist"""
        if playlist_name not in self._rotation_managers:
            self._rotation_managers[playlist_name] = RotationManager(
                playlist_name=playlist_name,
                db=self.db,
                spotify=self.spotify
            )
        return self._rotation_managers[playlist_name]

    def import_songs(self, file_path: str):
        """Import songs from a file into the database
        
        Supports both .txt and .csv files with format: song_name,artist_name
        Lines starting with # are treated as comments
        
        Validates that:
        1. The song exists in Spotify
        2. The artist has less than 1 million monthly listeners
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return
        
        if not path.suffix.lower() in ['.txt', '.csv']:
            logger.warning(f"File extension {path.suffix} not recognized. Expected .txt or .csv")
            logger.warning("Attempting to process file anyway...")

        # Initialize Spotify for validation
        try:
            # Ensure Spotify manager is initialized
            spotify = self.spotify
            logger.info("Spotify connection established for song validation")
        except Exception as e:
            logger.error(f"Failed to initialize Spotify for validation: {str(e)}")
            return

        # Track statistics
        stats = {
            "total": 0,
            "added": 0,
            "already_exists": 0,
            "not_found": 0,
            "popular_artist": 0,
            "error": 0
        }

        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        stats["total"] += 1
                        
                        # Skip empty lines and comments
                        if not line.strip() or line.startswith('#'):
                            stats["total"] -= 1  # Don't count comments/empty lines
                            continue
                            
                        # Split and take only first two columns (name, artist)
                        parts = [x.strip().lower() for x in line.split(',')]
                        if len(parts) < 2:
                            logger.warning(f"Line {line_num}: Skipping invalid line (not enough columns): {line.strip()}")
                            stats["error"] += 1
                            continue
                            
                        name, artist = parts[0], parts[1]
                        
                        # Basic validation
                        if not name or not artist:
                            logger.warning(f"Line {line_num}: Skipping invalid line (empty name or artist): {line.strip()}")
                            stats["error"] += 1
                            continue
                        
                        logger.info(f"Validating: {name} by {artist}")
                        
                        # Step 1: Check if song exists in Spotify
                        query = f"track:{name} artist:{artist}"
                        results = spotify.sp.search(query, type='track', limit=1)
                        
                        if not results['tracks']['items']:
                            logger.warning(f"Line {line_num}: Song not found in Spotify: {name} by {artist}")
                            stats["not_found"] += 1
                            continue
                        
                        track = results['tracks']['items'][0]
                        track_uri = track['uri']
                        
                        # Step 2: Check artist popularity
                        artist_id = track['artists'][0]['id']
                        artist_info = spotify.sp.artist(artist_id)
                        
                        # Get follower count
                        follower_count = artist_info['followers']['total']
                        
                        if follower_count >= 1000000:
                            logger.warning(f"Line {line_num}: Artist too popular ({follower_count:,} followers): {artist}")
                            stats["popular_artist"] += 1
                            continue
                        
                        # Song passed validation, add to database
                        song = Song(
                            id=f"{artist}|||{name}",
                            name=name,
                            artist=artist,
                            spotify_uri=track_uri,
                            first_added=datetime.now()
                        )
                        
                        if self.db.add_song(song):
                            logger.info(f"Added: {song.name} by {song.artist}")
                            stats["added"] += 1
                        else:
                            logger.info(f"Skipped (already exists): {song.name} by {song.artist}")
                            stats["already_exists"] += 1
                            
                    except Exception as e:
                        logger.warning(f"Line {line_num}: Error processing line: {str(e)}")
                        stats["error"] += 1
                        continue
            
            # Display import statistics
            section("Import Summary")
            key_value_table(
                [
                    ["Total entries processed", stats["total"]],
                    ["Songs added", stats["added"]],
                    ["Songs already in database", stats["already_exists"]],
                    ["Songs not found in Spotify", stats["not_found"]],
                    ["Artists with >=1M followers", stats["popular_artist"]],
                    ["Errors", stats["error"]],
                ]
            )
                    
        except Exception as e:
            logger.error(f"Error importing songs: {str(e)}")

    def update_playlist(
        self,
        playlist_name: str,
        song_count: int = 10,
        fresh_days: int = 30,
        dry_run: bool = False,
        score_strategy: str = "local",
        query: Optional[str] = None
    ):
        """Update a playlist with new songs by deleting and recreating it
        
        Args:
            playlist_name: Name of the playlist to update
            song_count: Number of songs to include in the playlist
            fresh_days: Prioritize songs not listened to in this many days
            dry_run: If True, preview selection without updating Spotify
            score_strategy: Match scoring strategy (local, web, hybrid)
            query: Optional theme query for building a playlist profile
        """
        try:
            rm = self._get_rotation_manager(playlist_name)
            score_config = ScoreConfig(strategy=score_strategy, query=query)
            
            # Select songs
            logger.info(f"Selecting {song_count} songs (prioritizing songs not used in {fresh_days} days)...")
            songs = rm.select_songs_for_today(
                count=song_count,
                fresh_days=fresh_days,
                score_config=score_config
            )

            if dry_run:
                section("Dry Run", "Selected Songs")
                table_data = [[i, s.name, s.artist] for i, s in enumerate(songs, 1)]
                table(["#", "Song", "Artist"], table_data)
                info(f"Total selected: {len(songs)}")
                return
            
            # Update playlist
            logger.info("Updating playlist...")
            if rm.update_playlist(songs):
                # Show detailed stats
                stats = rm.get_rotation_stats()
                section("Playlist Update Stats")
                key_value_table(
                    [
                        ["Total songs in database", stats.total_songs],
                        ["Songs used so far", stats.unique_songs_used],
                        ["Songs never used", stats.songs_never_used],
                        ["Total generations", stats.generations_count],
                        ["Complete rotation achieved", stats.complete_rotation_achieved],
                    ]
                )
            else:
                logger.error("Failed to update playlist")
            
        except Exception as e:
            logger.error(f"Error updating playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def _show_detailed_stats(self, rm: RotationManager):
        """Show detailed statistics about the playlist"""
        stats = rm.get_rotation_stats()
        recent_songs = rm.get_recent_songs(days=7)
        
        # Basic stats
        section("Playlist Statistics")
        stats_table = [
            ["Total Songs", stats.total_songs],
            ["Songs Used", stats.unique_songs_used],
            ["Songs Never Used", stats.songs_never_used],
            ["Total generations", stats.generations_count],
            ["Complete Rotation", "Yes" if stats.complete_rotation_achieved else "No"],
            ["Rotation Progress", f"{(stats.unique_songs_used/stats.total_songs)*100:.1f}%"],
            ["Current Strategy", stats.current_strategy],
        ]
        key_value_table(stats_table)
        
        # Recent activity
        section("Recent Activity", "Last 7 Days")
        recent_table = []
        for date, songs in recent_songs.items():
            recent_table.append([
                date,
                len(songs),
                ", ".join(f"{s.name} by {s.artist}"[:40] for s in songs[:3]) + 
                ("..." if len(songs) > 3 else "")
            ])
        table(
            ["Date", "Songs Added", "Sample Songs"],
            recent_table,
        )

    def restore_previous_rotation(self, playlist_name: str, offset: int = -1):
        """
        Restore a playlist to a previous rotation by going 'offset' generations back.
        If out of range, inform user and do nothing.
        """
        try:
            rm = self._get_rotation_manager(playlist_name)

            # Calculate the generation index to restore
            new_gen_index = rm.history.current_generation + offset
            if new_gen_index < 0 or new_gen_index >= len(rm.history.generations):
                logger.error(
                    f"Offset {offset} is out of bounds. Valid range: 0 to {-(len(rm.history.generations))} "
                    f"(or up to {len(rm.history.generations) - 1} if you prefer positive indexes)."
                )
                return

            # Retrieve songs from that generation
            old_song_ids = rm.history.generations[new_gen_index]
            songs_to_restore = []
            for sid in old_song_ids:
                song = self.db.get_song_by_id(sid)
                if song:
                    songs_to_restore.append(song)

            if not songs_to_restore:
                logger.info(f"No songs found in generation index {new_gen_index}.")
                return

            # Update playlist with these songs
            logger.info(f"Restoring playlist '{playlist_name}' to generation index {new_gen_index}...")
            # Don't record a new generation when reverting
            success = rm.update_playlist(songs_to_restore, record_generation=False)
            if success:
                rm.history.current_generation = new_gen_index
                rm._save_history()
                logger.info("Playlist successfully restored to the requested generation.")
            else:
                logger.error("Failed to restore playlist.")
        except Exception as e:
            logger.error(f"Error restoring previous rotation: {str(e)}")
            logger.debug("Full error:", exc_info=True)
            
    def list_rotations(self, playlist_name: str, generations: str = "3"):
        """List rotations for a given playlist
        
        Args:
            playlist_name: Name of the playlist
            generations: Number of generations to list, or 'all' for all generations
        """
        try:
            rm = self._get_rotation_manager(playlist_name)
            if not rm.history.generations:
                logger.info(f"No rotations found for playlist '{playlist_name}'.")
                return

            # Determine how many generations to show
            gens_str = generations.lower()
            all_gens = rm.history.generations
            if gens_str == "all":
                limit = len(all_gens)
                logger.info(f"Showing all {limit} generations")
            else:
                try:
                    limit = int(gens_str)
                    if limit <= 0:
                        logger.info("Number of generations must be positive.")
                        return
                except ValueError:
                    logger.info("Invalid --generations value. Must be an integer or 'all'.")
                    return
                
            # Handle out-of-bounds
            if limit > len(all_gens):
                logger.info(f"Requested {limit} generations, but only {len(all_gens)} available.")
                limit = len(all_gens)
                
            # Get the most recent N generations
            selected_gens = all_gens[-limit:]
            
            section("Rotations", f"Playlist: {playlist_name}")
            # Calculate the starting index for proper numbering
            start_idx = len(all_gens) - limit + 1
            for i, gen_songs in enumerate(selected_gens, start=start_idx):
                subsection(f"Generation {i}")
                songs = []
                for song_id in gen_songs:
                    song = self.db.get_song_by_id(song_id)
                    if song:
                        songs.append(song)
                
                # Display songs in a tabular format
                if songs:
                    table_data = []
                    for j, song in enumerate(songs, 1):
                        table_data.append([j, song.name, song.artist])
                    table(["#", "Song", "Artist"], table_data)
                else:
                    logger.info("   No songs found for this generation.")
                    
            info(f"Current generation: {rm.history.current_generation + 1}")
        except Exception as e:
            logger.error(f"Error listing rotations: {str(e)}")
            logger.debug("Full error:", exc_info=True)
            
    def view_playlist(self, playlist_name: str):
        """View current playlist contents - only needs Spotify"""
        try:
            # Only initialize Spotify manager
            tracks = self.spotify.get_playlist_tracks(playlist_name)
            
            section("Current Playlist", playlist_name)
            
            if not tracks:
                warning("Playlist is empty.")
                return
            
            # Prepare table data
            table_data = []
            for i, track in enumerate(tracks, 1):
                added_date = track.get('added_at', '')
                if added_date:
                    try:
                        # Convert from ISO format to YYYY-MM-DD
                        added_date = added_date.split('T')[0]
                    except:
                        added_date = 'Unknown'
                
                table_data.append([
                    i,
                    track['name'],
                    track['artist'],
                    added_date or 'Unknown'
                ])
            
            table(
                ["#", "Song", "Artist", "Added Date"],
                table_data,
            )
            
            # Show summary
            info(f"Total tracks: {len(tracks)}")
            
        except Exception as e:
            logger.error(f"Error viewing playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def show_stats(self, playlist_name: Optional[str] = None):
        """Show database and playlist statistics"""
        try:
            # Database stats
            db_stats = self.db.get_stats()
            section("Database Stats")
            key_value_table(
                [
                    ["Total songs", db_stats["total_songs"]],
                    ["Embedding dimensions", db_stats["embedding_dimensions"]],
                    ["Storage size (MB)", f"{db_stats['storage_size_mb']:.2f}"],
                ]
            )

            # Playlist stats if specified
            if playlist_name:
                rm = self._get_rotation_manager(playlist_name)
                stats = rm.get_rotation_stats()
                
                section("Playlist Stats", playlist_name)
                key_value_table(
                    [
                        ["Total songs", stats.total_songs],
                        ["Songs used so far", stats.unique_songs_used],
                        ["Songs never used", stats.songs_never_used],
                        ["Total generations", stats.generations_count],
                        ["Complete rotation achieved", stats.complete_rotation_achieved],
                    ]
                )
                
                # Show recent generations
                recent_gens = rm.get_recent_generations(count=5)
                if recent_gens:
                    section("Recent Generations")
                    for i, gen_songs in enumerate(recent_gens, 1):
                        gen_index = stats.generations_count - len(recent_gens) + i
                        subsection(f"Generation {gen_index}")
                        table_data = []
                        for j, song in enumerate(gen_songs, 1):
                            table_data.append([j, song.name, song.artist])
                        table(["#", "Song", "Artist"], table_data)
                else:
                    info("No generation history found.")
                
        except Exception as e:
            logger.error(f"Error showing stats: {str(e)}")

    def export_stats(self, playlist_name: Optional[str], export_format: str, output_file: Optional[str]):
        """Export database and playlist stats to a file."""
        db_stats = self.db.get_stats()
        export_payload = {
            "database": db_stats,
            "playlist": None,
            "generated_at": datetime.now().isoformat()
        }

        if playlist_name:
            rm = self._get_rotation_manager(playlist_name)
            stats = rm.get_rotation_stats()
            export_payload["playlist"] = {
                "name": playlist_name,
                "total_songs": stats.total_songs,
                "unique_songs_used": stats.unique_songs_used,
                "songs_never_used": stats.songs_never_used,
                "generations_count": stats.generations_count,
                "complete_rotation_achieved": stats.complete_rotation_achieved,
                "current_strategy": stats.current_strategy
            }

        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = "json" if export_format == "json" else "csv"
            output_file = f"stats_export_{timestamp}.{suffix}"

        if export_format == "json":
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(export_payload, f, indent=2)
            logger.info(f"Exported stats to {output_file}")
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
        logger.info(f"Exported stats to {output_file}")

    def sync_playlist(self, playlist_name: str):
        """Sync a playlist with all songs in the database by adding new songs and removing songs no longer in the database"""
        try:
            logger.info(f"Starting database sync with playlist '{playlist_name}'...")
            
            # Get all songs from database
            all_songs = self.db.get_all_songs()
            if not all_songs:
                logger.error("No songs found in database")
                return
            
            logger.info(f"Found {len(all_songs)} songs in database")
            
            # Get existing tracks in the playlist
            existing_tracks = self.spotify.get_playlist_tracks(playlist_name)
            if existing_tracks is None:
                logger.error(f"Failed to retrieve tracks from playlist '{playlist_name}'")
                return
                
            # Create a set of existing URIs for quick lookup
            existing_uris = set()
            for track in existing_tracks:
                if 'uri' in track:
                    existing_uris.add(track['uri'])
            
            logger.info(f"Found {len(existing_uris)} existing tracks in playlist")
            
            # Create a set of database URIs for quick lookup
            database_uris = set()
            songs_to_add = []
            
            for song in all_songs:
                # If song doesn't have a URI yet, try to find it
                if not song.spotify_uri:
                    song.spotify_uri = self.spotify.search_song(song)
                    
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
                add_success = self.spotify.append_to_playlist(playlist_name, songs_to_add)
                if add_success:
                    logger.info(f"Successfully added {len(songs_to_add)} new songs to playlist '{playlist_name}'")
                else:
                    logger.error("Failed to add new songs to playlist")
            else:
                logger.info("No new songs to add")
            
            # Remove songs if needed
            if uris_to_remove:
                remove_success = self.spotify.remove_from_playlist(playlist_name, list(uris_to_remove))
                if remove_success:
                    logger.info(f"Successfully removed {len(uris_to_remove)} songs from playlist '{playlist_name}'")
                else:
                    logger.error("Failed to remove songs from playlist")
            else:
                logger.info("No songs to remove")
                
            if not songs_to_add and not uris_to_remove:
                logger.info("Playlist is already in sync with the database")
            
        except Exception as e:
            logger.error(f"Error syncing playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def extract_playlist(self, playlist_name: str, output_file: str = None):
        """Extract playlist contents to a CSV file"""
        try:
            # Get tracks from playlist
            tracks = self.spotify.get_playlist_tracks(playlist_name)
            
            if not tracks:
                logger.error(f"No tracks found in playlist '{playlist_name}'")
                return False
            
            # Generate output filename if not provided
            if output_file is None:
                output_file = f"{playlist_name}_songs.csv"
            
            # Ensure file extension is .csv
            if not output_file.endswith('.csv'):
                output_file += '.csv'
            
            # Write to file
            logger.info(f"Writing {len(tracks)} tracks to {output_file}")
            with open(output_file, 'w', encoding='utf-8') as f:
                for track in tracks:
                    f.write(f"{track['name']},{track['artist']}\n")
            
            logger.info(f"Successfully exported playlist to {output_file}")
            return True
            
        except Exception as e:
            logger.error(f"Error extracting playlist: {str(e)}")
            return False
            
    def backup_data(self, backup_name: Optional[str] = None):
        """
        Create a backup of the entire data/ folder in a new backups/ directory
        at the same level as src/.
        """
        project_root = Path(__file__).parent.parent
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
        shutil.copytree(str(data_dir), str(backup_folder))
        logger.info(f"Backup '{backup_folder.name}' created successfully.")

    def restore_data(self, backup_name: str):
        """
        Restore data/ from the chosen backup in backups/.
        """
        project_root = Path(__file__).parent.parent
        data_dir = project_root / "data"
        backups_dir = project_root / "backups"
        backup_folder = backups_dir / backup_name

        if not backup_folder.exists():
            logger.error(f"No such backup folder: '{backup_folder.name}'")
            return

        # Rename or remove current data/ before restoring
        if data_dir.exists():
            logger.info("Renaming existing data folder...")
            old_data_dir = project_root / f"data_old_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            data_dir.rename(old_data_dir)
            logger.info(f"Renamed existing data/ to {old_data_dir.name}")

        logger.info(f"Restoring backup '{backup_folder.name}' to data/ ...")
        shutil.copytree(str(backup_folder), str(data_dir))
        logger.info(f"Data successfully restored from '{backup_folder.name}'.")

    def list_backups(self):
        """List all available backups with their sizes and dates"""
        project_root = Path(__file__).parent.parent
        backups_dir = project_root / "backups"

        if not backups_dir.exists():
            logger.info("No backups directory found.")
            return

        # Get all backup folders
        backup_folders = sorted(backups_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)

        if not backup_folders:
            logger.info("No backups found.")
            return

        section("Available Backups")

        # Prepare table data
        table_data = []
        for backup in backup_folders:
            if backup.is_dir():
                # Calculate folder size
                total_size = sum(f.stat().st_size for f in backup.rglob('*') if f.is_file())
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
            logger.info("No backup folders found.")

    def clean_database(self, dry_run: bool = False):
        """Clean database by removing songs that no longer exist in Spotify
        or whose artists have 1 million or more monthly listeners
        
        Args:
            dry_run: If True, only show what would be removed without actually removing
        """
        try:
            logger.info("Starting database cleaning process...")
            
            # Get all songs from database
            all_songs = self.db.get_all_songs()
            if not all_songs:
                logger.info("No songs found in database")
                return
            
            logger.info(f"Checking {len(all_songs)} songs in database")
            
            # Initialize Spotify for validation
            spotify = self.spotify
            
            # Track statistics
            stats = {
                "total": len(all_songs),
                "checked": 0,
                "not_found": 0,
                "popular_artist": 0,
                "kept": 0
            }
            
            # Songs to remove
            songs_to_remove = []
            
            # Check each song
            from tqdm import tqdm
            for song in tqdm(all_songs, desc="Checking songs"):
                stats["checked"] += 1
                
                # Skip songs that already have a Spotify URI (optimization)
                if song.spotify_uri:
                    # Verify the URI still works
                    try:
                        track_info = spotify.get_track_info(song.spotify_uri)
                        if track_info:
                            # Check artist popularity
                            track = spotify.sp.track(song.spotify_uri)
                            artist_id = track['artists'][0]['id']
                            artist_info = spotify.sp.artist(artist_id)
                            follower_count = artist_info['followers']['total']
                            
                            if follower_count >= 1000000:
                                logger.warning(f"Artist too popular ({follower_count:,} followers): {song.artist}")
                                songs_to_remove.append(song)
                                stats["popular_artist"] += 1
                                continue
                            
                            stats["kept"] += 1
                            continue
                    except Exception:
                        # URI no longer valid, continue with search
                        pass
                
                # Search for the song on Spotify
                query = f"track:{song.name} artist:{song.artist}"
                results = spotify.sp.search(query, type='track', limit=1)
                
                if not results['tracks']['items']:
                    # Song not found in Spotify
                    logger.warning(f"Song not found in Spotify: {song.name} by {song.artist}")
                    songs_to_remove.append(song)
                    stats["not_found"] += 1
                else:
                    # Song found, update URI if needed
                    track = results['tracks']['items'][0]
                    if not song.spotify_uri:
                        song.spotify_uri = track['uri']
                        self.db._save_state()  # Save the updated URI
                    
                    # Check artist popularity
                    artist_id = track['artists'][0]['id']
                    artist_info = spotify.sp.artist(artist_id)
                    follower_count = artist_info['followers']['total']
                    
                    if follower_count >= 1000000:
                        logger.warning(f"Artist too popular ({follower_count:,} followers): {song.artist}")
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
                        self.db.remove_song(song.id)
            
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

    def plan_playlist(
        self,
        playlist_name: str,
        song_count: int,
        fresh_days: int,
        generations: int,
        score_strategy: str = "local",
        query: Optional[str] = None
    ):
        """Preview future generations without updating Spotify."""
        try:
            rm = self._get_rotation_manager(playlist_name)
            score_config = ScoreConfig(strategy=score_strategy, query=query)
            plans = rm.simulate_generations(
                count=song_count,
                fresh_days=fresh_days,
                generations=generations,
                score_config=score_config
            )
            section("Plan", f"{generations} future generations for '{playlist_name}'")
            for idx, songs in enumerate(plans, 1):
                subsection(f"Generation {idx}")
                table_data = [[i, s.name, s.artist] for i, s in enumerate(songs, 1)]
                table(["#", "Song", "Artist"], table_data)
        except Exception as e:
            logger.error(f"Error planning playlist: {str(e)}")

    def diff_playlist(
        self,
        playlist_name: str,
        song_count: int,
        fresh_days: int,
        score_strategy: str = "local",
        query: Optional[str] = None
    ):
        """Show playlist changes before applying update."""
        try:
            rm = self._get_rotation_manager(playlist_name)
            logger.info(f"Selecting {song_count} songs for diff (fresh_days={fresh_days})...")
            score_config = ScoreConfig(strategy=score_strategy, query=query)
            selected = rm.select_songs_for_today(
                count=song_count,
                fresh_days=fresh_days,
                score_config=score_config
            )

            current_tracks = self.spotify.get_playlist_tracks(playlist_name)
            current_uris = {t["uri"] for t in current_tracks if t.get("uri")}

            selected_uris = set()
            for song in selected:
                if not song.spotify_uri:
                    song.spotify_uri = self.spotify.search_song(song)
                if song.spotify_uri:
                    selected_uris.add(song.spotify_uri)

            to_add = selected_uris - current_uris
            to_remove = current_uris - selected_uris

            section("Playlist Diff")
            key_value_table(
                [
                    ["Would add", f"{len(to_add)} tracks"],
                    ["Would remove", f"{len(to_remove)} tracks"],
                ]
            )

            if to_add:
                add_sample = []
                for uri in list(to_add)[:10]:
                    add_sample.append([uri])
                subsection("Sample additions (URIs)")
                table(["URI"], add_sample)

            if to_remove:
                remove_sample = []
                for uri in list(to_remove)[:10]:
                    remove_sample.append([uri])
                subsection("Sample removals (URIs)")
                table(["URI"], remove_sample)
        except Exception as e:
            logger.error(f"Error generating playlist diff: {str(e)}")

    def auth_status(self):
        """Show Spotify auth token status without triggering auth flow."""
        token_info = get_cached_token_info()
        if not token_info:
            logger.info("No cached Spotify token found.")
            return

        expires_at = token_info.get("expires_at")
        expires_in = token_info.get("expires_in")
        scope = token_info.get("scope")

        section("Spotify Auth Status")
        rows = []
        if expires_at:
            expires_dt = datetime.fromtimestamp(expires_at)
            rows.append(["Expires at", expires_dt.isoformat()])
        if expires_in:
            rows.append(["Expires in (seconds)", expires_in])
        if scope:
            rows.append(["Scopes", scope])

        if rows:
            key_value_table(rows)
        else:
            info("Token metadata not available.")

    def auth_refresh(self):
        """Refresh Spotify auth token if possible."""
        refreshed = refresh_cached_token()
        if not refreshed:
            logger.info("No token refreshed. You may need to re-authenticate using any Spotify command.")
            return
        expires_at = refreshed.get("expires_at")
        if expires_at:
            expires_dt = datetime.fromtimestamp(expires_at)
            logger.info(f"Token refreshed. New expiry: {expires_dt.isoformat()}")
        else:
            logger.info("Token refreshed.")

def main():
    cli = PlaylistCLI()
    command, args = parse_args()
    
    if not command:
        return 1

    try:
        if command == 'import':
            cli.import_songs(args.file)
        elif command == 'update':
            cli.update_playlist(
                args.playlist,
                args.count,
                args.fresh_days,
                args.dry_run,
                args.score_strategy,
                args.query
            )
        elif command == 'stats':
            if args.export:
                cli.export_stats(args.playlist, args.export, args.output)
            else:
                cli.show_stats(args.playlist)
        elif command == 'view':
            cli.view_playlist(args.playlist)
        elif command == 'sync':
            cli.sync_playlist(args.playlist)
        elif command == 'extract':
            cli.extract_playlist(args.playlist, args.output)
        elif command == 'plan':
            cli.plan_playlist(
                args.playlist,
                args.count,
                args.fresh_days,
                args.generations,
                args.score_strategy,
                args.query
            )
        elif command == 'diff':
            cli.diff_playlist(
                args.playlist,
                args.count,
                args.fresh_days,
                args.score_strategy,
                args.query
            )
        elif command == 'clean':
            cli.clean_database(args.dry_run)
        elif command == 'backup':
            cli.backup_data(args.backup_name)
        elif command == 'restore':
            cli.restore_data(args.backup_name)
        elif command == 'restore-previous-rotation':
            # New command handling
            cli.restore_previous_rotation(args.playlist, args.offset)
        elif command == 'list-rotations':
            cli.list_rotations(args.playlist, args.generations)
        elif command == 'list-backups':
            cli.list_backups()
        elif command == 'auth-status':
            cli.auth_status()
        elif command == 'auth-refresh':
            cli.auth_refresh()
    except Exception as e:
        logger.error(f"Command failed: {str(e)}")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
