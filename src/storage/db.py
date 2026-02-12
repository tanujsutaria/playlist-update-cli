from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


def get_db_path(path: Optional[str] = None) -> Path:
    if path:
        return Path(path).expanduser()
    env_path = os.getenv("TUNR_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()
    project_root = Path(__file__).resolve().parent.parent.parent
    return project_root / "data" / "tunr.db"


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA busy_timeout=10000;")


@dataclass
class Database:
    path: Path

    def __init__(self, path: Optional[str | Path] = None) -> None:
        resolved = get_db_path(str(path)) if path else get_db_path()
        self.path = Path(resolved)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            _apply_pragmas(self._conn)
        return self._conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
