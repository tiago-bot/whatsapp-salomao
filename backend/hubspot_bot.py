"""Salomão v1 adapted to HubSpot WhatsApp, with a durable delivery outbox."""
import base64
from datetime import datetime, timedelta, timezone
import logging
import mimetypes
from threading import Lock
from urllib.parse import urlsplit
import uuid

import requests

from config import DELIVERY_DB_PATH, WHATSAPP_MAX_MESSAGE_LENGTH
from delivery_store import DeliveryStore
from salomao_agent import salomao
from whatsapp_formatting import format_whatsapp, split_whatsapp
from hubspot_service import (
    get_tickets_for_salomao, get_ticket_by_id, get_conversation_thread_by_ticket,
    get_thread_messages, parse_incoming_messages, reply_to_visitor,
    transfer_ticket_to_human_support, SALOMAO_PIPELINE, SALOMAO_STATUS,
    SALOMAO_ACTOR_ID, get_headers,
)

logger = logging.getLogger("salomao.hubspot_bot")


class HubSpotSalomaoBot:
    MESSAGE_MAX_AGE_MINUTES = 5
    MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

    def __init__(self, store=None, agent=None):
        self.store = store if store is not None else DeliveryStore(DELIVERY_DB_PATH)
        self.agent = agent if agent is not None else salomao
        # Bounded striped locks: webhook + polling cannot process the same thread
        # concurrently, without retaining an unbounded map of customer IDs.
        self._locks = [Lock() for _ in range(64)]

    @staticmethod
    def get_session_id_for_thread(thread_id):
        return f"hubspot_thread_{thread_id}"

    @staticmethod
    def _eligible(ticket):
        props = (ticket or {}).get("properties", {})
        return (str(props.get("hs_pipeline", "")) == SALOMAO_PIPELINE
                and str(props.get("hs_pipeline_stage", "")) == SALOMAO_STATUS
                and str(props.get("hubspot_owner_id", "")) == SALOMAO_ACTOR_ID.removeprefix("A-"))

    def get_unprocessed_visitor_messages(self, thread_id):
        messages = parse_incoming_messages(get_thread_messages(thread_id))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.MESSAGE_MAX_AGE_MINUTES)
        pending = []
        for message in messages:
            if not message.get("is_from_visitor") or not message.get("id"):
                continue
            if self.store.get(thread_id, message["id"]):
                continue
            try:
                created = datetime.fromisoformat(message.get("created_at", "").replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < cutoff:
                    continue
            except (ValueError, TypeError):
                logger.warning("Ignoring message without a valid timestamp")
                continue
            pending.append(message)
        return sorted(pending, key=lambda msg: msg.get("created_at", ""))

    def _download_attachment_as_base64(self, url):
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        # Never send the HubSpot bearer token to arbitrary attachment URLs.
        allowed = ("hubapi.com", "hubspot.com", "hubspotusercontent.com",
                   "hubspotusercontent-na1.net", "hubspotusercontent-eu1.net",
                   "hsusercontent.com")
        if parsed.scheme != "https" or parsed.username or not any(host == d or host.endswith("." + d) for d in allowed):
            raise ValueError("unsupported_attachment_host")
        headers = get_headers() if host == "api.hubapi.com" else {}
        with requests.get(url, headers=headers, timeout=20, stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ValueError("attachment_redirect_not_allowed")
            if int(response.headers.get("Content-Length", 0)) > self.MAX_ATTACHMENT_BYTES:
                raise ValueError("attachment_too_large")
            data = bytearray()
            for part in response.iter_content(65536):
                data.extend(part)
                if len(data) > self.MAX_ATTACHMENT_BYTES:
                    raise ValueError("attachment_too_large")
        return base64.b64encode(data).decode("ascii")

    def process_message(self, thread_id, message):
        attachments = message.get("raw", {}).get("attachments", [])
        kwargs = {}
        try:
            for attachment in attachments:
                url = attachment.get("url", "")
                kind = str(attachment.get("type", "")).lower()
                mime = attachment.get("mimeType") or attachment.get("contentType") or mimetypes.guess_type(urlsplit(url).path)[0] or ""
                extension = urlsplit(url).path.rsplit(".", 1)[-1].lower()
                if mime.startswith("image/") or kind == "image":
                    if "image_base64" in kwargs:
                        raise ValueError("multiple_images")
                    kwargs["image_base64"] = self._download_attachment_as_base64(url)
                    kwargs["image_mime_type"] = mime if mime.startswith("image/") else "image/jpeg"
                elif mime.startswith("audio/") or kind == "audio" or extension in {"ptt", "oga", "opus"}:
                    if "audio_base64" in kwargs:
                        raise ValueError("multiple_audio")
                    kwargs["audio_base64"] = self._download_attachment_as_base64(url)
                    kwargs["audio_format"] = {"oga": "ogg", "ptt": "ogg", "opus": "ogg"}.get(extension, extension if extension in {"ogg", "mp3", "wav", "m4a", "webm", "mp4"} else "ogg")
                else:
                    raise ValueError("unsupported_attachment")
            result = self.agent.process_message(
                message=message.get("text", ""),
                session_id=self.get_session_id_for_thread(thread_id),
                originating_channel="whatsapp", **kwargs,
            )
        except ValueError:
            return {"response": "Não consegui ler esse anexo. Envie uma imagem ou áudio por vez, ou descreva a dúvida em texto.",
                    "success": False, "transfer_requested": False, "answer_status": "unavailable"}
        except Exception as exc:
            logger.error("Message generation failed | type=%s", type(exc).__name__)
            result = {"success": False}
        if not result.get("response"):
            result["response"] = "Não consegui consultar a orientação agora. Tente novamente em instantes ou peça para falar com um atendente."
        # A documented knowledge gap is actionable in the WhatsApp helpdesk.
        if result.get("answer_status") == "no_match":
            result["transfer_requested"] = True
        if result.get("transfer_requested"):
            result["response"] = "Vou encaminhar seu atendimento para a equipe da inChurch."
        result["response"] = format_whatsapp(result["response"])
        return result

    def _deliver(self, entry, ticket_id):
        thread_id, message_id, payload = entry["thread_id"], entry["message_id"], entry["payload"]
        for index in range(entry["sent_parts"], len(payload["parts"])):
            if not reply_to_visitor(thread_id, payload["parts"][index]):
                return {"message_id": message_id, "sent": False, "error": "Falha ao enviar via HubSpot"}
            self.store.confirm_part(thread_id, message_id, index + 1)
        if payload.get("transfer_requested"):
            if not ticket_id or not transfer_ticket_to_human_support(ticket_id):
                # Parts remain confirmed; next poll retries only the handoff.
                return {"message_id": message_id, "sent": True, "transferred": False,
                        "error": "Transferência pendente; será tentada novamente"}
        self.store.complete(thread_id, message_id)
        return {"message_id": message_id, "user_message": payload.get("user_message", ""),
                "bot_response": payload["response"], "sent": True,
                "transferred": bool(payload.get("transfer_requested")),
                "answer_status": payload.get("answer_status", "answered")}

    def process_thread(self, thread_id, ticket_id=None):
        lock = self._locks[hash(thread_id) % len(self._locks)]
        if not lock.acquire(blocking=False):
            return []
        try:
            # Recheck eligibility even for the direct thread-processing endpoint.
            if not ticket_id:
                return []
            if not self._eligible(get_ticket_by_id(ticket_id)):
                return []
            responses = []
            for entry in self.store.pending(thread_id):
                delivered = self._deliver(entry, ticket_id)
                responses.append(delivered)
                if delivered.get("error") or delivered.get("transferred"):
                    return responses
            for message in self.get_unprocessed_visitor_messages(thread_id):
                # Ownership may change while a previous response was generated.
                if not self._eligible(get_ticket_by_id(ticket_id)):
                    break
                result = self.process_message(thread_id, message)
                parts = split_whatsapp(result["response"], WHATSAPP_MAX_MESSAGE_LENGTH)
                entry = self.store.enqueue(thread_id, message["id"], {
                    "response": result["response"], "parts": parts,
                    "user_message": message.get("text", ""),
                    "transfer_requested": bool(result.get("transfer_requested")),
                    "answer_status": result.get("answer_status", "answered"),
                })
                if not self._eligible(get_ticket_by_id(ticket_id)):
                    break
                delivered = self._deliver(entry, ticket_id)
                responses.append(delivered)
                if delivered.get("error") or delivered.get("transferred"):
                    break
            return responses
        finally:
            lock.release()

    def process_ticket(self, ticket_id):
        if not self._eligible(get_ticket_by_id(ticket_id)):
            return {"success": False, "ticket_id": ticket_id, "responses": [], "error": "Ticket fora dos filtros do Salomão"}
        thread = get_conversation_thread_by_ticket(ticket_id)
        if not thread or not thread.get("id"):
            return {"success": False, "ticket_id": ticket_id, "error": "Thread não encontrado"}
        responses = self.process_thread(str(thread["id"]), ticket_id=ticket_id)
        return {"success": not any(r.get("error") for r in responses), "ticket_id": ticket_id,
                "thread_id": str(thread["id"]), "responses": responses}

    def send_message_to_ticket(self, ticket_id, question):
        # Explicit manual send still follows the exact same pipeline and outbox.
        if not self._eligible(get_ticket_by_id(ticket_id)):
            return {"success": False, "error": "Ticket fora dos filtros do Salomão"}
        thread = get_conversation_thread_by_ticket(ticket_id)
        if not thread or not thread.get("id"):
            return {"success": False, "error": "Thread não encontrado"}
        thread_id = str(thread["id"])
        lock = self._locks[hash(thread_id) % len(self._locks)]
        with lock:
            if self.store.pending(thread_id):
                return {"success": False, "error": "Há uma entrega pendente nesta conversa"}
            result = self.process_message(thread_id, {"text": question})
            entry = self.store.enqueue(thread_id, "manual_" + uuid.uuid4().hex, {
                **result, "user_message": question,
                "parts": split_whatsapp(result["response"], WHATSAPP_MAX_MESSAGE_LENGTH),
            })
            if not self._eligible(get_ticket_by_id(ticket_id)):
                return {"success": False, "error": "Responsável pelo ticket mudou"}
            delivered = self._deliver(entry, ticket_id)
            return {"success": not bool(delivered.get("error")), "ticket_id": ticket_id,
                    "thread_id": thread_id, "response": result["response"], **delivered}

    def process_all_pending_tickets(self):
        return [self.process_ticket(ticket["id"]) for ticket in get_tickets_for_salomao()]

    def get_thread_history(self, thread_id):
        session_id = self.get_session_id_for_thread(thread_id)
        return {"thread_id": thread_id, "session_id": session_id,
                "hubspot_messages": parse_incoming_messages(get_thread_messages(thread_id)),
                "salomao_history": self.agent.get_conversation_history(session_id)}

    def clear_thread_session(self, thread_id):
        # Keep delivery receipts: clearing history must not resend old messages.
        self.agent.clear_conversation(self.get_session_id_for_thread(thread_id))


hubspot_bot = HubSpotSalomaoBot()


def process_single_ticket(ticket_id):
    return hubspot_bot.process_ticket(ticket_id)


def process_all_tickets():
    return hubspot_bot.process_all_pending_tickets()


def get_thread_info(thread_id):
    return hubspot_bot.get_thread_history(thread_id)
