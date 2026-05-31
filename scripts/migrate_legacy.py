"""One-time CLI to migrate the legacy pickle store into the SQLite v4 schema.

Run from the repo root:

    python scripts/migrate_legacy.py [--data-dir data] [--model NAME]
                                     [--skip-reembed] [--dry-run]

This re-embeds every legacy track with the canonical SentenceTransformer model
so the stored embeddings match the search pipeline's model. It does NOT delete
any legacy pickle/npy files (that is T10).
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Standalone maintenance script (not part of the installed package): put the
# source root on sys.path so the bare module imports below resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storage.db import Database  # noqa: E402
from storage.legacy_migrate import DEFAULT_MODEL_NAME, migrate_legacy  # noqa: E402
from storage.migrations import ensure_schema  # noqa: E402
from storage.repos import Repositories  # noqa: E402


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy pickles into SQLite (v4).")
    parser.add_argument("--data-dir", default="data", help="Legacy data directory (default: data)")
    parser.add_argument(
        "--model",
        default=os.getenv("SEARCH_EMBEDDING_MODEL", DEFAULT_MODEL_NAME),
        help="Embedding model name (default: $SEARCH_EMBEDDING_MODEL or all-mpnet-base-v2)",
    )
    parser.add_argument(
        "--skip-reembed",
        action="store_true",
        help="Skip re-embedding tracks (only migrate metadata and history)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Roll back instead of committing; still report counts",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    db = Database()
    conn = db.connect()
    ensure_schema(conn)
    repos = Repositories(conn)

    try:
        report = migrate_legacy(
            repos,
            model_name=args.model,
            data_dir=args.data_dir,
            reembed=not args.skip_reembed,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
