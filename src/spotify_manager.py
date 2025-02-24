import os
from typing import List, Optional, Dict, Callable
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from models import Song
import logging
from datetime import datetime
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

class SpotifyManager:
    """Manages Spotify playlist operations"""
    
    def __init__(self):
        self.sp = self._initialize_spotify()
        
        self.user_id = self.sp.current_user()['id']
        self.playlists: Dict[str, str] = {}  # name -> id mapping
        self._load_playlists()

    def _initialize_spotify(self) -> spotipy.Spotify:
        """Initialize Spotify client with proper scopes"""
        scopes = [
            'playlist-modify-public',
            'playlist-modify-private',
            'playlist-read-private',
            'user-library-read'
        ]
        
        return spotipy.Spotify(auth_manager=SpotifyOAuth(
            scope=' '.join(scopes),
            redirect_uri=os.getenv('SPOTIFY_REDIRECT_URI'),
            client_id=os.getenv('SPOTIFY_CLIENT_ID'),
            client_secret=os.getenv('SPOTIFY_CLIENT_SECRET')
        ))

    def _load_playlists(self):
        """Load user's playlists"""
        results = self.sp.current_user_playlists()
        for item in results['items']:
            self.playlists[item['name']] = item['id']

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
            # Step 1: Try exact match first
            query = f"track:{song.name} artist:{song.artist}"
            results = self.sp.search(query, type='track', limit=3)
            
            if results['tracks']['items']:
                # Check first result for exact match
                track = results['tracks']['items'][0]
                if (track['name'].lower() == song.name.lower() and 
                    track['artists'][0]['name'].lower() == song.artist.lower()):
                    logger.debug(f"Found exact match for: {song.name} by {song.artist}")
                    return track['uri']
            
            # Step 2: Try fuzzy matching with higher threshold (0.95)
            query = f"{song.name} {song.artist}"
            results = self.sp.search(query, type='track', limit=5)
            
            best_match = None
            best_score = 0
            
            for track in results['tracks']['items']:
                # Calculate similarity scores
                name_score = SequenceMatcher(None, track['name'].lower(), song.name.lower()).ratio()
                artist_score = SequenceMatcher(None, track['artists'][0]['name'].lower(), song.artist.lower()).ratio()
                
                # Combined score (weighted towards artist matching)
                combined_score = (name_score * 0.4) + (artist_score * 0.6)
                
                if combined_score > best_score and combined_score > 0.95:  # Increased threshold to 95%
                    best_score = combined_score
                    best_match = track
                    
            if best_match:
                logger.info(f"Found fuzzy match for '{song.name} by {song.artist}' => "
                           f"'{best_match['name']} by {best_match['artists'][0]['name']}' "
                           f"(Score: {best_score:.2f})")
                return best_match['uri']
            
            # If no high-confidence match found, log and skip
            logger.warning(f"No high-confidence match found for: {song.name} by {song.artist}")
            return None
            
        except Exception as e:
            logger.error(f"Error searching for song {song.name}: {str(e)}")
            return None

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
                    results = self.sp.next(results)
                else:
                    break
            
            logger.info(f"Retrieved {len(tracks)} tracks from playlist '{name}'")
            return tracks
            
        except Exception as e:
            logger.error(f"Error getting playlist tracks: {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return []

    def refresh_playlist(self, name: str, songs: List[Song], progress_callback: Optional[callable] = None) -> bool:
        """Completely refresh a playlist with new songs"""
        try:
            # Get or create playlist
            if name not in self.playlists:
                self.create_playlist(name)
            playlist_id = self.playlists[name]
            
            # Clear existing tracks
            logger.info("Clearing existing tracks...")
            self.sp.playlist_replace_items(playlist_id, [])
            
            # Search and add new tracks
            track_uris = []
            failed_songs = []
            
            logger.info("Searching for tracks...")
            for song in songs:
                try:
                    if song.spotify_uri:
                        track_uris.append(song.spotify_uri)
                    else:
                        uri = self.search_song(song)
                        if uri:
                            song.spotify_uri = uri
                            track_uris.append(uri)
                        else:
                            failed_songs.append(song)
                    
                    if progress_callback:
                        progress_callback(1)
                    
                except Exception as e:
                    logger.warning(f"Failed to process song {song.name}: {str(e)}")
                    failed_songs.append(song)
            
            # Add tracks in batches
            if track_uris:
                logger.info(f"Adding {len(track_uris)} tracks to playlist...")
                batch_size = 100
                for i in range(0, len(track_uris), batch_size):
                    batch = track_uris[i:i + batch_size]
                    self.sp.playlist_add_items(playlist_id, batch)
            
            # Report results
            if failed_songs:
                logger.warning(f"Failed to add {len(failed_songs)} songs: {', '.join(s.name for s in failed_songs)}")
            
            logger.info(f"Successfully refreshed playlist '{name}' with {len(track_uris)} tracks")
            return True
            
        except Exception as e:
            logger.error(f"Error refreshing playlist '{name}': {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return False 