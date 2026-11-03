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
    error TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    prev_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


def _open(autocommit):
    conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None if autocommit else "")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
