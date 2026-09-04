"""Build a concise, factual HubSpot note for a human handoff."""
from html import escape
import hashlib
import re
import unicodedata

from handoff import requests_human


MAX_ITEM_CHARS = 1400
MAX_CONTEXT_ITEMS = 5
MAX_GUIDANCE_ITEMS = 4


def handoff_reference(thread_id, message_id):
    value = f"{thread_id}:{message_id}".encode("utf-8")
    return "SALOMAO-" + hashlib.sha256(value).hexdigest()[:16].upper()


def _compact(value, limit=MAX_ITEM_CHARS):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit - 4].rstrip() + " ..."


def _list(items, empty):
    values = [f"<li>{escape(item)}</li>" for item in items if item]
    return "<ul>" + "".join(values) + "</ul>" if values else f"<p>{escape(empty)}</p>"


def _source_links(messages, sources):
    found = []
    for source in sources or []:
        url = str(source.get("url") or "").strip()
        title = _compact(source.get("title") or url, 180)
        if url and url not in {item[1] for item in found}:
            found.append((title, url))
    for message in messages:
        if message.get("is_from_visitor"):
            continue
        for url in re.findall(r"https?://[^\s<>()\[\]\"']+", str(message.get("text") or "")):
            url = url.rstrip(".,;:!?")
            if url not in {item[1] for item in found}:
                found.append((url, url))
    return found[:8]


def _reported_result(customer_messages):
    result_terms = re.compile(
        r"\b(funcionou|resolveu|deu certo|consegui|nao funcionou|nao resolveu|nao consegui|continua|mesmo erro|erro persiste)\b",
        re.IGNORECASE,
    )
    for text in reversed(customer_messages):
        comparable = "".join(character for character in unicodedata.normalize("NFKD", text)
                             if not unicodedata.combining(character))
        if result_terms.search(comparable):
            return text
    return "O cliente não confirmou a resolução antes da transferência."


def build_handoff_note(*, thread_id, message_id, messages, reason, sources=None):
    """Return escaped HTML accepted by HubSpot's note body field.

    This is extractive on purpose: it summarizes observed messages without
    inventing product facts or depending on another model call during routing.
    """
    ordered = [message for message in messages if _compact(message.get("text"))]
    customer = [_compact(m.get("text")) for m in ordered if m.get("is_from_visitor")]
    guidance = [_compact(m.get("text")) for m in ordered
                if not m.get("is_from_visitor") and not requests_human(m.get("text", ""))
                and "vou encaminhar seu atendimento" not in _compact(m.get("text")).lower()]
    problem = customer[-MAX_CONTEXT_ITEMS:]
    guidance = guidance[-MAX_GUIDANCE_ITEMS:]
    links = _source_links(ordered, sources)
    source_html = "<ul>" + "".join(
        f'<li><a href="{escape(url, quote=True)}">{escape(title)}</a></li>' for title, url in links
    ) + "</ul>" if links else "<p>Nenhuma fonte foi registrada nesta conversa.</p>"
    reference = handoff_reference(thread_id, message_id)
    return (
        "<h3>Resumo de transferência — Salomão</h3>"
        "<p><strong>Problema e contexto relatados pelo cliente</strong></p>"
        + _list(problem, "Não foi possível recuperar o relato anterior.")
        + "<p><strong>Orientações já fornecidas</strong></p>"
        + _list(guidance, "Nenhuma orientação anterior foi enviada pelo Salomão.")
        + "<p><strong>Resultado até o momento</strong></p>"
        + f"<p>{escape(_reported_result(customer))}</p>"
        + "<p><strong>Motivo da transferência</strong></p>"
        + f"<p>{escape(_compact(reason or 'Atendimento humano solicitado.'))}</p>"
        + "<p><strong>Fontes consultadas</strong></p>"
        + source_html
        + f"<p><small>Referência interna: {reference}</small></p>"
    )
