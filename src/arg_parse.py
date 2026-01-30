import argparse
import inspect
from typing import Tuple, Any, Optional

def setup_parsers(exit_on_error: bool = True) -> argparse.ArgumentParser:
    """Create and configure argument parser"""
    parser_kwargs = {"description": "Spotify Playlist Manager CLI"}
    if "exit_on_error" in inspect.signature(argparse.ArgumentParser).parameters:
        parser_kwargs["exit_on_error"] = exit_on_error
    parser = argparse.ArgumentParser(**parser_kwargs)
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Import command
    import_parser = subparsers.add_parser('import', help='Import songs from a file')
    import_parser.add_argument('file', help='Path to the input file')

    # Update command
    update_parser = subparsers.add_parser('update', help='Update a playlist')
    update_parser.add_argument('playlist', help='Name of the playlist')
    update_parser.add_argument('--count', type=int, default=10, help='Number of songs to include')
    update_parser.add_argument('--fresh-days', type=int, default=30, 
                              help='Prioritize songs not listened to in this many days (default: 30)')
    update_parser.add_argument('--dry-run', action='store_true',
                              help='Preview selected songs without updating Spotify')
    update_parser.add_argument('--score-strategy', choices=['local', 'web', 'hybrid'], default='local',
                              help='Match scoring strategy to rank candidates (default: local)')
    update_parser.add_argument('--query', default=None,
                              help='Optional theme query to build the playlist profile')

    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Show statistics')
    stats_parser.add_argument('--playlist', help='Playlist name (optional)', default=None)
    stats_parser.add_argument('--export', choices=['csv', 'json'], default=None,
                              help='Export stats to a file (csv or json)')
    stats_parser.add_argument('--output', help='Output file path (optional)', default=None)

    # View command
    view_parser = subparsers.add_parser('view', help='View current playlist contents')
    view_parser.add_argument('playlist', help='Name of the playlist')

    # Sync command
    sync_parser = subparsers.add_parser('sync', help='Sync entire database to a playlist')
    sync_parser.add_argument('playlist', help='Name of the playlist')

    # Extract command
    extract_parser = subparsers.add_parser('extract', help='Extract playlist contents to a CSV file')
    extract_parser.add_argument('playlist', help='Name of the playlist')
    extract_parser.add_argument('--output', help='Output file path (optional)', default=None)

    # Plan command
    plan_parser = subparsers.add_parser('plan', help='Preview future playlist rotations')
    plan_parser.add_argument('playlist', help='Name of the playlist')
    plan_parser.add_argument('--count', type=int, default=10, help='Number of songs per generation')
    plan_parser.add_argument('--fresh-days', type=int, default=30,
                             help='Prioritize songs not listened to in this many days (default: 30)')
    plan_parser.add_argument('--generations', type=int, default=3,
                             help='Number of future generations to preview (default: 3)')
    plan_parser.add_argument('--score-strategy', choices=['local', 'web', 'hybrid'], default='local',
                             help='Match scoring strategy to rank candidates (default: local)')
    plan_parser.add_argument('--query', default=None,
                             help='Optional theme query to build the playlist profile')

    # Diff command
    diff_parser = subparsers.add_parser('diff', help='Show playlist changes before applying update')
    diff_parser.add_argument('playlist', help='Name of the playlist')
    diff_parser.add_argument('--count', type=int, default=10, help='Number of songs to include')
    diff_parser.add_argument('--fresh-days', type=int, default=30,
                             help='Prioritize songs not listened to in this many days (default: 30)')
    diff_parser.add_argument('--score-strategy', choices=['local', 'web', 'hybrid'], default='local',
                             help='Match scoring strategy to rank candidates (default: local)')
    diff_parser.add_argument('--query', default=None,
                             help='Optional theme query to build the playlist profile')
    
    # Clean command
    clean_parser = subparsers.add_parser('clean', help='Clean database by removing songs that no longer exist in Spotify')
    clean_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without actually removing')

    # Search command
    search_parser = subparsers.add_parser('search', help='Deep web search for new songs')
    search_parser.add_argument('query', nargs='+', help='Search criteria (freeform)')

    # Backup command
    backup_parser = subparsers.add_parser('backup', help='Backup the data directory')
    backup_parser.add_argument('backup_name', nargs='?', default=None, 
                               help='Optional name for the backup (defaults to timestamp)')

    # Restore command
    restore_parser = subparsers.add_parser('restore', help='Restore from a backup')
    restore_parser.add_argument('backup_name', 
                                help='Name of the backup to restore')

    # Restore previous rotation command
    restore_prev_parser = subparsers.add_parser('restore-previous-rotation',
                                                help='Restore a playlist to a previous rotation')
    restore_prev_parser.add_argument('playlist', help='Name of the playlist')
    restore_prev_parser.add_argument('offset', nargs='?', type=int, default=-1,
                                     help='How many generations back to restore from the current generation (default: -1). '
                                          'Example: -5 restores 5 generations back.')

    # List rotations command
    list_rotations_parser = subparsers.add_parser('list-rotations', help='List all rotations for a given playlist')
    list_rotations_parser.add_argument('playlist', help='Name of the playlist')
    list_rotations_parser.add_argument('--generations', '-g', default='3',
                                       help='Number of generations to list, or "all" for all generations')

    # List backups command
    subparsers.add_parser('list-backups', help='List all available backups with their sizes and dates')

    # Auth commands
    subparsers.add_parser('auth-status', help='Show Spotify auth token status')
    subparsers.add_parser('auth-refresh', help='Refresh Spotify auth token if possible')

    # Interactive UI
    subparsers.add_parser('interactive', help='Launch the interactive UI')

    return parser

def parse_args() -> Tuple[str, Any]:
    """Parse command line arguments and return command and args"""
    parser = setup_parsers()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return None, None
        
    return args.command, args 


def parse_tokens(tokens: list[str]) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """Parse tokens for interactive /command input."""
    parser = setup_parsers(exit_on_error=False)
    if not tokens:
        return None, None, "No command provided."
    try:
        args = parser.parse_args(tokens)
    except argparse.ArgumentError as exc:
        return None, None, str(exc)
    except SystemExit:
        return None, None, "Invalid command."

    if not getattr(args, "command", None):
        return None, None, "No command provided."

    return args.command, args, None
