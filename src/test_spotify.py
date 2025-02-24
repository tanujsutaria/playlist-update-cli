import logging
from models import Song
from spotify_manager import SpotifyManager
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_spotify_integration():
    """Test basic Spotify functionality"""
    try:
        load_dotenv('config/.env')
        sp = SpotifyManager()
        
        # Test playlist creation
        playlist_name = "Test Playlist"
        sp.create_playlist(playlist_name, "Test playlist for integration testing")
        
        # Test song search and playlist update
        test_songs = [
            Song(id="queen|||bohemian_rhapsody", name="Bohemian Rhapsody", artist="Queen"),
            Song(id="beatles|||hey_jude", name="Hey Jude", artist="The Beatles")
        ]
        
        success = sp.refresh_playlist(playlist_name, test_songs)
        if success:
            logger.info("Spotify integration test passed!")
            return True
    
    except Exception as e:
        logger.error(f"Spotify integration test failed: {str(e)}")
    
    return False

if __name__ == "__main__":
    test_spotify_integration() 