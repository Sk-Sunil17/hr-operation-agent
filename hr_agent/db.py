"""SQLite access.

One connection factory, one schema loader, and small helpers. Everything else in
the codebase goes through these so that tenant scoping and row factories are
consistent, and so the audit table only ever gets appended to.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = "2"

# Paths whose schema this process has already created. Running the schema script
# is idempotent but not free -- it is twenty CREATE TABLE statements, and the API
# was paying for them on every request, which was most of the cost of a cheap one.
_INITIALISED: set[str] = set()


def now() -> str:
    """UTC, ISO-8601, second precision. Every timestamp in the database uses this."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else config.DB_PATH
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL" if str(target) != ":memory:" else "PRAGMA journal_mode = MEMORY")
    return conn


def init(conn: sqlite3.Connection) -> None:
    """Create the schema if it is not there. Safe to call repeatedly."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )


def open_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Connect, creating the schema the first time this process sees the file.

    An in-memory database is private to its connection, so it is always created.
    """
    conn = connect(path)
    key = str(path if path is not None else config.DB_PATH)
    if key == ":memory:" or key not in _INITIALISED:
        init(conn)
        _INITIALISED.add(key)
    return conn


def forget_initialised() -> None:
    """Test hook: pretend this process has never seen any database."""
    _INITIALISED.clear()


# ------------------------------------------------------------------ helpers


def insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    """Insert one row. JSON-valued columns may be passed as dict or list."""
    prepared = {k: (json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
    columns = ", ".join(prepared)
    marks = ", ".join("?" for _ in prepared)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(prepared.values()))


def one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, tuple(params)).fetchone()


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, tuple(params)).fetchall()


def loads(value: Any, default: Any = None) -> Any:
    """Read a JSON column without making every caller handle NULL."""
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in rows(conn, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
