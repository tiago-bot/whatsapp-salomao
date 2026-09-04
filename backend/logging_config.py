"""Single-line JSON on stdout: Railway must not mark routine logs as errors.

No customer text, credentials, HTTP bodies or raw exception values belong in
operational logs. Correlation metadata travels with asyncio/to_thread contexts.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
import traceback

import regex

_context = ContextVar("log_context", default={})
_fields = {"event", "ticket_id", "thread_id", "message_id", "session_id", "run_id",
           "source", "reason", "error_type", "status_code", "duration_ms", "model",
           "message_count", "context_messages", "context_chars", "context_truncated",
           "pending_count", "skipped_count", "source_count", "source_ids", "route",
           "answer_status", "parts", "part", "handoff", "interval_seconds",
           "ticket_count", "response_count", "error_count", "pipeline_id", "stage_id",
           "owner_id", "polling_enabled", "contextualized", "page_count", "scope_policy_version", "wait_seconds",
           "note_id"}


def clean_log_text(value):
    text = str(value)
    text = re.sub(r"(?:sk-[\w-]+|pcsk_[\w-]+|pat-[\w-]+|eyJ[\w.-]+)", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", text)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED]", text)
    text = regex.sub(r"[\p{Extended_Pictographic}\p{Regional_Indicator}\uFE0F\u200D\u20E3]", "", text)
    return text.strip()


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": {"WARNING": "warn", "CRITICAL": "error"}.get(record.levelname, record.levelname.lower()),
            "logger": record.name,
            "message": clean_log_text(record.getMessage()),
        }
        for key, value in {**_context.get(), **record.__dict__}.items():
            if key in _fields and value is not None:
                data[key] = clean_log_text(value) if isinstance(value, str) else value
        if record.exc_info:
            data["error_type"] = record.exc_info[0].__name__
            # Frame locations are useful; exception text and local values can
            # contain entire requests, tokens or customer conversations.
            data["stack"] = [{"file": os.path.basename(f.filename), "line": f.lineno,
                              "function": f.name} for f in traceback.extract_tb(record.exc_info[2])]
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging():
    root = logging.getLogger()
    if not any(getattr(h, "salomao_json", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.salomao_json = True
        handler.setFormatter(JsonFormatter())
        root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    # Uvicorn installs stderr handlers before importing the application.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "agno"):
        current = logging.getLogger(name)
        current.handlers.clear()
        current.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "agno"):
        logging.getLogger(name).setLevel(logging.WARNING)


@contextmanager
def log_context(**fields):
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)
