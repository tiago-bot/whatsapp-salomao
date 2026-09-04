"""Durable local outbox. Run one worker and mount this DB on persistent storage.

Confirmed parts are never resent. A crash/timeout between remote acceptance and
local confirmation still requires reconciliation (HubSpot has no idempotency key).
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from conversation_context import message_time
from process_lock import thread_lock


class DeliveryStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
                payload TEXT NOT NULL, sent_parts INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 0,
                handoff_note_state TEXT NOT NULL DEFAULT 'pending',
                handoff_note_id TEXT,
                PRIMARY KEY (thread_id, message_id))""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(deliveries)")}
            if "handoff_note_state" not in columns:
                conn.execute("ALTER TABLE deliveries ADD COLUMN handoff_note_state TEXT NOT NULL DEFAULT 'pending'")
            if "handoff_note_id" not in columns:
                conn.execute("ALTER TABLE deliveries ADD COLUMN handoff_note_id TEXT")
            conn.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (
                thread_id TEXT NOT NULL, message_id TEXT NOT NULL, created_at TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL,
                PRIMARY KEY (thread_id, message_id))""")
            existing = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_attempts'").fetchone()
            conn.execute("""CREATE TABLE IF NOT EXISTS delivery_attempts (
                thread_id TEXT NOT NULL, message_id TEXT NOT NULL, part INTEGER NOT NULL,
                state TEXT NOT NULL, attempted_at TEXT NOT NULL, remote_id TEXT,
                PRIMARY KEY(thread_id,message_id,part))""")
            if not existing:
                # Old pending rows have no proof that their last POST failed.
                conn.execute("""INSERT OR IGNORE INTO delivery_attempts
                    (thread_id,message_id,part,state,attempted_at)
                    SELECT thread_id,message_id,sent_parts,'uncertain',? FROM deliveries
                    WHERE complete=0""", (datetime.now(timezone.utc).isoformat(),))

    def thread_lock(self, thread_id):
        return thread_lock(self.path, thread_id)

    def restore_memory(self, thread_id, messages):
        """Lost receipts cannot be reconstructed from conversation text alone."""
        self.remember_messages(thread_id, messages)
        with self._connect() as conn:
            for message in messages:
                if message.get("is_from_visitor") and message.get("id"):
                    conn.execute("INSERT OR IGNORE INTO deliveries(thread_id,message_id,payload,complete) VALUES(?,?,?,1)",
                        (str(thread_id), str(message["id"]), json.dumps({
                            "blocked_reason": "restored_history_without_receipt_requires_review"})))

    def begin_part(self, thread_id, message_id, part):
        """Commit intent BEFORE sending. An interrupted intent is never replayed."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT complete,sent_parts FROM deliveries WHERE thread_id=? AND message_id=?",
                               (thread_id, message_id)).fetchone()
            if not row or row["complete"] or row["sent_parts"] > part:
                return "confirmed"
            if row["sent_parts"] != part:
                return "out_of_order"
            attempt = conn.execute("SELECT state FROM delivery_attempts WHERE thread_id=? AND message_id=? AND part=?",
                                   (thread_id, message_id, part)).fetchone()
            if attempt and attempt["state"] != "retryable":
                return "uncertain"
            conn.execute("""INSERT INTO delivery_attempts VALUES(?,?,?,?,?,NULL)
                ON CONFLICT(thread_id,message_id,part) DO UPDATE SET state='sending',attempted_at=excluded.attempted_at""",
                (thread_id, message_id, part, "sending", datetime.now(timezone.utc).isoformat()))
        return "send"

    def failed_part(self, thread_id, message_id, part, *, retryable=False):
        with self._connect() as conn:
            conn.execute("UPDATE delivery_attempts SET state=? WHERE thread_id=? AND message_id=? AND part=? AND state='sending'",
                ("retryable" if retryable else "uncertain", thread_id, message_id, part))

    def begin_handoff_note(self, thread_id, message_id):
        """Durably claim the non-idempotent note POST before calling HubSpot."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT complete,handoff_note_state,handoff_note_id FROM deliveries
                WHERE thread_id=? AND message_id=?""", (thread_id, message_id)).fetchone()
            if not row:
                return "missing"
            if row["handoff_note_state"] == "confirmed":
                return "confirmed"
            if row["handoff_note_state"] not in {"pending", "retryable"}:
                return "uncertain"
            conn.execute("""UPDATE deliveries SET handoff_note_state='sending'
                WHERE thread_id=? AND message_id=?""", (thread_id, message_id))
        return "send"

    def failed_handoff_note(self, thread_id, message_id, *, retryable=False):
        with self._connect() as conn:
            conn.execute("""UPDATE deliveries SET handoff_note_state=?
                WHERE thread_id=? AND message_id=? AND handoff_note_state='sending'""",
                ("retryable" if retryable else "uncertain", thread_id, message_id))

    def confirm_handoff_note(self, thread_id, message_id, note):
        remote_id = (note or {}).get("id")
        if not remote_id:
            raise ValueError("missing_handoff_note_receipt")
        with self._connect() as conn:
            conn.execute("""UPDATE deliveries SET handoff_note_state='confirmed',handoff_note_id=?
                WHERE thread_id=? AND message_id=? AND handoff_note_state='sending'""",
                (str(remote_id), thread_id, message_id))

    def update_payload(self, thread_id, message_id, fields):
        """Persist generated handoff metadata before its first external write."""
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM deliveries WHERE thread_id=? AND message_id=?",
                               (thread_id, message_id)).fetchone()
            if not row:
                return None
            payload = {**json.loads(row["payload"]), **fields}
            conn.execute("UPDATE deliveries SET payload=? WHERE thread_id=? AND message_id=?",
                (json.dumps(payload, ensure_ascii=False), thread_id, message_id))
        return self.get(thread_id, message_id)

    def confirm_delivery_part(self, thread_id, message_id, part, sent, text):
        """Receipt, progress and delivered context share one durable transaction."""
        remote_id = sent.get("id")
        if not remote_id:
            raise ValueError("missing_delivery_receipt")
        timestamp = message_time(sent.get("createdAt")) or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("UPDATE delivery_attempts SET state='confirmed',remote_id=? WHERE thread_id=? AND message_id=? AND part=?",
                (str(remote_id), thread_id, message_id, part))
            conn.execute("UPDATE deliveries SET sent_parts=MAX(sent_parts,?) WHERE thread_id=? AND message_id=?",
                (part + 1, thread_id, message_id))
            conn.execute("INSERT OR IGNORE INTO conversation_messages VALUES(?,?,?,?,?)",
                (str(thread_id), str(remote_id), timestamp.isoformat(), "assistant", text))

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
            for input_id in payload.get("source_message_ids", []):
                if str(input_id) != str(message_id):
                    conn.execute("INSERT OR IGNORE INTO deliveries(thread_id,message_id,payload,complete) VALUES(?,?,?,1)",
                        (thread_id, str(input_id), json.dumps({"coalesced_into": str(message_id)})))
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
