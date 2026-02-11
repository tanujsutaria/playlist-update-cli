import os
import time
from typing import List, Optional, Dict, Callable, Any, TypeVar
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from models import Song
import logging
from datetime import datetime
from difflib import SequenceMatcher
from tqdm import tqdm
from pathlib import Path

logger = logging.getLogger(__name__)

T = TypeVar('T')


def _retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
) -> T:
    """Execute a function with exponential backoff retry on failure.

    Args:
        func: The function to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds

    Returns:
        The result of the function

    Raises:
        The last exception if all retries fail
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()
            is_rate_limited = "429" in error_str or "rate limit" in error_str
            is_transient = "timeout" in error_str or "connection" in error_str
            # Also check SpotifyException status code directly
            if hasattr(e, 'http_status'):
                if e.http_status == 429:
                    is_rate_limited = True
                elif e.http_status in (500, 502, 503, 504):
                    is_transient = True

            if attempt < max_retries and (is_rate_limited or is_transient):
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(f"Spotify API error (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                raise
    raise last_exception  # Should never reach here, but for type safety

SPOTIFY_SCOPES = [
    'playlist-modify-public',
    'playlist-modify-private',
    'playlist-read-private',
    'user-library-read'
]

def _get_cache_handler() -> spotipy.cache_handler.CacheFileHandler:
    cache_dir = Path(__file__).parent.parent / ".spotify_cache"
    cache_dir.mkdir(exist_ok=True, mode=0o700)
    cache_path = cache_dir / ".spotify_token"
    return spotipy.cache_handler.CacheFileHandler(
        cache_path=str(cache_path),
        username='default'
    )

def _get_auth_manager(open_browser: bool = True) -> SpotifyOAuth:
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    redirect_uri = os.getenv('SPOTIFY_REDIRECT_URI')
    if not client_id:
        logger.warning("SPOTIFY_CLIENT_ID not set")
    if not client_secret:
        logger.warning("SPOTIFY_CLIENT_SECRET not set")
    if not redirect_uri:
        logger.warning("SPOTIFY_REDIRECT_URI not set")
    return SpotifyOAuth(
        scope=' '.join(SPOTIFY_SCOPES),
        redirect_uri=redirect_uri,
        client_id=client_id,
        client_secret=client_secret,
        cache_handler=_get_cache_handler(),
        open_browser=open_browser,
        show_dialog=False
    )

def get_cached_token_info() -> Optional[Dict[str, Any]]:
    """Return cached token info without triggering auth flow."""
    try:
        auth_manager = _get_auth_manager(open_browser=False)
        return auth_manager.get_cached_token()
    except Exception as e:
        logger.error(f"Error reading cached token: {e}")
        return None

def refresh_cached_token() -> Optional[Dict[str, Any]]:
    """Refresh the cached token if possible."""
    try:
        auth_manager = _get_auth_manager(open_browser=False)
        cached = auth_manager.get_cached_token()
        if not cached or "refresh_token" not in cached:
            logger.warning("No refresh token available in cache")
            return None
        refreshed = auth_manager.refresh_access_token(cached["refresh_token"])
        return refreshed
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        return None

class SpotifyManager:
    """Manages Spotify playlist operations"""
    
    def __init__(self):
        auth_manager = _get_auth_manager(open_browser=True)
        
        # Initialize Spotify client with auth manager
        self.sp = spotipy.Spotify(auth_manager=auth_manager)
        
        # Test the connection and token
        try:
            self.user_id = self.sp.current_user()['id']
            logger.debug("Successfully authenticated with Spotify")
        except Exception as e:
            logger.error(f"Failed to authenticate: {e}")
            raise
        
        self.playlists: Dict[str, str] = {}
        self._load_playlists()

    def _load_playlists(self):
        """Load user's playlists into cache"""
        try:
            playlists = self.sp.current_user_playlists()
            for playlist in playlists['items']:
                if playlist['owner']['id'] == self.user_id:
                    self.playlists[playlist['name']] = playlist['id']
        except Exception as e:
            logger.error(f"Error loading playlists: {str(e)}")

    def create_playlist(self, name: str, description: str = "") -> str:
        """Create a new playlist"""
        if name in self.playlists:
            logger.info(f"Playlist '{name}' already exists")
            return self.playlists[name]
            
        result = self.sp.user_playlist_create(
            self.user_id,
            name,
            public=False,
            description=description
        )
        
        playlist_id = result['id']
        self.playlists[name] = playlist_id
        logger.info(f"Created playlist '{name}' with ID: {playlist_id}")
        return playlist_id

    def search_song(self, song: Song) -> Optional[str]:
        """Search for a song on Spotify and return its URI"""
        try:
            # Clean up search terms
            song_name = song.name.strip()
            artist_name = song.artist.strip()
            
            # Remove common features/remix indicators for initial search
            search_name = song_name
            for pattern in [' - remix', ' (remix)', ' feat.', ' ft.', ' (ft', ' (feat']:
                if pattern in search_name:
                    search_name = search_name[:search_name.index(pattern)]
            
            # Step 1: Try exact artist search first (most reliable)
            query = f"artist:{artist_name} track:{search_name}"
            results = self.sp.search(query, type='track', limit=5)
            
            if results['tracks']['items']:
                for track in results['tracks']['items']:
                    if not track.get('artists'):
                        continue
                    track_name = track['name'].lower()
                    artist_name_spotify = track['artists'][0]['name'].lower()

                    # Check for exact artist match first
                    if artist_name_spotify == artist_name:
                        # Then check for song name similarity
                        name_score = SequenceMatcher(None, track_name, song_name).ratio()
                        if name_score > 0.85:  # Lowered threshold for name if artist matches exactly
                            logger.info(f"Found match with exact artist: '{song_name} by {artist_name}' => "
                                      f"'{track_name} by {artist_name_spotify}' (Score: {name_score:.2f})")
                            return track['uri']
            
            # Step 2: Try general search with both terms
            query = f"{search_name} {artist_name}"
            results = self.sp.search(query, type='track', limit=10)
            
            best_match = None
            best_score = 0
            
            for track in results['tracks']['items']:
                if not track.get('artists'):
                    continue
                track_name = track['name'].lower()
                artist_name_spotify = track['artists'][0]['name'].lower()

                # Calculate base similarity scores
                name_score = SequenceMatcher(None, track_name, song_name).ratio()
                artist_score = SequenceMatcher(None, artist_name_spotify, artist_name).ratio()

                # Check if the artist name is contained within the other
                artist_contained = (artist_name in artist_name_spotify or
                                  artist_name_spotify in artist_name)
                
                # Boost artist score if names are contained within each other
                if artist_contained:
                    artist_score = max(artist_score, 0.9)
                
                # Combined score (weighted towards artist matching)
                combined_score = (name_score * 0.4) + (artist_score * 0.6)
                
                # Additional checks for remixes and features
                if 'remix' in song_name and 'remix' in track_name:
                    combined_score += 0.1
                if ('feat.' in song_name or 'ft.' in song_name) and \
                   ('feat.' in track_name or 'ft.' in track_name):
                    combined_score += 0.1
                
                if combined_score > best_score and combined_score > 0.8:  # Lowered threshold
                    best_score = combined_score
                    best_match = track
            
            if best_match and best_match.get('artists'):
                logger.info(f"Found fuzzy match for '{song_name} by {artist_name}' => "
                           f"'{best_match['name'].lower()} by {best_match['artists'][0]['name'].lower()}' "
                           f"(Score: {best_score:.2f})")
                return best_match['uri']
            
            # If no match found, log and skip
            logger.warning(f"No high-confidence match found for: {song_name} by {artist_name}")
            return None
            
        except Exception as e:
            logger.error(f"Error searching for song {song_name}: {str(e)}")
            return None

    def get_artist_top_tracks(self, artist_name: str, limit: int = 3, market: str = "US") -> List[Dict]:
        """Fetch top tracks for an artist by name."""
        try:
            results = self.sp.search(f"artist:{artist_name}", type='artist', limit=1)
            items = results.get("artists", {}).get("items", [])
            if not items:
                logger.warning(f"No artist found for '{artist_name}'")
                return []
            artist_id = items[0]["id"]
            tracks = self.sp.artist_top_tracks(artist_id, country=market).get("tracks", [])
            top_tracks = []
            for track in tracks[:limit]:
                artists = track.get("artists")
                artist = (artists[0].get("name") or artist_name) if artists else artist_name
                top_tracks.append({
                    "name": track["name"],
                    "artist": artist,
                    "uri": track.get("uri"),
                })
            return top_tracks
        except Exception as e:
            logger.error(f"Error fetching top tracks for {artist_name}: {str(e)}")
            return []

    def get_playlist_tracks(self, name: str) -> List[Dict]:
        """Get all tracks in a playlist with their metadata"""
        if name not in self.playlists:
            logger.error(f"Playlist '{name}' not found")
            return []
            
        playlist_id = self.playlists[name]
        tracks = []
        
        try:
            # Get initial batch of tracks
            results = self.sp.playlist_tracks(
                playlist_id,
                fields='items(added_at,track(name,artists,uri)),next'
            )
            
            while results:
                for item in results['items']:
                    if item and item.get('track'):
                        track = item['track']
                        tracks.append({
                            'name': track['name'],
                            'artist': track['artists'][0]['name'] if track['artists'] else 'Unknown',
                            'uri': track['uri'],
                            'added_at': item.get('added_at', datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
                        })
                
                # Get next batch if available
                if results.get('next'):
                    try:
                        results = self.sp.next(results)
                    except Exception as e:
                        logger.warning(f"Error fetching next page: {e}")
                        break
                else:
                    break
            
            logger.info(f"Retrieved {len(tracks)} tracks from playlist '{name}'")
            return tracks
            
        except Exception as e:
            logger.error(f"Error getting playlist tracks: {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return []

    def get_track_info(self, uri: str) -> Optional[Dict]:
        """Get track info from URI"""
        try:
            track = self.sp.track(uri)
            return {
                'name': track['name'].lower(),
                'artist': track['artists'][0]['name'].lower() if track.get('artists') else 'unknown',
                'uri': track['uri']
            }
        except Exception as e:
            logger.error(f"Error getting track info for {uri}: {str(e)}")
            return None

    def refresh_playlist(self, name: str, songs: List[Song], sync_mode: bool = False) -> bool:
        """Refresh a playlist with new songs by deleting and recreating it"""
        try:
            # Delete the playlist if it exists
            if name in self.playlists:
                old_playlist_id = self.playlists[name]
                logger.info(f"Deleting existing playlist '{name}' (ID: {old_playlist_id})...")
                try:
                    self.sp.current_user_unfollow_playlist(old_playlist_id)
                    # Remove from cache
                    del self.playlists[name]
                    logger.info(f"Successfully deleted playlist '{name}'")
                except Exception as e:
                    logger.warning(f"Error deleting playlist '{name}' (ID: {old_playlist_id}): {str(e)}. Proceeding to recreate playlist anyway.")
            
            # Create a new playlist
            logger.info(f"Creating new playlist '{name}'...")
            playlist_id = self.create_playlist(name)
            if not playlist_id:
                return False
            
            # Process new tracks
            track_uris = []
            failed_songs = set()
            
            # Process the original songs
            logger.info(f"Processing {len(songs)} new tracks...")
            with tqdm(
                total=len(songs),
                desc="Processing tracks",
                disable=os.getenv("TUNR_INTERACTIVE") == "1",
            ) as pbar:
                for song in songs:
                    uri = None
                    try:
                        if song.spotify_uri:
                            uri = song.spotify_uri
                        else:
                            uri = self.search_song(song)
                            if uri:
                                song.spotify_uri = uri
                    except Exception as e:
                        logger.warning(f"Failed to process song {song.name}: {str(e)}")
                        failed_songs.add(song.name)
                    finally:
                        pbar.update(1)
                    
                    if uri:
                        track_uris.append(uri)
                    else:
                        failed_songs.add(song.name)
            
            # Add new tracks in batches
            if track_uris:
                logger.info(f"Adding {len(track_uris)} new tracks...")
                batch_size = 50
                batch_failures = 0

                # Keep track of successfully added songs
                added_songs = []
                for i, song in enumerate(songs):
                    if song.spotify_uri in track_uris or any(uri == song.spotify_uri for uri in track_uris):
                        added_songs.append(song)

                for i in range(0, len(track_uris), batch_size):
                    batch = track_uris[i:i + batch_size]
                    try:
                        _retry_with_backoff(lambda b=batch: self.sp.playlist_add_items(playlist_id, b))
                        logger.info(f"Added batch of {len(batch)} tracks")
                    except Exception as e:
                        logger.error(f"Error adding track batch: {str(e)}")
                        batch_failures += 1

            # Report results
            if failed_songs:
                logger.warning(f"Failed to add {len(failed_songs)} songs: {', '.join(failed_songs)}")

            # Check for batch failures
            if track_uris and batch_failures > 0:
                logger.error(f"Failed to add {batch_failures} batch(es) to playlist")
                return False

            # Log the songs that were successfully added
            if track_uris:
                logger.info(f"Successfully updated playlist '{name}': added {len(track_uris)} new tracks")
                for i, song in enumerate(songs):
                    if song.spotify_uri and (song.spotify_uri in track_uris):
                        logger.info(f"  - Added: {song.name} by {song.artist}")
            return True
            
        except Exception as e:
            logger.error(f"Error refreshing playlist '{name}': {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return False

    def replace_playlist_items(self, name: str, songs: List[Song]) -> bool:
        """Replace playlist contents without deleting the playlist (preserve ID)."""
        try:
            playlist_id = self.get_playlist_id(name)
            if not playlist_id:
                playlist_id = self.create_playlist(name)
                if not playlist_id:
                    logger.error(f"Failed to create playlist '{name}'")
                    return False

            track_uris: List[str] = []
            failed_songs = set()
            logger.info(f"Processing {len(songs)} tracks for replacement...")
            with tqdm(
                total=len(songs),
                desc="Processing tracks",
                disable=os.getenv("TUNR_INTERACTIVE") == "1",
            ) as pbar:
                for song in songs:
                    uri = None
                    try:
                        if song.spotify_uri:
                            uri = song.spotify_uri
                        else:
                            uri = self.search_song(song)
                            if uri:
                                song.spotify_uri = uri
                    except Exception as e:
                        logger.warning(f"Failed to process song {song.name}: {str(e)}")
                        failed_songs.add(song.name)
                    finally:
                        pbar.update(1)

                    if uri:
                        track_uris.append(uri)
                    else:
                        failed_songs.add(song.name)

            batch_failures = 0
            if track_uris:
                first_batch = track_uris[:100]
                try:
                    _retry_with_backoff(lambda: self.sp.playlist_replace_items(playlist_id, first_batch))
                    logger.info(f"Replaced playlist with first {len(first_batch)} tracks")
                except Exception as e:
                    logger.error(f"Error replacing playlist items: {str(e)}")
                    batch_failures += 1

                remaining = track_uris[100:]
                batch_size = 50
                for i in range(0, len(remaining), batch_size):
                    batch = remaining[i:i + batch_size]
                    try:
                        _retry_with_backoff(lambda b=batch: self.sp.playlist_add_items(playlist_id, b))
                        logger.info(f"Added batch of {len(batch)} tracks")
                    except Exception as e:
                        logger.error(f"Error adding track batch: {str(e)}")
                        batch_failures += 1
            else:
                try:
                    _retry_with_backoff(lambda: self.sp.playlist_replace_items(playlist_id, []))
                except Exception as e:
                    logger.error(f"Error clearing playlist: {str(e)}")
                    batch_failures += 1

            if failed_songs:
                logger.warning(f"Failed to add {len(failed_songs)} songs: {', '.join(failed_songs)}")

            if batch_failures > 0:
                logger.error(f"Failed {batch_failures} batch operation(s) during playlist replacement")
                return False

            logger.info(f"Playlist '{name}' updated without deletion.")
            return True
        except Exception as e:
            logger.error(f"Error replacing playlist '{name}': {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return False

    def append_to_playlist(self, name: str, songs: List[Song]) -> bool:
        """Append songs to an existing playlist without removing current tracks"""
        try:
            # Get playlist ID
            playlist_id = self.get_playlist_id(name)
            if not playlist_id:
                playlist_id = self.create_playlist(name)
                if not playlist_id:
                    logger.error(f"Failed to create playlist '{name}'")
                    return False
            
            # Process tracks to add
            track_uris = []
            failed_songs = set()
            
            logger.info(f"Processing {len(songs)} tracks to append...")
            with tqdm(
                total=len(songs),
                desc="Processing tracks",
                disable=os.getenv("TUNR_INTERACTIVE") == "1",
            ) as pbar:
                for song in songs:
                    uri = None
                    try:
                        if song.spotify_uri:
                            uri = song.spotify_uri
                        else:
                            uri = self.search_song(song)
                            if uri:
                                song.spotify_uri = uri
                    except Exception as e:
                        logger.warning(f"Failed to process song {song.name}: {str(e)}")
                        failed_songs.add(song.name)
                    finally:
                        pbar.update(1)
                    
                    if uri:
                        track_uris.append(uri)
                    else:
                        failed_songs.add(song.name)
            
            # Add tracks in batches
            batch_failures = 0
            if track_uris:
                logger.info(f"Appending {len(track_uris)} new tracks...")
                batch_size = 50
                for i in range(0, len(track_uris), batch_size):
                    batch = track_uris[i:i + batch_size]
                    try:
                        _retry_with_backoff(lambda b=batch: self.sp.playlist_add_items(playlist_id, b))
                        logger.info(f"Added batch of {len(batch)} tracks")
                    except Exception as e:
                        logger.error(f"Error adding track batch: {str(e)}")
                        batch_failures += 1

            # Report results
            if failed_songs:
                logger.warning(f"Failed to add {len(failed_songs)} songs: {', '.join(failed_songs)}")

            # Check for batch failures
            if batch_failures > 0:
                logger.error(f"Failed to add {batch_failures} batch(es) to playlist")
                return False

            logger.info(f"Successfully appended tracks to playlist '{name}'")
            return True
            
        except Exception as e:
            logger.error(f"Error appending to playlist '{name}': {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return False
            
    def remove_from_playlist(self, name: str, track_uris: List[str]) -> bool:
        """Remove tracks from a playlist by URI"""
        try:
            # Get playlist ID
            playlist_id = self.get_playlist_id(name)
            if not playlist_id:
                logger.error(f"Playlist '{name}' not found")
                return False
            
            if not track_uris:
                logger.info("No tracks to remove")
                return True
            
            logger.info(f"Removing {len(track_uris)} tracks from playlist '{name}'...")

            # Remove tracks in batches
            batch_size = 50
            batch_failures = 0
            for i in range(0, len(track_uris), batch_size):
                batch = track_uris[i:i + batch_size]
                try:
                    _retry_with_backoff(lambda b=batch: self.sp.playlist_remove_all_occurrences_of_items(playlist_id, b))
                    logger.info(f"Removed batch of {len(batch)} tracks")
                except Exception as e:
                    logger.error(f"Error removing track batch: {str(e)}")
                    batch_failures += 1

            if batch_failures > 0:
                logger.error(f"Failed to remove {batch_failures} batch(es) from playlist")
                return False

            logger.info(f"Successfully removed tracks from playlist '{name}'")
            return True
            
        except Exception as e:
            logger.error(f"Error removing tracks from playlist '{name}': {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return False
            
    def get_playlist_id(self, name: str) -> Optional[str]:
        """Get playlist ID by name"""
        # Check cache first
        if name in self.playlists:
            return self.playlists[name]
        
        # Load playlists if not cached
        self._load_playlists()
        return self.playlists.get(name)
