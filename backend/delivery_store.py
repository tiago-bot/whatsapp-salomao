"""Durable local outbox. Run one worker and mount this DB on persistent storage.

Confirmed parts are never resent. A crash/timeout between remote acceptance and
local confirmation still requires reconciliation (HubSpot has no idempotency key).
"""
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from conversation_context import message_time


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
            conn.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (
                thread_id TEXT NOT NULL, message_id TEXT NOT NULL, created_at TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL,
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

    def quarantine(self, thread_id, message_id, reason):
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM deliveries WHERE thread_id=? AND message_id=?", (thread_id, message_id)).fetchone()
            if row:
                payload = {**json.loads(row["payload"]), "blocked_reason": reason}
                conn.execute("UPDATE deliveries SET complete=1, payload=? WHERE thread_id=? AND message_id=?",
                    (json.dumps(payload, ensure_ascii=False), thread_id, message_id))

    def remember_messages(self, thread_id, messages):
        """Cache only messages actually observed/sent, never unsent generations."""
        with self._connect() as conn:
            for message in messages:
                if not message.get("id") or not message.get("created_at") or not message.get("text"):
                    continue
                timestamp = message_time(message["created_at"])
                if timestamp is None:
                    continue
                conn.execute("""INSERT INTO conversation_messages VALUES(?,?,?,?,?)
                    ON CONFLICT(thread_id,message_id) DO UPDATE SET content=excluded.content""",
                    (str(thread_id), str(message["id"]), timestamp.isoformat(),
                     "user" if message.get("is_from_visitor") else "assistant", message["text"]))
            # Bound the context cache per thread; delivery receipts remain intact.
            conn.execute("""DELETE FROM conversation_messages WHERE thread_id=? AND message_id NOT IN
                (SELECT message_id FROM conversation_messages WHERE thread_id=?
                 ORDER BY created_at DESC, rowid DESC LIMIT 100)""", (str(thread_id), str(thread_id)))

    def conversation_messages(self, thread_id):
        with self._connect() as conn:
            rows = conn.execute("""SELECT * FROM conversation_messages WHERE thread_id=?
                ORDER BY created_at, rowid""", (str(thread_id),)).fetchall()
        return [{"id": r["message_id"], "created_at": r["created_at"], "text": r["content"],
                 "is_from_visitor": r["role"] == "user"} for r in rows]
