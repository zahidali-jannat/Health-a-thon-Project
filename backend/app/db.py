"""SQLite connection + versioned SQL migrations (app/migrations/NNNN_name.sql, applied once, in order)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .settings import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or get_settings().db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply any migrations not yet recorded in schema_migrations. Returns the versions applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in done:
            continue
        sql = path.read_text()
        record = f"INSERT INTO schema_migrations VALUES ('{version}', '{now_iso()}');"
        if sql.lstrip().startswith(FK_OFF_MARKER):
            _migrate_with_foreign_keys_off(conn, version, sql, record)
        else:
            conn.executescript(f"BEGIN;\n{sql}\n{record}\nCOMMIT;")
        applied.append(version)
    return applied


# A migration that rebuilds a table other tables point at must run with foreign keys off (SQLite's documented
# procedure). It still runs in one transaction, and it only commits if every foreign key is intact afterwards.
FK_OFF_MARKER = "-- migrate: foreign_keys=off"


def _migrate_with_foreign_keys_off(conn: sqlite3.Connection, version: str, sql: str, record: str) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"BEGIN;\n{sql}\n{record}")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(f"Migration {version} would break {len(broken)} foreign key(s): {[tuple(r) for r in broken[:5]]}")
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def reset(path: Path | str | None = None) -> sqlite3.Connection:
    """Delete the database file and rebuild it from migrations (development only)."""
    path = Path(path or get_settings().db_path)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = connect(path)
    migrate(conn)
    return conn
