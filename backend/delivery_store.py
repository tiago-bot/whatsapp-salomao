"""Durable local outbox. Run one worker and mount this DB on persistent storage.

Confirmed parts are never resent. A crash/timeout between remote acceptance and
local confirmation still requires reconciliation (HubSpot has no idempotency key).
"""
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager


class DeliveryStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
                payload TEXT NOT NULL, sent_parts INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (thread_id, message_id))""")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, thread_id, message_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM deliveries WHERE thread_id=? AND message_id=?", (thread_id, message_id)).fetchone()
        return self._decode(row) if row else None

    @staticmethod
    def _decode(row):
        return {**dict(row), "payload": json.loads(row["payload"])}

    def enqueue(self, thread_id, message_id, payload):
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO deliveries(thread_id,message_id,payload) VALUES(?,?,?)",
                         (thread_id, message_id, json.dumps(payload, ensure_ascii=False)))
        return self.get(thread_id, message_id)

    def pending(self, thread_id):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM deliveries WHERE thread_id=? AND complete=0 ORDER BY rowid", (thread_id,)).fetchall()
        return [self._decode(row) for row in rows]

    def confirm_part(self, thread_id, message_id, count):
        with self._connect() as conn:
            conn.execute("UPDATE deliveries SET sent_parts=? WHERE thread_id=? AND message_id=?", (count, thread_id, message_id))

    def complete(self, thread_id, message_id):
        with self._connect() as conn:
            conn.execute("UPDATE deliveries SET complete=1 WHERE thread_id=? AND message_id=?", (thread_id, message_id))
