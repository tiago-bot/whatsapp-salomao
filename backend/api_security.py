"""Authentication and HubSpot request validation, without trusting proxy headers."""
import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
import config

PUBLIC_ROUTES = {("GET", "/"), ("GET", "/health"), ("GET", "/ready"),
                 ("POST", "/webhook/hubspot")}
MAX_WEBHOOK_BYTES = 1024 * 1024


async def require_admin(request: Request):
    if (request.method, request.url.path) in PUBLIC_ROUTES:
        return
    if len(config.ADMIN_API_TOKEN) < 32:
        raise HTTPException(503, "Autenticação administrativa não configurada")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
            token.encode(), config.ADMIN_API_TOKEN.encode()):
        raise HTTPException(401, "Credencial administrativa inválida", headers={"WWW-Authenticate": "Bearer"})


def security_configured():
    url = urlsplit(config.HUBSPOT_WEBHOOK_URL)
    return (len(config.ADMIN_API_TOKEN) >= 32 and bool(config.HUBSPOT_CLIENT_SECRET)
            and url.scheme == "https" and bool(url.netloc) and not url.username
            and url.path == "/webhook/hubspot" and not url.fragment)


def signature_uri(uri):
    # HubSpot specifies exactly these twelve escapes, not a general unquote().
    return re.sub(r"%(?:3A|2F|3F|40|21|24|27|28|29|2A|2C|3B)",
                  lambda match: chr(int(match[0][1:], 16)), uri, flags=re.I)


async def verified_events(request: Request):
    if not config.HUBSPOT_CLIENT_SECRET:
        raise HTTPException(503, "Webhook não configurado")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "Webhook excede o limite")
    raw = bytes(raw)
    now_ms = int(time.time() * 1000)
    legacy = False
    if "x-hubspot-signature-v3" in request.headers:
        stamp = request.headers.get("x-hubspot-request-timestamp", "")
        if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 16 or abs(now_ms - int(stamp)) > 300_000:
            raise HTTPException(401, "Timestamp do webhook inválido ou expirado")
        url = urlsplit(config.HUBSPOT_WEBHOOK_URL)
        if url.scheme != "https" or not url.netloc or url.username or url.fragment:
            raise HTTPException(503, "URL pública do webhook não configurada")
        # The configured public URL is authoritative behind Railway/proxies.
        if request.url.path != url.path or request.scope.get("query_string", b"") != url.query.encode():
            raise HTTPException(401, "URL do webhook inválida")
        source = (request.method + signature_uri(config.HUBSPOT_WEBHOOK_URL)).encode() + raw + stamp.encode()
        expected = base64.b64encode(hmac.digest(config.HUBSPOT_CLIENT_SECRET.encode(), source, "sha256"))
        supplied = request.headers["x-hubspot-signature-v3"].encode()
    elif config.HUBSPOT_WEBHOOK_ALLOW_V1 and request.headers.get("x-hubspot-signature-version") == "v1":
        legacy = True
        expected = hashlib.sha256(config.HUBSPOT_CLIENT_SECRET.encode() + raw).hexdigest().encode()
        supplied = request.headers.get("x-hubspot-signature", "").encode()
    else:
        raise HTTPException(401, "Assinatura do webhook ausente ou não suportada")
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, "Assinatura do webhook inválida")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "JSON inválido") from None
    events = body if isinstance(body, list) else [body]
    if not 1 <= len(events) <= 100:
        raise HTTPException(422, "Esperados entre 1 e 100 eventos")
    for event in events:
        if (not isinstance(event, dict) or not isinstance(event.get("subscriptionType"), str)
                or not event["subscriptionType"] or isinstance(event.get("objectId"), bool)
                or not isinstance(event.get("objectId"), (str, int)) or not str(event["objectId"]).strip()):
            raise HTTPException(422, "Evento inválido")
        if legacy:
            # v1 has no signed request timestamp. occurredAt IS covered by its
            # body signature; allow HubSpot's 24-hour retry window, dedupe on disk.
            stamp = event.get("occurredAt")
            if type(stamp) is not int or not -300_000 <= now_ms - stamp <= 86_400_000:
                raise HTTPException(401, "Data assinada do evento inválida ou expirada")
    return events
