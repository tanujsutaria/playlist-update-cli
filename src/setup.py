import os
from pathlib import Path
import logging
from dotenv import load_dotenv
from models import Song
from db_manager import DatabaseManager  # Direct import

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def verify_spotify_credentials():
    """Verify that Spotify credentials are set"""
    load_dotenv('config/.env')
    required_vars = ['SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'SPOTIFY_REDIRECT_URI']
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        return False
    return True

def setup_directories():
    """Create necessary directories"""
    dirs = [
        "data",
        "data/embeddings",
        "data/history",
        "data/state"
    ]
    
    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        logger.info(f"Created directory: {dir_path}")

def test_database():
    """Test database functionality"""
    try:
        db = DatabaseManager()
        
        # Test song
        test_song = Song(
            id="test_artist|||test_song",
            name="test_song",
            artist="test_artist"
        )
        
        # Test embedding generation
        embedding = db.generate_embedding(test_song)
        logger.info(f"Successfully generated test embedding with shape: {embedding.shape}")
        
        # Test song addition
        db.add_song(test_song)
        logger.info("Successfully added test song to database")
        
        # Test retrieval
        retrieved = db.get_song_by_id(test_song.id)
        assert retrieved is not None
        logger.info("Successfully retrieved test song from database")
        
        return True
    
    except Exception as e:
        logger.error(f"Database test failed: {str(e)}")
        return False

def main():
    logger.info("Starting setup...")
    
    # Verify Spotify credentials
    if not verify_spotify_credentials():
        logger.error("Setup failed: Missing Spotify credentials")
        return False
    
    # Create directories
    setup_directories()
    
    # Test database
    if not test_database():
        logger.error("Setup failed: Database test failed")
        return False
    
    logger.info("Setup completed successfully!")
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
