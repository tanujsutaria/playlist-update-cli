from __future__ import annotations

import argparse
import difflib
import inspect
from typing import IO, Any, Optional, Sequence, Tuple, Type


def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"{value} is not a positive integer")
    return ivalue


def _non_negative_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"{value} must not be negative")
    return ivalue


def _add_cohort_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the mutually-exclusive backfill cohort flags (/enrich, /sonic).

    No flag targets the whole library in track-id order (today's behavior);
    a cohort points the backfill at the tracks that actually feed /taste,
    /find and rotation.
    """
    cohort = parser.add_mutually_exclusive_group()
    cohort.add_argument(
        "--played",
        action="store_true",
        help="Only tracks with listen history (the /listen-sync ledger)",
    )
    cohort.add_argument(
        "--liked",
        action="store_true",
        help="Only Liked Songs (the /pull mirror)",
    )
    cohort.add_argument(
        "--rotation",
        action="store_true",
        help="Only tracks that have appeared in rotation generations",
    )
    cohort.add_argument(
        "--playlist",
        metavar="NAME",
        default=None,
        help="Only tracks in the named playlist (the /pull mirror)",
    )


class HelpText(str):
    """Marker type: a parse_tokens "error" that is really help output.

    The interactive UI renders HelpText as a Help panel instead of an Error
    panel; it stays a plain str for every other caller.
    """


class _HelpRequested(Exception):
    """Internal: raised by _NoExitArgumentParser when --help/-h is parsed."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


# Subcommands kept out of did-you-mean suggestions: /interactive is a no-op
# inside the UI, /debug is intercepted by the meta router, /rotate-played is a
# deprecated alias.
_UNSUGGESTED_COMMANDS = {"interactive", "debug", "rotate-played"}


def unknown_command_message(name: str, candidates: Sequence[str]) -> str:
    """Build the "Unknown command" error, with a did-you-mean suggestion."""
    candidate_list = list(candidates)
    if name in candidate_list:
        # The command exists but reached the parser with arguments it doesn't
        # take (meta commands like /clear are exact-match routed) — suggesting
        # "did you mean /<itself>?" would be absurd.
        return f"/{name} doesn't take those arguments. Try /help {name}."
    message = f"Unknown command /{name}."
    matches = difflib.get_close_matches(name, candidate_list, n=1, cutoff=0.6)
    if matches:
        message += f" Did you mean /{matches[0]}?"
    message += " Type /help for the list."
    return message


class _NoExitArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that raises instead of exiting (for interactive UI)."""

    def error(self, message: str) -> None:
        # Carry the usage signature so missing-arg errors are actionable.
        raise argparse.ArgumentError(None, f"{message}\n{self.format_usage()}")

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if message:
            raise argparse.ArgumentError(None, message)
        raise argparse.ArgumentError(None, "Invalid command.")

    def print_help(self, file: Optional[IO[str]] = None) -> None:
        # Subparsers inherit this class via add_subparsers' parser_class
        # default, so `/update --help` and `-h` raise instead of printing to
        # stdout (which the TUI swallows) and then exiting.
        raise _HelpRequested(self.format_help())


def setup_parsers(
    exit_on_error: bool = True,
    parser_class: Type[argparse.ArgumentParser] = argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Create and configure argument parser"""
    # prog: usage lines should read "tunr update ...", not the module name
    # ("main.py update ...") that argparse infers from sys.argv inside the TUI.
    parser_kwargs = {"prog": "tunr", "description": "Spotify Playlist Manager CLI"}
    if "exit_on_error" in inspect.signature(argparse.ArgumentParser).parameters:
        parser_kwargs["exit_on_error"] = exit_on_error
    parser = parser_class(**parser_kwargs)
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Import command
    import_parser = subparsers.add_parser("import", help="Legacy: import songs from a file")
    import_parser.add_argument("file", help="Path to the input file")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update a playlist")
    update_parser.add_argument("playlist", help="Name of the playlist")
    update_parser.add_argument(
        "--count", type=_positive_int, default=10, help="Number of songs to include"
    )
    update_parser.add_argument(
        "--fresh-days",
        type=_non_negative_int,
        default=30,
        help="Prioritize songs not listened to in this many days (default: 30)",
    )
    update_parser.add_argument(
        "--dry-run", action="store_true", help="Preview selected songs without updating Spotify"
    )
    update_parser.add_argument(
        "--score-strategy",
        choices=["local", "web", "hybrid"],
        default="local",
        help="Match scoring strategy to rank candidates (default: local)",
    )
    update_parser.add_argument(
        "--query", default=None, help="Optional theme query to build the playlist profile"
    )

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument("--playlist", help="Playlist name (optional)", default=None)
    stats_parser.add_argument(
        "--export",
        choices=["csv", "json"],
        default=None,
        help="Export stats to a file (csv or json)",
    )
    stats_parser.add_argument("--output", help="Output file path (optional)", default=None)
    stats_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of tables"
    )

    # Status command (read-only, offline): one screen answering "what state
    # is my tunr in?" — storage counts, auth, data coverage, config.
    subparsers.add_parser(
        "status", help="One-screen status: storage, auth, data coverage, config (offline)"
    )

    # Profile command (library visualization)
    profile_parser = subparsers.add_parser(
        "profile", help="Visualize your library: top artists + rotation coverage"
    )
    profile_parser.add_argument(
        "--top",
        type=_positive_int,
        default=15,
        help="Number of top artists to chart (default: 15)",
    )
    profile_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of tables"
    )

    # Taste command (current sonic/semantic preference card)
    taste_parser = subparsers.add_parser(
        "taste", help="Show your current taste profile (representative tracks)"
    )
    taste_parser.add_argument(
        "--top",
        type=_positive_int,
        default=8,
        help="Number of representative tracks to show (default: 8)",
    )
    taste_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of tables"
    )

    # View command
    view_parser = subparsers.add_parser("view", help="View current playlist contents")
    view_parser.add_argument("playlist", help="Name of the playlist")

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Sync entire database to a playlist")
    sync_parser.add_argument("playlist", help="Name of the playlist")

    # Extract command
    extract_parser = subparsers.add_parser(
        "extract", help="Extract playlist contents to a CSV file"
    )
    extract_parser.add_argument("playlist", help="Name of the playlist")
    extract_parser.add_argument("--output", help="Output file path (optional)", default=None)

    # Plan command
    plan_parser = subparsers.add_parser("plan", help="Preview future playlist rotations")
    plan_parser.add_argument("playlist", help="Name of the playlist")
    plan_parser.add_argument(
        "--count", type=_positive_int, default=10, help="Number of songs per generation"
    )
    plan_parser.add_argument(
        "--fresh-days",
        type=_non_negative_int,
        default=30,
        help="Prioritize songs not listened to in this many days (default: 30)",
    )
    plan_parser.add_argument(
        "--generations",
        type=_positive_int,
        default=3,
        help="Number of future generations to preview (default: 3)",
    )
    plan_parser.add_argument(
        "--score-strategy",
        choices=["local", "web", "hybrid"],
        default="local",
        help="Match scoring strategy to rank candidates (default: local)",
    )
    plan_parser.add_argument(
        "--query", default=None, help="Optional theme query to build the playlist profile"
    )

    # Diff command
    diff_parser = subparsers.add_parser("diff", help="Show playlist changes before applying update")
    diff_parser.add_argument("playlist", help="Name of the playlist")
    diff_parser.add_argument(
        "--count", type=_positive_int, default=10, help="Number of songs to include"
    )
    diff_parser.add_argument(
        "--fresh-days",
        type=_non_negative_int,
        default=30,
        help="Prioritize songs not listened to in this many days (default: 30)",
    )
    diff_parser.add_argument(
        "--score-strategy",
        choices=["local", "web", "hybrid"],
        default="local",
        help="Match scoring strategy to rank candidates (default: local)",
    )
    diff_parser.add_argument(
        "--query", default=None, help="Optional theme query to build the playlist profile"
    )

    # Clean command
    clean_parser = subparsers.add_parser(
        "clean", help="Clean database by removing songs that no longer exist in Spotify"
    )
    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without actually removing",
    )

    # Search command
    search_parser = subparsers.add_parser("search", help="Deep web search for new songs")
    search_parser.add_argument("query", nargs="+", help="Search criteria (freeform)")
    search_parser.add_argument(
        "--to",
        dest="to_playlist",
        metavar="NAME",
        default=None,
        help="Add the results straight to playlist NAME (skips the interactive prompts)",
    )
    search_parser.add_argument(
        "--replace",
        action="store_true",
        help="With --to: swap the playlist's contents (keeps the playlist/ID); default is to append",
    )
    search_parser.add_argument(
        "--save",
        action="store_true",
        help="Mark the results as accepted in the local library",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="With --to: only add the top N ranked results (default: all)",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the ranked results as machine-readable JSON (suppresses tables)",
    )

    # Find command (flagship: deep search re-ranked by your taste)
    find_parser = subparsers.add_parser(
        "find", help="Deep search re-ranked by your taste; optionally add to a playlist"
    )
    find_parser.add_argument("query", nargs="+", help="Search criteria (freeform)")
    find_parser.add_argument(
        "--taste-weight",
        dest="taste_weight",
        type=float,
        default=0.5,
        metavar="W",
        help="Blend 0..1: 1=pure taste, 0=pure relevance (default 0.5)",
    )
    find_parser.add_argument(
        "--to",
        dest="to_playlist",
        metavar="NAME",
        default=None,
        help="Add the ranked results to playlist NAME (otherwise preview only)",
    )
    find_parser.add_argument(
        "--replace",
        action="store_true",
        help="With --to: swap the playlist's contents (keeps the playlist/ID); default is append",
    )
    find_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="With --to: only add the top N ranked results (default: all)",
    )
    find_parser.add_argument(
        "--json", action="store_true", help="Emit the ranked results as machine-readable JSON"
    )

    # Undo command (reverts the last playlist write this session)
    subparsers.add_parser("undo", help="Undo the last playlist change made this session")

    # Enrich command (semantic context backfill + re-embed)
    enrich_parser = subparsers.add_parser(
        "enrich", help="Backfill semantic context + re-embed tracks (genre/mood/era; not acoustic)"
    )
    enrich_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        metavar="N",
        help="Max tracks to enrich (default 25; each is a deep-search call)",
    )
    enrich_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the tracks that would be enriched without calling out or writing",
    )
    enrich_parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=8,
        metavar="N",
        help="Parallel deep-search workers (default 8); writes stay serialized",
    )
    _add_cohort_flags(enrich_parser)

    # Sonic command (acoustic feature backfill from AcousticBrainz)
    sonic_parser = subparsers.add_parser(
        "sonic", help="Backfill acoustic features from AcousticBrainz (no audio; MBID-keyed)"
    )
    sonic_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=50,
        metavar="N",
        help="Max tracks to look up (default 50; MusicBrainz is rate-limited to ~1/sec)",
    )
    sonic_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the tracks that would be looked up without calling out or writing",
    )
    _add_cohort_flags(sonic_parser)

    # Debug command (non-interactive)
    debug_parser = subparsers.add_parser("debug", help="Show debug info (last search or track)")
    debug_parser.add_argument(
        "topic",
        nargs="?",
        choices=["last", "track"],
        default="last",
        help="Debug topic (last or track)",
    )
    debug_parser.add_argument("value", nargs="?", default=None, help="Track ID for debug track")
    debug_parser.add_argument(
        "--format", choices=["json", "table"], default="json", help="Output format (default: json)"
    )

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest tracks into the cache")
    ingest_parser.add_argument(
        "source", choices=["liked", "playlist", "top", "recent"], help="Source to ingest from"
    )
    ingest_parser.add_argument(
        "name", nargs="?", default=None, help="Optional name (required for playlist source)"
    )
    ingest_parser.add_argument(
        "--time-range",
        choices=["short_term", "medium_term", "long_term"],
        default="medium_term",
        help="Time range for top tracks (default: medium_term)",
    )

    # Listen ledger sync
    listen_parser = subparsers.add_parser(
        "listen-sync", help="Sync recently played tracks into the listen ledger"
    )
    listen_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=50,
        help="Number of recent plays to pull (default: 50)",
    )

    # GDPR extended-streaming-history import (zip / folder / single json)
    import_history_parser = subparsers.add_parser(
        "import-history",
        help="Import a Spotify GDPR extended-streaming-history export into the listen ledger",
    )
    import_history_parser.add_argument(
        "path",
        help="Export .zip, the extracted folder, or one Streaming_History_Audio*.json file",
    )
    import_history_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be imported without writing anything",
    )
    import_history_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the import summary as machine-readable JSON",
    )

    # Pull command (read-only mirror of the user's real Spotify library)
    pull_parser = subparsers.add_parser(
        "pull", help="Mirror your Spotify playlists + liked songs locally (read-only)"
    )
    pull_scope = pull_parser.add_mutually_exclusive_group()
    pull_scope.add_argument("--liked-only", action="store_true", help="Only sync liked songs")
    pull_scope.add_argument("--playlists-only", action="store_true", help="Only sync playlists")
    pull_parser.add_argument(
        "--full",
        action="store_true",
        help="Re-fetch every playlist even when its snapshot_id is unchanged",
    )
    pull_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of tables"
    )

    # Rotation based on listen ledger
    rotate_played_parser = subparsers.add_parser(
        "rotate-played", help="Legacy: rotate by listen history (use rotate)"
    )
    rotate_played_parser.add_argument("playlist", help="Name of the playlist")
    rotate_played_parser.add_argument(
        "--max-replace",
        type=int,
        default=None,
        help="Maximum number of played tracks to replace (default: all)",
    )

    # Consolidated rotate command (replaces the legacy rotate-played twin)
    rotate_parser = subparsers.add_parser(
        "rotate", help="Rotate a playlist (replace tracks you've already played)"
    )
    rotate_parser.add_argument("playlist", help="Name of the playlist")
    rotate_parser.add_argument(
        "--max-replace",
        type=int,
        default=None,
        help="Maximum number of played tracks to replace (default: all)",
    )

    # Backup command
    backup_parser = subparsers.add_parser("backup", help="Backup the data directory")
    backup_parser.add_argument(
        "backup_name",
        nargs="?",
        default=None,
        help="Optional name for the backup (defaults to timestamp)",
    )

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore from a backup")
    restore_parser.add_argument("backup_name", help="Name of the backup to restore")

    # Restore previous rotation command
    restore_prev_parser = subparsers.add_parser(
        "restore-previous-rotation", help="Restore a playlist to a previous rotation"
    )
    restore_prev_parser.add_argument("playlist", help="Name of the playlist")
    restore_prev_parser.add_argument(
        "offset",
        nargs="?",
        type=int,
        default=-1,
        help="How many generations back to restore from the current generation (default: -1). "
        "Example: -5 restores 5 generations back.",
    )

    # List rotations command
    list_rotations_parser = subparsers.add_parser(
        "list-rotations", help="List all rotations for a given playlist"
    )
    list_rotations_parser.add_argument("playlist", help="Name of the playlist")
    list_rotations_parser.add_argument(
        "--generations",
        "-g",
        default="3",
        help='Number of generations to list, or "all" for all generations',
    )

    # List backups command
    subparsers.add_parser(
        "list-backups", help="List all available backups with their sizes and dates"
    )

    # Auth commands
    subparsers.add_parser("auth-status", help="Show Spotify auth token status")
    subparsers.add_parser("auth-refresh", help="Refresh Spotify auth token if possible")
    auth_reset_parser = subparsers.add_parser(
        "auth-reset",
        help="Delete the cached Spotify token so the next command re-opens consent",
    )
    auth_reset_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the reset (without this flag nothing is deleted)",
    )

    # Embed command (offline lexical embedding backfill)
    embed_parser = subparsers.add_parser(
        "embed", help="Backfill lexical embeddings for tracks that lack one (offline, local model)"
    )
    embed_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Max tracks to embed (default: all missing)",
    )
    embed_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the tracks that would be embedded without writing anything",
    )

    # Similar command (local more-like-this over stored embeddings)
    similar_parser = subparsers.add_parser(
        "similar", help="More-like-this from stored embeddings (offline; track id or free text)"
    )
    similar_parser.add_argument(
        "query", nargs="+", help="A track id (artist|||name) or free text to match against"
    )
    similar_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        metavar="N",
        help="Number of neighbors to show (default: 10)",
    )
    similar_parser.add_argument(
        "--to",
        dest="to_playlist",
        metavar="NAME",
        default=None,
        help="Add the matches to playlist NAME (undoable via /undo)",
    )
    similar_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of tables"
    )

    # Interactive UI
    subparsers.add_parser("interactive", help="Launch the interactive UI")

    # Doctor command (read-only, offline): integrity + consistency audit of
    # the SQLite system of record, one ok/warn/fail row per check.
    doctor_parser = subparsers.add_parser(
        "doctor", help="Audit the local database: integrity, schema, orphans, backups (offline)"
    )
    doctor_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of tables"
    )

    return parser


