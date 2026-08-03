"""Playlist-name resolver: fuzzy did-you-mean suggestions on a miss.

Central seam shared by every playlist-taking command's not-found path
(update/rotate/sync/view/diff/extract/plan), so both the TUI and the
headless CLI render the same actionable error. SUGGEST-ONLY by contract:
a close match is only ever PRINTED — nothing here (or in any caller) may
substitute a guessed name or execute against it.

``collect_playlist_names`` reads the cli's PRIVATE ``_spotify``/``_repos``
attributes via getattr on purpose: a suggestion path must never trigger the
lazy properties (interactive OAuth, database creation). Pinned by
tests/test_playlist_resolver.py.
"""

from __future__ import annotations

import difflib
import logging
from typing import TYPE_CHECKING, Any, Dict, List

from ui import error, warning

if TYPE_CHECKING:
    from main import PlaylistCLI

logger = logging.getLogger(__name__)


def suggest_playlist_names(name: str, candidates: List[str], limit: int = 3) -> List[str]:
    """Close matches for a missing playlist name (case-insensitive, ranked).

    Pure function over an explicit candidate list; duplicates that differ only
    by case collapse to the first spelling seen. Exact matches never reach
    this — callers only consult it after resolution already failed.
    """
    by_lower: Dict[str, str] = {}
    for candidate in candidates:
        if candidate:
            by_lower.setdefault(candidate.lower(), candidate)
    # cutoff 0.5 (below difflib's 0.6 default): playlist typos often drop or
    # add whole words ("dailymix" vs "my daily mix"), which dilutes the ratio.
    matches = difflib.get_close_matches(name.lower(), list(by_lower), n=limit, cutoff=0.5)
    return [by_lower[match] for match in matches]


def playlist_not_found_message(name: str, suggestions: List[str]) -> str:
    """The actionable miss message (suggestions are display-only)."""
    if suggestions:
        listed = ", ".join(suggestions)
        return (
            f"no playlist '{name}' — did you mean: {listed}? "
            "(suggestions only — rerun with the exact name)"
        )
    return (
        f"no playlist '{name}' — no similar name known "
        "(names come from your Spotify playlists, rotation history and the "
        "/pull mirror; run /pull to refresh)"
    )


def collect_playlist_names(cli: "PlaylistCLI") -> List[str]:
    """Every playlist name tunr knows about, for did-you-mean candidates.

    Sources: the live Spotify cache when the manager is ALREADY initialized
    (never lazy-inits it — that could launch the interactive OAuth flow from
    a suggestion path), plus the rotation `playlists` table and the /pull
    mirror (`spotify_playlists`) when the repos are ALREADY materialized —
    the lazy `repos` property is never triggered just to suggest, so this
    path can never create a database as a side effect. Best-effort: any
    source that cannot be read is skipped — suggestions are advisory, never
    worth failing over.
    """
    by_lower: Dict[str, str] = {}

    def _add(value: Any) -> None:
        if isinstance(value, str) and value:
            by_lower.setdefault(value.lower(), value)

    spotify = getattr(cli, "_spotify", None)
    if spotify is not None:
        cached = getattr(spotify, "playlists", None)
        if isinstance(cached, dict):
            for cached_name in cached:
                _add(cached_name)
    repos = getattr(cli, "_repos", None)
    if repos is not None:
        try:
            for row in repos.conn.execute("SELECT name FROM playlists").fetchall():
                _add(row["name"])
            for mirrored in repos.spotify_playlists.list_all():
                _add(mirrored.get("name"))
        except Exception:
            logger.debug("Could not collect playlist names for suggestions", exc_info=True)
    return list(by_lower.values())


def report_playlist_miss(cli: "PlaylistCLI", name: str) -> None:
    """Render the not-found error + suggestions (the central miss path).

    Keeps the historical `logger.error` signal (the TUI counts ERROR records
    toward its honest "exited with errors" line; headless keeps its log line)
    and adds the actionable red panel via ui.error.
    """
    logger.error(f"Playlist '{name}' not found")
    error(
        playlist_not_found_message(name, suggest_playlist_names(name, collect_playlist_names(cli)))
    )


def warn_if_unknown_playlist(cli: "PlaylistCLI", name: str) -> None:
    """Non-blocking twin of ``report_playlist_miss`` for create-capable commands.

    /update and /plan legitimately target brand-new names (update is the only
    way a rotation playlist is born), so an unknown name must not fail — but a
    near-miss of an EXISTING name is probably a typo about to create a junk
    playlist. Warn with the suggestions and continue with the name as typed;
    a genuinely new name (no close match) stays quiet.
    """
    candidates = collect_playlist_names(cli)
    lowered = name.lower()
    if any(candidate.lower() == lowered for candidate in candidates):
        return
    suggestions = suggest_playlist_names(name, candidates)
    if suggestions:
        warning(
            f"'{name}' is a new playlist name — continuing with it as typed. "
            f"Did you mean: {', '.join(suggestions)}?"
        )
