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
from scope_policy import explicit_external_request

import httpx
from config import KB_SUPABASE_URL, KB_SUPABASE_ANON_KEY

logger = logging.getLogger(__name__)
STOP = set("a o as os de da do das dos em no na nos nas e ou um uma para por com que qual quais como onde quando fazer faco realizar quero preciso gostaria posso pode consigo funciona funcionar area opcao botao tela esse essa isso ele ela aqui agora nao sim meu minha ao se ser esta estar tem foi ainda sobre inchurch church in painel plataforma mais muito favor ajuda favor me pelo pela aparece aparecer sumiu so".split())
STOP.update({"pedir", "solicitar", "solicito"})
STOP.update({"mim", "pra", "voce", "voces", "obrigado", "obrigada", "ola", "bom", "dia",
             "tarde", "noite", "certo", "entendi", "tentei", "consegui", "funcionou", "continua"})

# These are subject signals, not routing rules. A newly named subject can reset
# context; generic UI symptoms must not reset it merely because wording changed.
SUBJECTS = {"estorno", "cancelamento", "contrato", "oracao", "evento", "ingresso", "inscricao",
            "celula", "membro", "pessoa", "boleto", "pix", "cartao", "senha", "login",
            "financeiro", "doacao", "relatorio", "notificacao", "transmissao", "cupom"}
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
    """Keep the latest CUSTOMER subject across symptoms/short answers.

    Historical assistant guesses are not a topic authority. Explicit subject
    changes win; wording order/length alone must not turn a refund into cells.
    """
    if not history or explicit_external_request(query):
        return query

    def follows(text, anchor, assistant):
        normalized = normalize(text).strip()
        current = set(terms(text))
        if explicit_external_request(text):
            return False
        if re.match(r"^(mudando de assunto|outra (duvida|pergunta)|agora (quero|preciso))\b", normalized):
            return False
        choice = ("?" in assistant and bool(current) and current.issubset(set(terms(assistant))) and
                  not re.match(r"^(como|onde|qual|quero|preciso|pedidos)\b", normalized))
        modifier = bool(re.match(r"^(de|do|da|no|na)\b", normalized))
        if choice or modifier:
            return True
        # 'Não consigo criar evento' after refunds is a new explicit request.
        if current & SUBJECTS and not (current & SUBJECTS & set(terms(anchor))):
            return False
        # Attribute questions inherit the active object: required fields, price,
        # deadline, permissions, etc. They are not independent search topics.
        if re.search(r"\b(obrigatorio[sa]?|obrigatorios|campos?|preencher|preenchimento|prazo|demora|permissoes|permissao|limite|opcional|opcionais)\b", normalized):
            return bool(set(terms(anchor)) & SUBJECTS)
        # A short follow-up can quote a detail from the last answer without
        # naming the original object ('e telefone?', 'e a data de nascimento?').
        if current and current.issubset(set(terms(assistant))) and len(current) <= 5:
            return bool(set(terms(anchor)) & SUBJECTS)
        return (not current or bool(re.match(r"^(e\b|nao\b|sim\b)", normalized)) or
                bool(re.search(r"\b(botao|opcao|isso|essa|esse|ele|ela|aqui|sumiu|aparece|"
                               r"consegui|funcionou|continua|deu certo|tentei|fiz)\b", normalized)))

    anchor, assistant = "", ""
    for role, content in re.findall(r"^(Cliente|Salomao): (.+)$", history, flags=re.MULTILINE):
        if role == "Salomao":
            assistant = content
            continue
        if not terms(content):
            continue
        if anchor and follows(content, anchor, assistant):
            # Retain short object selections (refund -> of an event), but not
            # an ever-growing chain of symptoms or an assistant's wrong topic.
            if set(terms(content)) & SUBJECTS:
                anchor = f"{anchor} {content}"[-1600:]
        else:
            anchor = content
    return f"{anchor} {query}" if anchor and follows(query, anchor, assistant) else query


def previous_source_urls(query: str, history: str) -> list[str]:
    """References are hints to re-fetch, never evidence by themselves."""
    if contextual_query(query, history) == query:
        return []
    # Only the last answer, not links from a previous unrelated subject.
    replies = re.findall(r"^Salomao: (.+)$", history, flags=re.MULTILINE)
    if not replies:
        return []
    return list(dict.fromkeys(url.rstrip(').,]') for url in re.findall(r"https://[^\s<>]+", replies[-1])
                              if safe_url(url.rstrip(').,]'))))[:3]


def context_relevant_articles(articles, query, history):
    """Do not ground a follow-up in documents unrelated to its active subject."""
    resolved = contextual_query(query, history)
    if resolved == query:
        return articles
    anchors = set(terms(resolved[:-len(query)].strip())) & SUBJECTS if query else set()
    if not anchors:
        return articles
    return [a for a in articles if anchors & set(terms(" ".join(str(a.get(k) or "") for k in ("title", "category", "content"))))]


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
