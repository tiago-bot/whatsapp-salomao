"""Durable webhook inbox. One application worker, like the delivery outbox.

Keep completed event fingerprints for seven days, beyond every accepted replay
window. A retry changes attemptNumber, but not the event identity. No HTTP 2xx
is returned until the whole batch is committed.
"""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager


class WebhookStore:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
                    fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    received_at REAL NOT NULL, available_at REAL NOT NULL,
                    completed_at REAL)""")
                conn.execute("""CREATE INDEX IF NOT EXISTS webhook_pending
                    ON webhook_events(available_at, received_at) WHERE completed_at IS NULL""")
                conn.execute("CREATE INDEX IF NOT EXISTS webhook_completed ON webhook_events(completed_at)")
                yield conn
        finally:
            conn.close()

    def accept(self, events):
        now = time.time()
        accepted = 0
        with self.connect() as conn:
            for event in events:
                canonical = json.dumps({k: v for k, v in event.items() if k != "attemptNumber"},
                                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
                accepted += conn.execute("INSERT OR IGNORE INTO webhook_events VALUES(?,?,?,?,NULL)",
                    (fingerprint, canonical, now, now)).rowcount
        return accepted

    def next_event(self):
        with self.connect() as conn:
            row = conn.execute("""SELECT * FROM webhook_events WHERE completed_at IS NULL
                AND available_at<=? ORDER BY received_at, rowid LIMIT 1""", (time.time(),)).fetchone()
        return dict(row) if row else None

    def finish(self, fingerprint, *, success):
        with self.connect() as conn:
            if success:
                conn.execute("UPDATE webhook_events SET completed_at=? WHERE fingerprint=?", (time.time(), fingerprint))
            else:
                conn.execute("UPDATE webhook_events SET available_at=? WHERE fingerprint=?", (time.time() + 30, fingerprint))

    def health(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM webhook_events WHERE completed_at<?", (time.time() - 7 * 86400,))
            row = conn.execute("SELECT COUNT(*), MIN(received_at) FROM webhook_events WHERE completed_at IS NULL").fetchone()
        return {"pending": row[0], "oldest_age_seconds": max(0, time.time() - row[1]) if row[1] else 0}
