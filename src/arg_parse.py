import argparse
from typing import Tuple, Any

def setup_parsers() -> argparse.ArgumentParser:
    """Create and configure argument parser"""
    parser = argparse.ArgumentParser(description="Spotify Playlist Manager CLI")
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

    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Show statistics')
    stats_parser.add_argument('--playlist', help='Playlist name (optional)', default=None)

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
    
    # Clean command
    clean_parser = subparsers.add_parser('clean', help='Clean database by removing songs that no longer exist in Spotify')
    clean_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without actually removing')

    # Backup command
    backup_parser = subparsers.add_parser('backup', help='Backup the data directory')
    backup_parser.add_argument('backup_name', nargs='?', default=None, 
                               help='Optional name for the backup (defaults to timestamp)')

    # Restore command
    restore_parser = subparsers.add_parser('restore', help='Restore from a backup')
    restore_parser.add_argument('backup_name', 
                                help='Name of the backup to restore')

    # New "restore-previous-rotation" command
    restore_prev_parser = subparsers.add_parser('restore-previous-rotation',
                                                help='Restore a playlist to a previous rotation')
    restore_prev_parser.add_argument('playlist', help='Name of the playlist')
    restore_prev_parser.add_argument('offset', nargs='?', type=int, default=-1,
                                     help='How many generations back to restore from the current generation (default: -1). '
                                          'Example: -5 restores 5 generations back.')
                                          
    # List rotations command
    list_rotations_parser = subparsers.add_parser('list-rotations', help='List all rotations for a given playlist')
    list_rotations_parser.add_argument('playlist', help='Name of the playlist')

    return parser

def parse_args() -> Tuple[str, Any]:
    """Parse command line arguments and return command and args"""
    parser = setup_parsers()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return None, None
        
    return args.command, args 
