"""Recognize explicit requests for human support without matching incidental words."""
import re
import unicodedata


_HUMAN_TARGET = (
    r"(?:atendente|humano|pessoa|alguem|suporte(?:\s+n1)?|equipe(?:\s+de\s+suporte)?|"
    r"atendimento(?:\s+humano|\s+do\s+suporte)?)"
)


def _normalize(message: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", str(message).lower())
                   if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def requests_human(message: str) -> bool:
    text = _normalize(message)
    if not text:
        return False

    # Remove only an explicitly negated request. A sentence such as
    # "nao quero falar com robo, quero suporte" must still be accepted.
    negated = (
        rf"\b(?:nao|nem)\s+(?:(?:quero|preciso|gostaria|desejo|prefiro)\s+(?:mais\s+)?(?:de\s+)?"
        rf"(?:(?:falar|conversar)\s+com\s+)?(?:um\s+|uma\s+|o\s+|a\s+)?{_HUMAN_TARGET}"
        rf"|(?:(?:me\s+)?(?:transfira|transfere|encaminhe|encaminha|passe|passa|mande|manda|chame|chama))"
        rf"(?:\s+para|\s+pro|\s+pra)?\s+(?:um\s+|uma\s+|o\s+|a\s+)?{_HUMAN_TARGET}"
        rf"|(?:precisa|pode)\s+(?:me\s+)?(?:transferir|encaminhar|passar|mandar|chamar)\s+"
        rf"(?:(?:para|pro|pra)\s+)?(?:(?:um|uma|o|a)\s+)?{_HUMAN_TARGET})\b"
    )
    text = re.sub(negated, " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if text in {"humano", "atendente", "atendimento", "atendimento humano", "suporte",
                "suporte humano", "suporte n1", "falar com alguem"}:
        return True

    return bool(re.search(
        rf"\b(?:falar|conversar)\s+com\s+(?:(?:um|uma|o|a|seu|sua)\s+)?{_HUMAN_TARGET}\b"
        rf"|\b(?:quero|preciso|gostaria|desejo|prefiro)\s+(?:de\s+)?(?:(?:falar|conversar)\s+com\s+)?"
        rf"(?:(?:um|uma|o|a)\s+)?{_HUMAN_TARGET}\b"
        rf"|\b(?:tem\s+como|posso|poderia|consigo)\s+(?:falar|conversar|ser\s+atendid[oa])\s+"
        rf"(?:com|por)\s+(?:(?:um|uma|o|a)\s+)?{_HUMAN_TARGET}\b"
        rf"|\b(?:pode|consegue|poderia)\s+(?:me\s+)?(?:transferir|encaminhar|passar|mandar|chamar)\s+"
        rf"(?:(?:para|pro|pra)\s+)?(?:(?:um|uma|o|a)\s+)?{_HUMAN_TARGET}\b"
        rf"|\b(?:(?:me\s+)?(?:transfira|transfere|encaminhe|encaminha|passe|passa|mande|manda|chame|chama)|"
        rf"(?:transferir|encaminhar|passar|mandar|chamar))\s+(?:(?:para|pro|pra)\s+)?(?:(?:um|uma|o|a)\s+)?{_HUMAN_TARGET}\b"
        r"|\b(?:nao\s+quero|cansei\s+de)\s+(?:mais\s+)?(?:falar\s+com\s+)?(?:robo|bot|ia)\b",
        text,
    ))
