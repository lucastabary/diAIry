"""SQLite connection management.

One file holds the fact log, the full-text index and the vector index. No
server, no daemon, nothing to install beyond the Python wheel -- which is what
makes "clone it and run it on your Mac" actually true.

The vector extension is optional at runtime. If it cannot be loaded, search
degrades to full-text only and says so, rather than the whole application
refusing to start.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from diairy.core.errors import StoreError
from diairy.store.migrations import migrate

_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 10000",
)


def load_vector_extension(connection: sqlite3.Connection) -> bool:
    """Load sqlite-vec into ``connection``. Returns whether it worked.

    Failure is not fatal: some Python builds ship without extension loading
    support. The caller falls back to full-text search and reports the
    degradation instead of crashing.
    """
    try:
        import sqlite_vec  # noqa: PLC0415 -- optional, loaded on demand
    except ImportError:
        return False
    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        return True
    except (AttributeError, sqlite3.OperationalError):
        return False
    finally:
        with contextlib.suppress(AttributeError):
            connection.enable_load_extension(False)


def connect(path: Path, *, apply_migrations: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the diAIry database at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(path, isolation_level=None)
    except sqlite3.Error as exc:
        message = f"Could not open the database at {path}: {exc}"
        raise StoreError(message) from exc
    connection.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        connection.execute(pragma)
    load_vector_extension(connection)
    if apply_migrations:
        migrate(connection)
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in a single transaction, rolling back on any exception."""
    connection.execute("BEGIN")
    try:
        yield connection
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
