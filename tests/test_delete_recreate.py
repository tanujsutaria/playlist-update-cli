import logging
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from models import Song  # noqa: E402
from db_manager import DatabaseManager  # noqa: E402
from spotify_manager import SpotifyManager  # noqa: E402
from rotation_manager import RotationManager  # noqa: E402

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_delete_recreate_playlist():
    """Test the delete and recreate playlist functionality"""
    if not os.getenv("SPOTIFY_CLIENT_ID"):
        pytest.skip("Spotify credentials not configured")
    try:
        # Load environment variables
        load_dotenv('config/.env')
        
        # Initialize managers
        db = DatabaseManager()
        spotify = SpotifyManager()
        
        # Create a test playlist name
        test_playlist = "Test Delete Recreate Playlist"
        
        # Create initial playlist with one song
        logger.info(f"Creating initial playlist '{test_playlist}'...")
        initial_song = Song(id="queen|||bohemian_rhapsody", name="Bohemian Rhapsody", artist="Queen")
        uri = spotify.search_song(initial_song)
        if uri:
            initial_song.spotify_uri = uri
        
        spotify.refresh_playlist(test_playlist, [initial_song])
        
        # Now update with different songs to test delete and recreate
        logger.info(f"Now updating playlist with new songs (should delete and recreate)...")
        new_songs = [
            Song(id="michael_jackson|||billie_jean", name="Billie Jean", artist="Michael Jackson"),
            Song(id="eagles|||hotel_california", name="Hotel California", artist="Eagles")
        ]
        
        # Find URIs for the new songs
        for song in new_songs:
            uri = spotify.search_song(song)
            if uri:
                song.spotify_uri = uri
                logger.info(f"Found URI for {song.name}: {uri}")
        
        # Update the playlist (should delete and recreate)
        spotify.refresh_playlist(test_playlist, new_songs)
        
        # Verify the playlist contents
        tracks = spotify.get_playlist_tracks(test_playlist)
        logger.info(f"Final playlist has {len(tracks)} tracks:")
        for i, track in enumerate(tracks, 1):
            logger.info(f"  {i}. {track['name']} by {track['artist']}")
        
        logger.info("Delete and recreate test completed!")
        return True
        
    except Exception as e:
        logger.error(f"Test failed with error: {str(e)}")
        logger.debug("Full error:", exc_info=True)
        return False

if __name__ == "__main__":
    test_delete_recreate_playlist()
