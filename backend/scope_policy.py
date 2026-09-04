"""Deterministic tripwires and a versioned, content-bound delivery approval.

Rules supplement semantic checks; they are not a complete language classifier.
Unapproved output must never be sent, including old durable outbox entries.
"""
import hashlib
import json
import re
import unicodedata

SCOPE_POLICY_VERSION = "2026-09-04-grounded-v2"
SCOPE_REDIRECT = "Meu foco aqui é ajudar com a plataforma inChurch. Podemos retomar sua dúvida sobre ela."
SCOPE_CLARIFY = "Quero entender melhor para te orientar: a qual função ou tela da inChurch você está se referindo?"
SCOPE_UNAVAILABLE = "Não consegui validar uma orientação segura agora. Tente novamente em instantes ou peça para falar com um atendente."


def normalized(text):
    text = "".join(c for c in unicodedata.normalize("NFKD", str(text).lower())
                   if not unicodedata.combining(c) and unicodedata.category(c) != "Cf")
    return re.sub(r"\s+", " ", text).strip()


def explicit_external_request(text):
    text = normalized(text)
    patterns = (
        r"\bquant[oa]s?\b[^?\n]{0,100}\b(libertadores|mundiais|titulos|gols)\b",
        r"\b(quem (ganhou|venceu)|qual (o )?placar|resultado do jogo|quem vai ganhar)\b",
        r"\bhow many\b.{0,100}\b(titles|libertadores|goals|championships)\b",
        r"\b(qual|diga|fale)\b.{0,25}\bcapital (d[aeo]|of)\b",
        r"\b(receita de (bolo|lasanha|brigadeiro)|como (fazer|preparar) (um |uma )?(bolo|lasanha|brigadeiro))\b",
        r"\b(escreva|crie|faca|compose|write)\b.{0,20}\b(sermao|oracao|piada|poema|sermon|joke)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def obvious_external_answer(text):
    text = normalized(text)
    return bool(re.search(r"\b(flamengo|palmeiras|corinthians|vasco|santos|gremio|botafogo)\b.{0,100}\b(conquistou|venceu|ganhou|possui|tem)\b.{0,100}\b(libertadores|titulos|mundiais|ocasioes)\b", text))


def approval_digest(response, parts):
    data = json.dumps([SCOPE_POLICY_VERSION, response, parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def approved_delivery(payload):
    return (payload.get("scope_policy_version") == SCOPE_POLICY_VERSION and
            payload.get("scope_digest") == approval_digest(payload.get("response", ""), payload.get("parts", [])) and
            not obvious_external_answer(payload.get("response", "")))
