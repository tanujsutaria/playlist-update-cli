import logging
import os
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

def test_playlist_update():
    """Test the playlist update functionality"""
    try:
        # Load environment variables
        load_dotenv('config/.env')
        
        # Initialize managers
        db = DatabaseManager()
        spotify = SpotifyManager()
        
        # Create a test playlist name
        test_playlist = "Test Update Playlist"
        
        # Initialize rotation manager
        rm = RotationManager(
            playlist_name=test_playlist,
            db=db,
            spotify=spotify
        )
        
        # Create test songs with real songs that will be found in Spotify
        test_songs = [
            Song(id="queen|||bohemian_rhapsody", name="Bohemian Rhapsody", artist="Queen"),
            Song(id="michael_jackson|||billie_jean", name="Billie Jean", artist="Michael Jackson")
        ]
        
        # Try to find real songs in Spotify
        for song in test_songs:
            uri = spotify.search_song(song)
            if uri:
                song.spotify_uri = uri
                logger.info(f"Found URI for {song.name}: {uri}")
            else:
                logger.warning(f"Could not find URI for {song.name}")
        
        # Update the playlist
        logger.info(f"Updating playlist '{test_playlist}' with test songs...")
        success = rm.update_playlist(test_songs)
        
        if success:
            logger.info("Playlist update test PASSED!")
        else:
            logger.error("Playlist update test FAILED!")
        
        return success
        
    except Exception as e:
        logger.error(f"Test failed with error: {str(e)}")
        logger.debug("Full error:", exc_info=True)
        return False

if __name__ == "__main__":
    test_playlist_update()
