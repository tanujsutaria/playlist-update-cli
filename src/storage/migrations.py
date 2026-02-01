from __future__ import annotations

import sqlite3
from typing import Iterable

from .schema import initial_schema, schema_v2, schema_v3


LATEST_VERSION = 3


def _get_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);")
    row = conn.execute("SELECT version FROM schema_version LIMIT 1;").fetchone()
    if row is None:
        return 0
    return int(row["version"]) if isinstance(row, sqlite3.Row) else int(row[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM schema_version;")
    conn.execute("INSERT INTO schema_version (version) VALUES (?);", (version,))


def _apply_statements(conn: sqlite3.Connection, statements: Iterable[str]) -> None:
    for statement in statements:
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)


def ensure_schema(conn: sqlite3.Connection) -> None:
    version = _get_version(conn)
    if version >= LATEST_VERSION:
        return

    if version == 0:
        _apply_statements(conn, initial_schema())
        version = 1

    if version == 1:
        _apply_statements(conn, schema_v2())
        version = 2

    if version == 2:
        _apply_statements(conn, schema_v3())
        _set_version(conn, LATEST_VERSION)
        return

    raise RuntimeError(f"Unsupported schema version {version}.")
