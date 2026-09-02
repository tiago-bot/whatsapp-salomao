"""Read-only search of the same published articles displayed by the Central.

Independent of embeddings/LLMs. The bounded cache survives temporary catalog
outages, but never serves a snapshot older than one hour.
"""
import logging
import math
import re
import time
import unicodedata
from collections import Counter
from difflib import get_close_matches, SequenceMatcher
from threading import Lock
from urllib.parse import urlparse

import httpx
from config import KB_SUPABASE_URL, KB_SUPABASE_ANON_KEY

logger = logging.getLogger(__name__)
STOP = set("a o as os de da do das dos em no na nos nas e ou um uma para por com que qual quais como onde quando fazer faco realizar quero preciso gostaria posso pode consigo funciona funcionar area opcao botao tela esse essa isso ele ela aqui agora nao sim meu minha ao se ser esta estar tem foi ainda sobre inchurch church in painel plataforma mais muito favor ajuda favor me pelo pela aparece aparecer sumiu so".split())
STOP.update({"pedir", "solicitar", "solicito"})
ALIASES = {
    "estornar": "estorno", "estornos": "estorno", "reembolso": "estorno", "devolver": "estorno",
    "cancelar": "cancelamento", "cancelo": "cancelamento", "encerrar": "cancelamento", "encerro": "cancelamento",
    "oracoes": "oracao", "inscricoes": "inscricao", "transacoes": "transacao",
    "doacoes": "doacao", "contribuicao": "doacao", "contribuicoes": "doacao",
}


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return [ALIASES.get(w, w[:-1] if w.endswith("s") and len(w) > 4 else w)
            for w in words if len(w) > 2 and w not in STOP]


def safe_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "portal.inchurch.com.br" and not parsed.username


def contextual_query(query: str, history: str) -> str:
    """Only borrow the most recent customer topic for short follow-ups.

    Assistant replies and older topics never become search queries.
    """
    current = terms(query)
    if len(current) > 3 or not history:
        return query
    prior = re.findall(r"^Cliente: (.+)$", history, flags=re.MULTILINE)
    for previous in reversed(prior[-3:]):
        if terms(previous):
            # A complete new question has its own topic; explicit follow-ups or
            # short answers such as 'de evento' inherit the prior intention.
            assistant = history.rsplit("Salomao:", 1)[-1] if "Salomao:" in history else ""
            is_choice = ("?" in assistant and set(current).issubset(set(terms(assistant))) and
                         not re.match(r"^(como|onde|qual|quero|preciso|pedidos)\b", normalize(query)))
            if not current or is_choice or re.match(r"^(e\b|de\b|do\b|da\b|nao\b|sim\b|no\b|na\b)", normalize(query)):
                return f"{previous} {query}"
            break
    return query


class PublishedKnowledge:
    def __init__(self):
        self._articles: list[dict] = []
        self._loaded_at = 0.0
        self._retry_at = 0.0
        self._lock = Lock()

    def _load(self) -> list[dict]:
        if not KB_SUPABASE_URL or not KB_SUPABASE_ANON_KEY:
            return []
        now = time.monotonic()
        if now - self._loaded_at < 300 or now < self._retry_at:
            return self._articles if now - self._loaded_at < 3600 else []
        with self._lock:
            if time.monotonic() - self._loaded_at < 300:
                return self._articles
            try:
                articles = []
                with httpx.Client(timeout=8.0) as client:
                    for offset in range(0, 10000, 500):
                        result = client.get(
                            f"{KB_SUPABASE_URL.rstrip('/')}/rest/v1/kb_articles",
                            headers={"apikey": KB_SUPABASE_ANON_KEY, "Authorization": f"Bearer {KB_SUPABASE_ANON_KEY}"},
                            params={"select": "id,title,body_plain,absolute_url,category_name,hs_updated_at",
                                    "state": "eq.PUBLISHED", "language": "eq.pt-br", "order": "id.asc",
                                    "offset": offset, "limit": 500},
                        )
                        result.raise_for_status()
                        page = result.json()
                        for row in page:
                            if row.get("body_plain") and safe_url(row.get("absolute_url") or ""):
                                articles.append({"id": row["id"], "title": row["title"],
                                                 "content": row["body_plain"], "url": row["absolute_url"],
                                                 "category": row.get("category_name") or "",
                                                 "updated_at": row.get("hs_updated_at") or ""})
                        if len(page) < 500:
                            break
                self._articles, self._loaded_at = articles, time.monotonic()
                logger.info("Catalogo publicado carregado | articles=%s", len(articles))
            except Exception as exc:
                # Never log URLs, headers or exception bodies containing credentials.
                logger.warning("Catalogo publicado indisponivel | type=%s", type(exc).__name__)
                self._retry_at = time.monotonic() + 30
            return self._articles if time.monotonic() - self._loaded_at < 3600 else []

    def search(self, query: str, history: str = "", top_k: int = 4) -> list[dict]:
        articles = self._load()
        requested = set(terms(contextual_query(query, history)))
        if not requested or not articles:
            return []
        titles = [Counter(terms(a["title"])) for a in articles]
        bodies = [Counter(terms(a["content"])) for a in articles]
        vocabulary = set().union(*titles)
        # Correct a single close spelling only if it is absent from the corpus.
        for word in list(requested):
            if word not in vocabulary and len(word) >= 5 and not any(word in body for body in bodies):
                close = get_close_matches(word, vocabulary, n=2, cutoff=.84)
                unambiguous = len(close) == 1 or (len(close) == 2 and
                    SequenceMatcher(None, word, close[0]).ratio() > SequenceMatcher(None, word, close[1]).ratio())
                if close and unambiguous:
                    requested.remove(word)
                    requested.add(close[0])
        frequencies = {w: sum(w in b for b in bodies) for w in requested}
        ranked = []
        for article, title, body in zip(articles, titles, bodies):
            title_hits = requested & title.keys()
            body_hits = requested & body.keys()
            coverage = len(body_hits | title_hits) / len(requested)
            # Avoid weak incidental mentions and answers matching only one word
            # of an unrelated multi-topic question.
            if not title_hits or coverage < .6:
                continue
            score = sum(math.log(1 + (len(articles) + 1) / (frequencies[w] + 1)) *
                        (3 * min(title[w], 1) + min(body[w], 3)) for w in requested)
            ranked.append({**article, "score": score, "retrieval": "published"})
        ranked.sort(key=lambda a: (a["score"], a["updated_at"]), reverse=True)
        return ranked[:top_k]

    def by_url(self) -> dict[str, dict]:
        return {a["url"]: a for a in self._load()}


def excerpt(article: dict, query: str, limit: int = 850) -> str:
    """Verbatim sentence window, with ellipses marking omitted material."""
    text = re.sub(r"\s+", " ", article["content"]).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    words = set(terms(query))
    wants_steps = bool(re.search(r"\b(como|quero|fazer)\b", normalize(query)))
    scores = [len(words & set(terms(s))) + (2 if wants_steps and re.search(r"\b(acesse|abra|selecione|clique|localize)\b", normalize(s)) else 0)
              for s in sentences]
    start = max(range(len(sentences)), key=lambda i: scores[i]) if sentences else 0
    parts = []
    for sentence in sentences[start:]:
        if parts and len(" ".join(parts)) + len(sentence) > limit:
            break
        parts.append(sentence[:limit])
    result = " ".join(parts)
    return ("… " if start else "") + result + (" …" if start + len(parts) < len(sentences) or len(result) < len(sentences[start]) else "")


published_knowledge = PublishedKnowledge()
