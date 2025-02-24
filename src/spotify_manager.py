import os
from typing import List, Optional, Dict, Callable
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from models import Song
import logging
from datetime import datetime
from difflib import SequenceMatcher
from tqdm import tqdm

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
            
            if best_match:
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

    def get_track_info(self, uri: str) -> Optional[Dict]:
        """Get track info from URI"""
        try:
            track = self.sp.track(uri)
            return {
                'name': track['name'].lower(),
                'artist': track['artists'][0]['name'].lower(),
                'uri': track['uri']
            }
        except Exception as e:
            logger.error(f"Error getting track info for {uri}: {str(e)}")
            return None

    def refresh_playlist(self, name: str, songs: List[Song], sync_mode: bool = False) -> bool:
        """Refresh a playlist with songs. In sync mode, only adds missing songs."""
        try:
            # Get or create playlist
            if name not in self.playlists:
                self.create_playlist(name)
            playlist_id = self.playlists[name]
            
            # Get existing tracks if in sync mode
            existing_tracks = {}  # key -> uri mapping for unique tracks
            if sync_mode:
                logger.info("Getting existing playlist tracks...")
                current_tracks = self.get_playlist_tracks(name)
                
                # First pass: keep only the first occurrence of each song
                seen_songs = {}  # key -> uri mapping for first occurrence
                duplicates_to_remove = []
                
                for track in current_tracks:
                    key = f"{track['name'].lower()}|||{track['artist'].lower()}"
                    if key not in seen_songs:
                        seen_songs[key] = track['uri']
                        existing_tracks[key] = track['uri']
                    else:
                        duplicates_to_remove.append(track['uri'])
                
                # Remove duplicates in batches if found
                if duplicates_to_remove:
                    logger.info(f"Found {len(duplicates_to_remove)} duplicate tracks, removing in batches...")
                    batch_size = 50
                    for i in range(0, len(duplicates_to_remove), batch_size):
                        batch = duplicates_to_remove[i:i + batch_size]
                        try:
                            self.sp.playlist_remove_all_occurrences_of_items(playlist_id, batch)
                            logger.info(f"Removed batch of {len(batch)} duplicates")
                        except Exception as e:
                            logger.error(f"Error removing duplicate batch: {str(e)}")
            else:
                # Clear existing tracks in non-sync mode
                logger.info("Clearing existing tracks...")
                self.sp.playlist_replace_items(playlist_id, [])
            
            # Find new songs to add
            songs_to_process = []
            seen_songs = set()
            for song in songs:
                key = f"{song.name.lower()}|||{song.artist.lower()}"
                if key not in seen_songs and (not sync_mode or key not in existing_tracks):
                    songs_to_process.append(song)
                    seen_songs.add(key)
            
            if not songs_to_process:
                logger.info("No new songs to add to playlist")
                return True
            
            # Search and add new tracks
            track_uris = []
            failed_songs = set()
            
            logger.info(f"Processing {len(songs_to_process)} new tracks...")
            with tqdm(total=len(songs_to_process), desc="Processing tracks") as pbar:
                for song in songs_to_process:
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
                for i in range(0, len(track_uris), batch_size):
                    batch = track_uris[i:i + batch_size]
                    try:
                        self.sp.playlist_add_items(playlist_id, batch)
                        logger.info(f"Added batch of {len(batch)} tracks")
                    except Exception as e:
                        logger.error(f"Error adding track batch: {str(e)}")
            
            # Report results
            if failed_songs:
                logger.warning(f"Failed to add {len(failed_songs)} songs: {', '.join(failed_songs)}")
            
            summary = []
            if duplicates_to_remove:
                summary.append(f"removed {len(duplicates_to_remove)} duplicates")
            if track_uris:
                summary.append(f"added {len(track_uris)} new tracks")
            
            logger.info(f"Successfully updated playlist '{name}': {', '.join(summary)}")
            return True
            
        except Exception as e:
            logger.error(f"Error refreshing playlist '{name}': {str(e)}")
            logger.debug("Full error:", exc_info=True)
            return False 