def parse_args() -> Tuple[str, Any]:
    """Parse command line arguments and return command and args"""
    parser = setup_parsers()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return None, None

    return args.command, args


def _subcommand_option_strings(parser: argparse.ArgumentParser, name: str) -> list[str]:
    """Option strings (--count, --dry-run, ...) of the named subcommand."""
    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
    )
    if sub_action is None:
        return []
    sub = sub_action.choices.get(name)
    if sub is None:
        return []
    return [option for action in sub._actions for option in action.option_strings]


def _flag_did_you_mean(tokens: Sequence[str]) -> list[str]:
    """Did-you-mean hints for mistyped flags ("--cout" -> "--count").

    Detection goes through ``parse_known_args`` extras — NEVER string-match
    argparse's error wording (it varies across the py3.9-3.12 matrix). Any
    other parse failure (missing positional, bad int, ...) makes
    ``parse_known_args`` raise too, and then there is no flag to correct.
    """
    if not tokens:
        return []
    parser = setup_parsers(exit_on_error=False, parser_class=_NoExitArgumentParser)
    try:
        _, extras = parser.parse_known_args(list(tokens))
    except (argparse.ArgumentError, _HelpRequested):
        return []
    option_strings = _subcommand_option_strings(parser, tokens[0])
    if not option_strings:
        return []
    hints = []
    for extra in extras:
        if not extra.startswith("-"):
            continue
        flag = extra.split("=", 1)[0]
        matches = difflib.get_close_matches(flag, option_strings, n=1, cutoff=0.6)
        if matches:
            hints.append(f"Unrecognized {flag} — did you mean {matches[0]}?")
    return hints


