"""Recognize requests for a person, not incidental words such as 'pessoas'."""
import re
import unicodedata


def requests_human(message: str) -> bool:
    text = "".join(c for c in unicodedata.normalize("NFKD", message.lower()) if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if re.search(r"\bnao\b.{0,35}\b(falar|quero|preciso|transfira|transferir|chame)\b", text):
        return False
    if text in {"humano", "atendente", "atendimento humano", "suporte humano", "falar com alguem"}:
        return True
    return bool(re.search(
        r"\b(?:falar|conversar)\s+com\s+(?:(?:um|uma|o|a|seu|sua)\s+)?(?:atendente|humano|pessoa|alguem|suporte|equipe)\b"
        r"|\b(?:quero|preciso de|chame|chamar|chama|transfira|transferir|encaminhe)\s+(?:(?:um|uma|o|a|para|pro|pra)\s+)*(?:atendente|humano|pessoa|suporte|atendimento humano)\b", text))
