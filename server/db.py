"""SQLite storage. Two tables: purchases (current state) and audit (append-only ledger)."""
import sqlite3
from contextlib import contextmanager

from server import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    merchant TEXT NOT NULL,
    items TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    rationale TEXT NOT NULL,
    decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    status TEXT NOT NULL,
    paypal_order_id TEXT,
    paypal_capture_id TEXT,
    approve_url TEXT,
    error TEXT,
    checks TEXT,
    mandate_id TEXT,
    verifier TEXT
);
CREATE TABLE IF NOT EXISTS mandates (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    task TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    prev_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


def _migrate(conn):
    """Bring databases created by older versions up to date."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(purchases)")}
    for name in ("checks", "mandate_id", "verifier"):
        if name not in cols:
            conn.execute("ALTER TABLE purchases ADD COLUMN %s TEXT" % name)


def _open(autocommit):
    conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None if autocommit else "")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


@contextmanager
def connect():
    """Normal transactional connection: commits on success, rolls back on error."""
    conn = _open(autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def connect_autocommit():
    """Autocommit connection; the caller manages BEGIN/COMMIT and must close it."""
    return _open(autocommit=True)
