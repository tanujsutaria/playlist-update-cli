import logging
import os
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from tqdm import tqdm

from models import Song

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Process-global retry-notice channel, cloned from ui.set_status_sink's pattern:
# commands that want VISIBLE backoff (e.g. /pull feeding the TUI top bar) install
# a callback around their Spotify calls; ``None`` uninstalls. Kept here (not in
# ui) so this module stays free of any ui import.
_retry_status_callback: Optional[Callable[[str], None]] = None


def set_retry_status_callback(callback: Optional[Callable[[str], None]]) -> None:
    """Route human-readable retry notices ("rate limited — retrying in 2s") to a sink.

    Without a callback the only trace of a 429 backoff is a logger.warning that
    a TUI user never sees — a rate-limited /pull looks frozen for the whole
    sleep. The callback is best-effort UX: it is guarded at the call site so a
    display failure can never break a retry.
    """
    global _retry_status_callback
    _retry_status_callback = callback


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
            if hasattr(e, "http_status"):
                if e.http_status == 429:
                    is_rate_limited = True
                elif e.http_status in (500, 502, 503, 504):
                    is_transient = True

            if attempt < max_retries and (is_rate_limited or is_transient):
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(
                    f"Spotify API error (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {delay:.1f}s..."
                )
                if _retry_status_callback is not None:
                    reason = "rate limited" if is_rate_limited else "transient error"
                    try:
                        _retry_status_callback(f"{reason} — retrying in {delay:.0f}s")
                    except Exception:
                        # Display is best-effort; a broken sink must never break a retry.
                        logger.debug("retry status callback failed", exc_info=True)
                time.sleep(delay)
            else:
                raise
    raise last_exception  # Should never reach here, but for type safety


SPOTIFY_SCOPES = [
    "playlist-modify-public",
    "playlist-modify-private",
    "playlist-read-private",
    "user-library-read",
    # Reading the user's listening signal (recently-played + top tracks/artists)
    # for /listen-sync and /ingest {recent,top}. Without these the API returns
    # 403 "Insufficient client scope".
    "user-read-recently-played",
    "user-top-read",
]

SCOPE_REAUTH_HINT = (
    "Spotify denied this request for a missing permission (scope). The app's "
    "requested scopes changed since you last authorized, so your cached token is "
    "stale. Re-authorize: run /auth-reset --yes, then re-run the command and "
    "approve the Spotify consent screen."
)


def missing_scopes(scope: Optional[str]) -> List[str]:
    """Return the required scopes a token's space-separated scope string lacks.

    ``scope`` is the ``scope`` field of a cached token info dict (or None).
    An empty/missing scope string counts as lacking every required scope.
    """
    granted = set(scope.split()) if scope else set()
    return [s for s in SPOTIFY_SCOPES if s not in granted]


def scope_error_hint(exc: Exception) -> Optional[str]:
    """Return an actionable re-auth hint if `exc` is a Spotify insufficient-scope
    error (HTTP 403 whose message mentions scope), otherwise None.

    Matches spotipy's SpotifyException, which exposes `http_status`; falls back
    to the stringified message so it also catches wrapped/re-raised errors.
    """
    status = getattr(exc, "http_status", None)
    message = str(exc).lower()
    if status == 403 and "scope" in message:
        return SCOPE_REAUTH_HINT
    return None


class _SecureCacheFileHandler(spotipy.cache_handler.CacheFileHandler):
    """CacheFileHandler that keeps the token file private (0600) after writes.

    spotipy writes the token file using the process umask (commonly leaving it
    world-readable), so we re-tighten permissions on every save.
    """

    def save_token_to_cache(self, token_info):
        super().save_token_to_cache(token_info)
        try:
            os.chmod(self.cache_path, 0o600)
        except OSError:
            logger.debug("Could not chmod token cache file to 0600", exc_info=True)


def _token_cache_path() -> Path:
    """Absolute path of the cached-token file (the only token path in the app)."""
    return Path(__file__).parent.parent / ".spotify_cache" / ".spotify_token"


def reset_cached_token() -> bool:
    """Delete the cached Spotify token file, if any.

    Deletes by path only — the file's contents are never read. Returns True if
    a token file was removed, False if none existed. The next command that
    needs Spotify will re-open the OAuth consent flow and cache a fresh token
    with the currently requested scopes.
    """
    try:
        _token_cache_path().unlink()
        return True
    except FileNotFoundError:
        return False