def parse_tokens(
    tokens: list[str],
    *,
    extra_commands: Sequence[str] = (),
) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """Parse tokens for interactive /command input.

    Returns (command, args, error). ``error`` is a plain str for real errors
    or a ``HelpText`` when the user asked for --help/-h. ``extra_commands``
    extends the did-you-mean candidates (e.g. with the UI's meta commands).
    """
    parser = setup_parsers(exit_on_error=False, parser_class=_NoExitArgumentParser)
    if not tokens:
        return None, None, "No command provided."

    # Unknown-command detection BEFORE parse_args: never string-match argparse
    # wording (it varies across the py3.9-3.12 matrix).
    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
    )
    if sub_action is not None and not tokens[0].startswith("-"):
        if tokens[0] not in sub_action.choices:
            candidates = [c for c in sub_action.choices if c not in _UNSUGGESTED_COMMANDS]
            candidates.extend(extra_commands)
            return None, None, unknown_command_message(tokens[0], candidates)

    try:
        args = parser.parse_args(tokens)
    except _HelpRequested as exc:
        return None, None, HelpText(exc.text)
    except argparse.ArgumentError as exc:
        message = str(exc)
        hints = _flag_did_you_mean(tokens)
        if hints:
            message = "\n".join([message, *hints])
        return None, None, message

    if not getattr(args, "command", None):
        return None, None, "No command provided."

    return args.command, args, None
