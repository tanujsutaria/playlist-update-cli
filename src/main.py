import argparse
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from tqdm import tqdm
from tabulate import tabulate
from dotenv import load_dotenv

from models import Song
from db_manager import DatabaseManager
from spotify_manager import SpotifyManager
from rotation_manager import RotationManager

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PlaylistCLI:
    def __init__(self):
        load_dotenv('config/.env')
        logger.info("Initializing database and Spotify managers...")
        self.db = DatabaseManager()
        self.spotify = SpotifyManager()
        self._rotation_managers = {}  # Cache for rotation managers

    def _get_rotation_manager(self, playlist_name: str) -> RotationManager:
        """Get or create a rotation manager for a playlist"""
        if playlist_name not in self._rotation_managers:
            self._rotation_managers[playlist_name] = RotationManager(playlist_name)
        return self._rotation_managers[playlist_name]

    def import_songs(self, file_path: str):
        """Import songs from a file into the database"""
        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        # Skip empty lines and comments
                        if not line.strip() or line.startswith('#'):
                            continue
                            
                        # Split and take only first two columns (name, artist)
                        parts = [x.strip().lower() for x in line.split(',')]
                        if len(parts) < 2:
                            logger.warning(f"Line {line_num}: Skipping invalid line (not enough columns): {line.strip()}")
                            continue
                            
                        name, artist = parts[0], parts[1]
                        
                        # Basic validation
                        if not name or not artist:
                            logger.warning(f"Line {line_num}: Skipping invalid line (empty name or artist): {line.strip()}")
                            continue
                            
                        song = Song(
                            id=f"{artist}|||{name}",
                            name=name,
                            artist=artist
                        )
                        
                        if self.db.add_song(song):
                            logger.info(f"Added: {song.name} by {song.artist}")
                        else:
                            logger.info(f"Skipped (already exists): {song.name} by {song.artist}")
                            
                    except Exception as e:
                        logger.warning(f"Line {line_num}: Error processing line: {str(e)}")
                        continue
                    
        except Exception as e:
            logger.error(f"Error importing songs: {str(e)}")

    def update_playlist(self, playlist_name: str, song_count: int = 30):
        """Update a playlist with new songs"""
        try:
            rm = self._get_rotation_manager(playlist_name)
            
            # Select songs with progress bar
            logger.info("Selecting songs...")
            songs = rm.select_songs_for_today(count=song_count)
            
            # Update with progress bar
            logger.info("Updating playlist...")
            with tqdm(total=len(songs), desc="Adding tracks") as pbar:
                rm.update_playlist(songs, progress_callback=lambda x: pbar.update(1))
            
            # Show detailed stats
            stats = rm.get_rotation_stats()
            logger.info("\nPlaylist Update Stats:")
            logger.info(f"Total songs in database: {stats.total_songs}")
            logger.info(f"Songs used so far: {stats.unique_songs_used}")
            logger.info(f"Songs never used: {stats.songs_never_used}")
            logger.info(f"Total generations: {stats.generations_count}")
            logger.info(f"Complete rotation achieved: {stats.complete_rotation_achieved}")
            
        except Exception as e:
            logger.error(f"Error updating playlist: {str(e)}")

    def _show_detailed_stats(self, rm: RotationManager):
        """Show detailed statistics about the playlist"""
        stats = rm.get_rotation_stats()
        recent_songs = rm.get_recent_songs(days=7)
        
        # Basic stats
        logger.info("\n=== Playlist Statistics ===")
        stats_table = [
            ["Total Songs", stats.total_songs],
            ["Songs Used", stats.unique_songs_used],
            ["Songs Never Used", stats.songs_never_used],
            ["Total generations", stats.generations_count],
            ["Complete Rotation", "Yes" if stats.complete_rotation_achieved else "No"],
            ["Rotation Progress", f"{(stats.unique_songs_used/stats.total_songs)*100:.1f}%"],
            ["Current Strategy", stats.current_strategy]
        ]
        print(tabulate(stats_table, tablefmt="grid"))
        
        # Recent activity
        logger.info("\n=== Recent Activity (Last 7 Days) ===")
        recent_table = []
        for date, songs in recent_songs.items():
            recent_table.append([
                date,
                len(songs),
                ", ".join(f"{s.name} by {s.artist}"[:40] for s in songs[:3]) + 
                ("..." if len(songs) > 3 else "")
            ])
        print(tabulate(recent_table, 
                      headers=["Date", "Songs Added", "Sample Songs"],
                      tablefmt="grid"))

    def view_playlist(self, playlist_name: str):
        """View current playlist contents"""
        try:
            tracks = self.spotify.get_playlist_tracks(playlist_name)
            
            logger.info(f"\n=== Current Playlist: {playlist_name} ===")
            
            if not tracks:
                print("\nPlaylist is empty!")
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
            
            print(tabulate(table_data,
                         headers=["#", "Song", "Artist", "Added Date"],
                         tablefmt="grid"))
            
            # Show summary
            print(f"\nTotal tracks: {len(tracks)}")
            
        except Exception as e:
            logger.error(f"Error viewing playlist: {str(e)}")
            logger.debug("Full error:", exc_info=True)

    def show_stats(self, playlist_name: Optional[str] = None):
        """Show database and playlist statistics"""
        try:
            # Database stats
            db_stats = self.db.get_stats()
            logger.info("\n=== Database Stats ===")
            logger.info(f"Total songs: {db_stats['total_songs']}")
            logger.info(f"Embedding dimensions: {db_stats['embedding_dimensions']}")
            logger.info(f"Storage size: {db_stats['storage_size_mb']:.2f} MB")

            # Playlist stats if specified
            if playlist_name:
                rm = self._get_rotation_manager(playlist_name)
                stats = rm.get_rotation_stats()
                
                logger.info(f"\n=== Playlist '{playlist_name}' Stats ===")
                logger.info(f"Total songs: {stats.total_songs}")
                logger.info(f"Songs used so far: {stats.unique_songs_used}")
                logger.info(f"Songs never used: {stats.songs_never_used}")
                logger.info(f"Total generations: {stats.generations_count}")
                logger.info(f"Complete rotation achieved: {stats.complete_rotation_achieved}")
                
                # Show recent generations
                recent_gens = rm.get_recent_generations(count=5)
                if recent_gens:
                    logger.info("\n=== Recent Generations ===")
                    for i, gen_songs in enumerate(recent_gens, 1):
                        logger.info(f"\nGeneration {stats.generations_count - len(recent_gens) + i}:")
                        for j, song in enumerate(gen_songs, 1):
                            logger.info(f"{j:2d}. {song.name} by {song.artist}")
                else:
                    logger.info("\nNo generation history found")
                
        except Exception as e:
            logger.error(f"Error showing stats: {str(e)}")

    def sync_playlist(self, playlist_name: str):
        """Sync a playlist with all songs in the database"""
        
        try:
            logger.info(f"Starting full database sync with playlist '{playlist_name}'...")
            
            # Get all songs from database
            all_songs = self.db.get_all_songs()
            if not all_songs:
                logger.error("No songs found in database")
                return
            
            logger.info(f"Found {len(all_songs)} songs in database")
            
            # Update with progress bar
            logger.info("Updating playlist...")
            with tqdm(total=len(all_songs), desc="Adding tracks") as pbar:
                success = self.spotify.refresh_playlist(playlist_name, all_songs, 
                                                      progress_callback=lambda x: pbar.update(1))
            
            if success:
                logger.info(f"Successfully synced playlist with all {len(all_songs)} songs")
            else:
                logger.error("Failed to sync playlist")
            
        except Exception as e:
            logger.error(f"Error syncing playlist: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description="Spotify Playlist Manager CLI")
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Import command
    import_parser = subparsers.add_parser('import', help='Import songs from a file')
    import_parser.add_argument('file', help='Path to the input file')

    # Update command
    update_parser = subparsers.add_parser('update', help='Update a playlist')
    update_parser.add_argument('playlist', help='Name of the playlist')
    update_parser.add_argument('--count', type=int, default=30, help='Number of songs to include')

    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Show statistics')
    stats_parser.add_argument('--playlist', help='Playlist name (optional)', default=None)

    # New view command
    view_parser = subparsers.add_parser('view', help='View current playlist contents')
    view_parser.add_argument('playlist', help='Name of the playlist')

    # New sync command
    sync_parser = subparsers.add_parser('sync', help='Sync entire database to a playlist')
    sync_parser.add_argument('playlist', help='Name of the playlist')

    args = parser.parse_args()
    cli = PlaylistCLI()

    try:
        if args.command == 'import':
            cli.import_songs(args.file)
        elif args.command == 'update':
            cli.update_playlist(args.playlist, args.count)
        elif args.command == 'stats':
            cli.show_stats(args.playlist)
        elif args.command == 'view':
            cli.view_playlist(args.playlist)
        elif args.command == 'sync':
            cli.sync_playlist(args.playlist)
        else:
            parser.print_help()
    except Exception as e:
        logger.error(f"Command failed: {str(e)}")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