def _get_cache_handler() -> spotipy.cache_handler.CacheFileHandler:
    cache_dir = _token_cache_path().parent
    cache_dir.mkdir(exist_ok=True, mode=0o700)
    # mkdir(mode=...) is a no-op when the directory already exists, so enforce
    # private permissions explicitly — a pre-existing cache dir may be 0755.
    try:
        cache_dir.chmod(0o700)
    except OSError:
        logger.debug("Could not chmod .spotify_cache dir to 0700", exc_info=True)
    cache_path = _token_cache_path()
    if cache_path.exists():
        try:
            cache_path.chmod(0o600)
        except OSError:
            logger.debug("Could not chmod existing token cache file to 0600", exc_info=True)
    return _SecureCacheFileHandler(cache_path=str(cache_path), username="default")


def _get_auth_manager(open_browser: bool = True) -> SpotifyOAuth:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")
    if not client_id:
        logger.warning("SPOTIFY_CLIENT_ID not set")
    if not client_secret:
        logger.warning("SPOTIFY_CLIENT_SECRET not set")
    if not redirect_uri:
        logger.warning("SPOTIFY_REDIRECT_URI not set")
    return SpotifyOAuth(
        scope=" ".join(SPOTIFY_SCOPES),
        redirect_uri=redirect_uri,
        client_id=client_id,
        client_secret=client_secret,
        cache_handler=_get_cache_handler(),
        open_browser=open_browser,
        show_dialog=False,
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
            self.user_id = self.sp.current_user()["id"]
            logger.debug("Successfully authenticated with Spotify")
        except Exception as e:
            logger.error(f"Failed to authenticate: {e}")
            raise

        self.playlists: Dict[str, str] = {}
        self._load_playlists()

    def _load_playlists(self):
        """Load user's playlists into cache.

        Paginates through ALL pages — current_user_playlists() returns only the
        first page (<=50), so without this loop playlists beyond the first page
        (e.g. an older "Favorites") were silently missing from the cache.
        """
        try:
            results = self.sp.current_user_playlists(limit=50)
            while results:
                for playlist in results.get("items", []):
                    if not playlist:
                        continue
                    owner_id = (playlist.get("owner") or {}).get("id")
                    if owner_id == self.user_id:
                        self.playlists[playlist["name"]] = playlist["id"]
                results = self.sp.next(results) if results.get("next") else None
        except Exception as e:
            logger.error(f"Error loading playlists: {str(e)}")

    def _resolve_name(self, name: str) -> Optional[str]:
        """Resolve a requested playlist name to its cached key.

        Tries an exact match first, then a case-insensitive match (so
        `/view favorites` finds a playlist actually named "Favorites").
        """
        if name in self.playlists:
            return name
        lowered = name.lower()
        for key in self.playlists:
            if key.lower() == lowered:
                return key
        return None

    def create_playlist(self, name: str, description: str = "") -> str:
        """Create a new playlist"""
        if name in self.playlists:
            logger.info(f"Playlist '{name}' already exists")
            return self.playlists[name]

        result = self.sp.user_playlist_create(
            self.user_id, name, public=False, description=description
        )

        playlist_id = result["id"]
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
            for pattern in [" - remix", " (remix)", " feat.", " ft.", " (ft", " (feat"]:
                if pattern in search_name:
                    search_name = search_name[: search_name.index(pattern)]

            # Step 1: Try exact artist search first (most reliable)
            query = f"artist:{artist_name} track:{search_name}"
            results = self.sp.search(query, type="track", limit=5)

            if results["tracks"]["items"]:
                for track in results["tracks"]["items"]:
                    if not track.get("artists"):
                        continue
                    track_name = track["name"].lower()
                    artist_name_spotify = track["artists"][0]["name"].lower()

                    # Check for exact artist match first
                    if artist_name_spotify == artist_name:
                        # Then check for song name similarity
                        name_score = SequenceMatcher(None, track_name, song_name).ratio()
                        if (
                            name_score > 0.85
                        ):  # Lowered threshold for name if artist matches exactly
                            logger.info(
                                f"Found match with exact artist: '{song_name} by {artist_name}' => "
                                f"'{track_name} by {artist_name_spotify}' (Score: {name_score:.2f})"
                            )
                            return track["uri"]

            # Step 2: Try general search with both terms
            query = f"{search_name} {artist_name}"
            results = self.sp.search(query, type="track", limit=10)

            best_match = None
            best_score = 0

            for track in results["tracks"]["items"]:
                if not track.get("artists"):
                    continue
                track_name = track["name"].lower()
                artist_name_spotify = track["artists"][0]["name"].lower()

                # Calculate base similarity scores
                name_score = SequenceMatcher(None, track_name, song_name).ratio()
                artist_score = SequenceMatcher(None, artist_name_spotify, artist_name).ratio()

                # Check if the artist name is contained within the other
                artist_contained = (
                    artist_name in artist_name_spotify or artist_name_spotify in artist_name
                )

                # Boost artist score if names are contained within each other
                if artist_contained:
                    artist_score = max(artist_score, 0.9)

                # Combined score (weighted towards artist matching)
                combined_score = (name_score * 0.4) + (artist_score * 0.6)

                # Additional checks for remixes and features
                if "remix" in song_name and "remix" in track_name:
                    combined_score += 0.1
                if ("feat." in song_name or "ft." in song_name) and (
                    "feat." in track_name or "ft." in track_name
                ):
                    combined_score += 0.1

                if combined_score > best_score and combined_score > 0.8:  # Lowered threshold
                    best_score = combined_score
                    best_match = track

            if best_match and best_match.get("artists"):
                logger.info(
                    f"Found fuzzy match for '{song_name} by {artist_name}' => "
                    f"'{best_match['name'].lower()} by {best_match['artists'][0]['name'].lower()}' "
                    f"(Score: {best_score:.2f})"
                )
                return best_match["uri"]

            # If no match found, log and skip
            logger.warning(f"No high-confidence match found for: {song_name} by {artist_name}")
            return None

        except Exception as e:
            logger.error(f"Error searching for song {song_name}: {str(e)}")
            return None

    def get_artist_top_tracks(
        self, artist_name: str, limit: int = 3, market: str = "US"
    ) -> List[Dict]:
        """Fetch top tracks for an artist by name."""
        try:
            results = self.sp.search(f"artist:{artist_name}", type="artist", limit=1)
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
                top_tracks.append(
                    {
                        "name": track["name"],
                        "artist": artist,
                        "uri": track.get("uri"),
                    }
                )
            return top_tracks
        except Exception as e:
            logger.error(f"Error fetching top tracks for {artist_name}: {str(e)}")
            return []

    def get_playlist_tracks(self, name: str) -> List[Dict]:
        """Get all tracks in a playlist with their metadata"""
        resolved = self._resolve_name(name)
        if resolved is None:
            logger.error(f"Playlist '{name}' not found")
            return []

        playlist_id = self.playlists[resolved]
        tracks = []

        try:
            # Get initial batch of tracks
            results = self.sp.playlist_tracks(
                playlist_id, fields="items(added_at,track(name,artists,uri)),next"
            )

            while results:
                for item in results["items"]:
                    if item and item.get("track"):
                        track = item["track"]
                        tracks.append(
                            {
                                "name": track["name"],
                                "artist": track["artists"][0]["name"]
                                if track["artists"]
                                else "Unknown",
                                "uri": track["uri"],
                                "added_at": item.get(
                                    "added_at", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
                                ),
                            }
                        )

                # Get next batch if available
                if results.get("next"):
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

    # Rich field filter for the /pull library mirror: keeps added_at (which
    # get_playlist_tracks' filter drops) plus the track metadata the local
    # `tracks` upsert wants. `next` must stay in the filter or sp.next() can't
    # paginate.
    PLAYLIST_ITEM_FIELDS = (
        "items(added_at,track(name,id,uri,artists(name),"
        "album(name,release_date),duration_ms,explicit,popularity,external_urls)),next"
    )

    def get_playlist_items_full(
        self,
        playlist_id: str,
        on_page: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict]:
        """All items of a playlist, with added_at + rich track fields (paginated).

        Unlike ``get_playlist_tracks`` this takes a playlist ID (not a name),
        keeps ``added_at``, and PROPAGATES fetch errors — including next-page
        failures — so callers can surface scope hints (403) and, crucially,
        so /pull never persists a silently truncated membership: storing a
        partial page list alongside the current snapshot_id would freeze the
        truncation until the playlist changes remotely (the snapshot-skip
        check would keep skipping it).

        ``on_page(page_number, items_so_far)`` is invoked once per fetched page
        (1-based) so callers can surface paging progress; it must be cheap and
        is called at most once per API round-trip, never per item.
        """
        items: List[Dict] = []
        page = 0
        results = _retry_with_backoff(
            lambda: self.sp.playlist_items(playlist_id, fields=self.PLAYLIST_ITEM_FIELDS)
        )
        while results:
            for item in results.get("items") or []:
                if item and item.get("track"):
                    items.append(item)
            page += 1
            if on_page:
                on_page(page, len(items))
            if results.get("next"):
                try:
                    results = _retry_with_backoff(lambda r=results: self.sp.next(r))
                except Exception:
                    logger.warning(
                        "Error fetching next page of playlist items for %s; aborting "
                        "so a truncated mirror is never persisted.",
                        playlist_id,
                    )
                    raise
            else:
                break
        return items

    def current_user_id(self) -> Optional[str]:
        """The authenticated user's Spotify id (cached from auth; refetch fallback)."""
        cached = getattr(self, "user_id", None)
        if cached:
            return cached
        try:
            self.user_id = (self.sp.current_user() or {}).get("id")
        except Exception as e:
            logger.error(f"Error fetching current user id: {e}")
            return None
        return self.user_id

    def get_track_info(self, uri: str) -> Optional[Dict]:
        """Get track info from URI"""
        try:
            track = self.sp.track(uri)
            return {
                "name": track["name"].lower(),
                "artist": track["artists"][0]["name"].lower()
                if track.get("artists")
                else "unknown",
                "uri": track["uri"],
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
                    logger.warning(
                        f"Error deleting playlist '{name}' (ID: {old_playlist_id}): {str(e)}. Proceeding to recreate playlist anyway."
                    )

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
                    if song.spotify_uri in track_uris or any(
                        uri == song.spotify_uri for uri in track_uris
                    ):
                        added_songs.append(song)

                for i in range(0, len(track_uris), batch_size):
                    batch = track_uris[i : i + batch_size]
                    try:
                        _retry_with_backoff(
                            lambda b=batch: self.sp.playlist_add_items(playlist_id, b)
                        )
                        logger.info(f"Added batch of {len(batch)} tracks")
                    except Exception as e:
                        logger.error(f"Error adding track batch: {str(e)}")
                        batch_failures += 1

            # Report results
            if failed_songs:
                logger.warning(
                    f"Failed to add {len(failed_songs)} songs: {', '.join(failed_songs)}"
                )

            # Check for batch failures
            if track_uris and batch_failures > 0:
                logger.error(f"Failed to add {batch_failures} batch(es) to playlist")
                return False

            # Log the songs that were successfully added
            if track_uris:
                logger.info(
                    f"Successfully updated playlist '{name}': added {len(track_uris)} new tracks"
                )
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
                    _retry_with_backoff(
                        lambda: self.sp.playlist_replace_items(playlist_id, first_batch)
                    )
                    logger.info(f"Replaced playlist with first {len(first_batch)} tracks")
                except Exception as e:
                    logger.error(f"Error replacing playlist items: {str(e)}")
                    batch_failures += 1

                remaining = track_uris[100:]
                batch_size = 50
                for i in range(0, len(remaining), batch_size):
                    batch = remaining[i : i + batch_size]
                    try:
                        _retry_with_backoff(
                            lambda b=batch: self.sp.playlist_add_items(playlist_id, b)
                        )
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
                logger.warning(
                    f"Failed to add {len(failed_songs)} songs: {', '.join(failed_songs)}"
                )

            if batch_failures > 0:
                logger.error(
                    f"Failed {batch_failures} batch operation(s) during playlist replacement"
                )
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
                    batch = track_uris[i : i + batch_size]
                    try:
                        _retry_with_backoff(
                            lambda b=batch: self.sp.playlist_add_items(playlist_id, b)
                        )
                        logger.info(f"Added batch of {len(batch)} tracks")
                    except Exception as e:
                        logger.error(f"Error adding track batch: {str(e)}")
                        batch_failures += 1

            # Report results
            if failed_songs:
                logger.warning(
                    f"Failed to add {len(failed_songs)} songs: {', '.join(failed_songs)}"
                )

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
                batch = track_uris[i : i + batch_size]
                try:
                    _retry_with_backoff(
                        lambda b=batch: self.sp.playlist_remove_all_occurrences_of_items(
                            playlist_id, b
                        )
                    )
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
        """Get playlist ID by name (exact or case-insensitive)."""
        resolved = self._resolve_name(name)
        if resolved is not None:
            return self.playlists[resolved]

        # Not cached — (re)load all playlists and try again.
        self._load_playlists()
        resolved = self._resolve_name(name)
        return self.playlists[resolved] if resolved is not None else None
