"""Append-only, hash-chained audit ledger.

Every entry stores the hash of the previous entry, so editing or deleting any past
entry breaks the chain and verify() reports where.
"""
import hashlib
import json
import sqlite3
import time

from server import db

GENESIS = "0" * 64


def _hash(prev_hash, payload):
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def append(purchase_id, event, data):
    payload = json.dumps(
        {"ts": round(time.time(), 3), "purchase_id": purchase_id, "event": event, "data": data},
        sort_keys=True,
        default=str,
    )
    conn = db.connect_autocommit()
    try:
        conn.execute("BEGIN IMMEDIATE")  # serialize writers so the chain never forks
        try:
            row = conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else GENESIS
            entry_hash = _hash(prev, payload)
            conn.execute(
                "INSERT INTO audit (prev_hash, payload, hash) VALUES (?, ?, ?)",
                (prev, payload, entry_hash),
            )
            conn.execute("COMMIT")
            return entry_hash
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.close()


def verify():
    conn = db.connect_autocommit()
    try:
        prev = GENESIS
        count = 0
        for row in conn.execute("SELECT seq, prev_hash, payload, hash FROM audit ORDER BY seq"):
            if row["prev_hash"] != prev or _hash(prev, row["payload"]) != row["hash"]:
                return {"ok": False, "broken_at": row["seq"], "entries": count}
            prev = row["hash"]
            count += 1
        return {"ok": True, "entries": count}
    finally:
        conn.close()


def entries(limit=200, purchase_id=None, ascending=False):
    """Ledger entries as dicts, newest first by default."""
    conn = db.connect_autocommit()
    try:
        rows = conn.execute("SELECT seq, prev_hash, payload, hash FROM audit ORDER BY seq DESC").fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        body = json.loads(row["payload"])
        if purchase_id is not None and body.get("purchase_id") != purchase_id:
            continue
        out.append({"seq": row["seq"], "ts": body.get("ts"), "event": body.get("event"),
                    "purchase_id": body.get("purchase_id"), "data": body.get("data"),
                    "hash": row["hash"], "prev_hash": row["prev_hash"]})
        if len(out) >= int(limit):
            break
    if ascending:
        out.reverse()
    return out
