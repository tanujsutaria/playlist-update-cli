import logging
from models import Song
from rotation_manager import RotationManager
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_rotation_manager():
    """Test rotation manager functionality"""
    try:
        load_dotenv('config/.env')
        
        # Initialize rotation manager
        rm = RotationManager("Test Rotation Playlist")
        
        # Get current stats
        stats = rm.get_rotation_stats()
        logger.info(f"Initial stats: {stats}")
        
        # Select songs for today
        songs = rm.select_songs_for_today(count=5)
        logger.info(f"Selected {len(songs)} songs for today")
        
        # Update playlist
        rm.update_playlist(songs)
        
        # Get recent songs
        recent = rm.get_recent_songs(days=3)
        logger.info(f"Recent songs from the last 3 days: {recent}")
        
        logger.info("Rotation manager test passed!")
        return True
        
    except Exception as e:
        logger.error(f"Rotation manager test failed: {str(e)}")
        return False

if __name__ == "__main__":
    test_rotation_manager() 