"""Supabase checkpoint of channel-observed messages, separate from LLM drafts.

Uses the existing salomao_sessions.metadata column and an exclusive namespace.
This is a context backup, NOT the authority for delivery/idempotency or a lock.
"""
from datetime import datetime, timezone
import hashlib
import json
import logging
import time

import httpx

from config import SUPABASE_URL, SUPABASE_KEY
from conversation_context import message_time

logger = logging.getLogger("salomao.memory")


class SupabaseConversationMemory:
    def __init__(self, client=None):
        self.client = client or httpx.Client(base_url=(SUPABASE_URL or "").rstrip("/") + "/rest/v1/",
            headers={"apikey": SUPABASE_KEY or "", "Authorization": "Bearer " + (SUPABASE_KEY or "")}, timeout=5)
        self.retry_at = 0

    @staticmethod
    def session_id(thread_id):
        return "whatsapp_salomao_memory_" + str(thread_id)

    @staticmethod
    def clean_messages(messages):
        cleaned = {}
        for message in messages[-100:]:
            if not isinstance(message, dict):
                continue
            timestamp = message_time(message.get("created_at"))
            if not message.get("id") or not timestamp or not message.get("text"):
                continue
            if not isinstance(message.get("is_from_visitor"), bool):
                continue
            cleaned[str(message["id"])] = {"id": str(message["id"]), "text": str(message["text"])[:12000],
                "created_at": timestamp.isoformat(), "is_from_visitor": message["is_from_visitor"]}
        return sorted(cleaned.values(), key=lambda m: (m["created_at"], m["id"]))

    @staticmethod
    def digest(messages):
        return hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _failed(self, exc):
        self.retry_at = time.monotonic() + 60
        logger.warning("Memoria remota indisponivel; cache local preservado", extra={
            "event": "memory.unavailable", "error_type": type(exc).__name__})

    def load(self, thread_id):
        if time.monotonic() < self.retry_at:
            return []
        try:
            response = self.client.get("salomao_sessions", params={"select": "metadata",
                "session_id": "eq." + self.session_id(thread_id), "limit": "1"})
            response.raise_for_status()
            rows = response.json()
            snapshot = rows[0].get("metadata", {}) if rows else {}
            if snapshot.get("version") != 1 or snapshot.get("thread_id") != str(thread_id):
                return []
            messages = self.clean_messages(snapshot.get("observed_messages", []))
            if snapshot.get("digest") != self.digest(messages):
                raise ValueError("invalid_memory_checkpoint")
            logger.info("Memoria remota recuperada", extra={"event": "memory.restored",
                "thread_id": str(thread_id), "context_messages": len(messages)})
            return messages
        except Exception as exc:
            self._failed(exc)
            return []

    def save(self, thread_id, messages):
        if time.monotonic() < self.retry_at:
            return False
        messages = self.clean_messages(messages)
        try:
            response = self.client.post("salomao_sessions", params={"on_conflict": "session_id"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"}, json={
                    "session_id": self.session_id(thread_id),
                    "last_activity_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {"version": 1, "thread_id": str(thread_id),
                        "observed_messages": messages, "digest": self.digest(messages)}})
            response.raise_for_status()
            logger.info("Memoria da conversa sincronizada", extra={"event": "memory.saved",
                "thread_id": str(thread_id), "context_messages": len(messages)})
            return True
        except Exception as exc:
            self._failed(exc)
            return False
