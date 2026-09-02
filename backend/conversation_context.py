"""Bounded conversation history with stable roles and no future-message leakage."""
from datetime import datetime, timezone

MAX_CONTEXT_MESSAGES = 30
MAX_CONTEXT_CHARS = 24000
MAX_MESSAGE_CHARS = 4500


def message_time(value):
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def bounded_history(messages):
    """Preserve message endings (often the pending question), not just openings."""
    selected, size, truncated = [], 0, len(messages) > MAX_CONTEXT_MESSAGES
    for message in reversed(messages[-MAX_CONTEXT_MESSAGES:]):
        if message.get("role") not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        remaining = min(MAX_MESSAGE_CHARS, MAX_CONTEXT_CHARS - size)
        if remaining < 100:
            truncated = True
            break
        if len(content) > remaining:
            truncated = True
            marker = " [...] "
            head = (remaining - len(marker)) // 2
            content = content[:head] + marker + content[-(remaining - len(marker) - head):]
        selected.append({"role": message["role"], "content": content})
        size += len(content)
    return list(reversed(selected)), truncated


def format_history(messages):
    history, _ = bounded_history(messages)
    # One physical line per message: customer text cannot forge role headers.
    return "\n".join(("Cliente: " if m["role"] == "user" else "Salomao: ") +
                     " ".join(m["content"].split()) for m in history)


def history_before(messages, current):
    """Only messages preceding the current input, never another queued turn."""
    cutoff = message_time(current.get("created_at"))
    if cutoff is None:
        return []
    ordered = sorted(messages, key=lambda m: (message_time(m.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc), str(m.get("id", ""))))
    history = []
    for message in ordered:
        timestamp = message_time(message.get("created_at"))
        if str(message.get("id")) == str(current.get("id")):
            break
        if timestamp is None or timestamp >= cutoff:
            continue
        if message.get("text"):
            history.append({"role": "user" if message.get("is_from_visitor") else "assistant",
                            "content": message["text"]})
    # The agent applies the budget once, retaining an accurate truncation flag.
    return history
