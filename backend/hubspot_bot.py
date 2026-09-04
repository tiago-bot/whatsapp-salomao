"""Salomão v1 adapted to HubSpot WhatsApp, with a durable delivery outbox."""
import base64
from datetime import datetime, timedelta, timezone
import logging
import mimetypes
from threading import Lock
from urllib.parse import urlsplit
import uuid
import time

import requests

from config import (DELIVERY_DB_PATH, WHATSAPP_MAX_MESSAGE_LENGTH, SUPABASE_CONVERSATION_MEMORY_ENABLED,
                    HUBSPOT_MESSAGE_DEBOUNCE_SECONDS)
from conversation_memory import SupabaseConversationMemory
from delivery_store import DeliveryStore
from conversation_context import bounded_history, history_before, message_time
from logging_config import log_context
from handoff import requests_human
from handoff_note import build_handoff_note
from salomao_agent import salomao
from whatsapp_formatting import format_whatsapp, split_whatsapp
from scope_policy import (SCOPE_POLICY_VERSION, SCOPE_UNAVAILABLE, SCOPE_REDIRECT,
                          explicit_external_request, obvious_external_answer, approval_digest, approved_delivery)
from hubspot_service import (
    get_tickets_for_salomao, get_ticket_by_id, get_conversation_thread_by_ticket,
    get_thread_messages, parse_incoming_messages, reply_to_visitor,
    transfer_ticket_to_human_support, SALOMAO_PIPELINE, SALOMAO_STATUS,
    SALOMAO_ACTOR_ID, get_headers,
    create_ticket_handoff_note, HubSpotReadError, HubSpotSendRejected, HubSpotNoteRejected,
)

logger = logging.getLogger("salomao.hubspot_bot")


class HubSpotSalomaoBot:
    MESSAGE_MAX_AGE_MINUTES = 5
    MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
    MAX_DEBOUNCE_WAIT_SECONDS = 20
    MAX_GENERATIONS_PER_CYCLE = 3
    AUDIO_EXTENSIONS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "oga", "opus", "ptt"}
    IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}

    def __init__(self, store=None, agent=None, memory=None, debounce_seconds=None):
        self.debounce_seconds = HUBSPOT_MESSAGE_DEBOUNCE_SECONDS if debounce_seconds is None else max(0, debounce_seconds)
        self.memory = memory if memory is not None else (
            SupabaseConversationMemory() if store is None and SUPABASE_CONVERSATION_MEMORY_ENABLED else None)
        self.store = store if store is not None else DeliveryStore(DELIVERY_DB_PATH)
        self.agent = agent if agent is not None else salomao
        # Bounded striped locks: webhook + polling cannot process the same thread
        # concurrently, without retaining an unbounded map of customer IDs.
        self._locks = [Lock() for _ in range(64)]

    @staticmethod
    def get_session_id_for_thread(thread_id):
        return f"whatsapp_salomao_thread_{thread_id}"

    @staticmethod
    def _eligible(ticket):
        props = (ticket or {}).get("properties", {})
        return (str(props.get("hs_pipeline", "")) == SALOMAO_PIPELINE
                and str(props.get("hs_pipeline_stage", "")) == SALOMAO_STATUS
                and str(props.get("hubspot_owner_id", "")) == SALOMAO_ACTOR_ID.removeprefix("A-"))

    @staticmethod
    def _ineligible_reason(ticket):
        if not ticket:
            return "ticket_unavailable"
        props = ticket.get("properties", {})
        for key, expected in (("hs_pipeline", SALOMAO_PIPELINE), ("hs_pipeline_stage", SALOMAO_STATUS),
                              ("hubspot_owner_id", SALOMAO_ACTOR_ID.removeprefix("A-"))):
            if key not in props or props[key] is None:
                return "missing_" + key
            if str(props[key]) != expected:
                return "different_" + key
        return ""

    def get_unprocessed_visitor_messages(self, thread_id):
        messages = parse_incoming_messages(get_thread_messages(thread_id, strict=True))
        if self.memory and not self.store.conversation_messages(thread_id):
            restored = self.memory.load(thread_id)
            if restored:
                self.store.restore_memory(thread_id, restored)
                logger.warning("Historico recuperado; entradas antigas sem recibo nao serao reenviadas", extra={
                    "event": "memory.receipts_review", "thread_id": str(thread_id)})
        self.store.remember_messages(thread_id, messages)
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
        logger.debug("Mensagens verificadas", extra={"event": "messages.scanned", "thread_id": str(thread_id),
            "message_count": len(messages), "pending_count": len(pending), "skipped_count": len(messages) - len(pending)})
        return sorted(pending, key=lambda msg: msg.get("created_at", ""))

    def _save_memory(self, thread_id):
        if self.memory:
            self.memory.save(thread_id, self.store.conversation_messages(thread_id))

    @staticmethod
    def _merge_pending(previous, observed):
        # A temporarily missing row/page must not erase a message already seen.
        messages = {str(message["id"]): message for message in previous + observed}
        return sorted(messages.values(), key=lambda message: (message.get("created_at", ""), str(message["id"])))

    def _wait_for_quiet(self, thread_id, pending, deadline, batch_cutoff):
        """Require five seconds since the last message, not since each webhook.

        Runs in the existing worker thread, under the conversation lock. If the
        customer keeps writing, close the batch after twenty seconds. Messages
        beyond that fixed boundary belong to the next batch, including during
        generation, so additions cannot postpone the same answer forever.
        """
        while pending:
            timestamps = [message_time(message.get("created_at")) for message in pending]
            if any(timestamp is None for timestamp in timestamps):
                return None
            timestamps = [timestamp for timestamp in timestamps if timestamp <= batch_cutoff]
            if not timestamps:
                return pending
            quiet_for = (datetime.now(timezone.utc) - max(timestamps)).total_seconds()
            remaining = min(self.debounce_seconds, max(0, self.debounce_seconds - quiet_for))
            if remaining <= 0:
                return pending
            budget = deadline - time.monotonic()
            if budget <= 0:
                logger.info("Limite de espera atingido; lote fechado", extra={
                    "event": "turn.debounce_limit", "thread_id": str(thread_id), "reason": "batch_wait_limit"})
                return pending
            logger.debug("Aguardando pausa nas mensagens", extra={"event": "turn.debounce",
                "thread_id": str(thread_id), "pending_count": len(pending), "wait_seconds": round(remaining, 3)})
            time.sleep(min(remaining, budget))
            observed = self.get_unprocessed_visitor_messages(thread_id)
            pending = self._merge_pending(pending, observed)
        return pending

    def _coalesce_pending(self, thread_id, pending):
        """Several text bubbles before an answer form one customer turn."""
        outgoing = [message_time(m.get("created_at")) for m in self.store.conversation_messages(thread_id)
                    if not m.get("is_from_visitor")]
        groups = []
        for message in pending:
            previous = groups[-1] if groups else None
            start = message_time(previous.get("created_at")) if previous else None
            end = message_time(message.get("created_at"))
            if (previous and start and end and not previous.get("raw", {}).get("attachments")
                    and not message.get("raw", {}).get("attachments")
                    and not any(t and start < t < end for t in outgoing)
                    and len(previous.get("text", "")) + len(message.get("text", "")) <= 8000):
                groups[-1] = {**message, "text": previous.get("text", "") + "\n" + message.get("text", ""),
                    "source_message_ids": previous.get("source_message_ids", [previous["id"]]) + [message["id"]]}
            else:
                groups.append(message)
        return groups

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
        try:
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
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        except ValueError:
            raise
        except requests.RequestException:
            raise ValueError("attachment_download_failed") from None
        logger.info("Anexo baixado", extra={"event": "attachment.downloaded",
            "attachment_bytes": len(data), "content_type": content_type})
        return base64.b64encode(data).decode("ascii")

    @staticmethod
    def _attachment_extension(attachment):
        candidates = [urlsplit(str(attachment.get("url", ""))).path,
                      str(attachment.get("name") or attachment.get("fileName") or "")]
        extensions = []
        for candidate in candidates:
            leaf = candidate.rsplit("/", 1)[-1]
            if "." in leaf:
                extension = leaf.rsplit(".", 1)[-1].lower().strip()
                if extension:
                    extensions.append(extension)
        known = HubSpotSalomaoBot.AUDIO_EXTENSIONS | HubSpotSalomaoBot.IMAGE_EXTENSIONS
        return next((extension for extension in extensions if extension in known), extensions[0] if extensions else "")

    def process_message(self, thread_id, message):
        message_text = message.get("text", "")
        # Human support is a routing command, even when the same message also
        # mentions a subject outside the assistant's knowledge scope.
        if requests_human(message_text):
            logger.info("Pedido de suporte humano identificado", extra={
                "event": "handoff.requested", "thread_id": str(thread_id), "message_id": message.get("id")})
            return {"success": True, "response": "Vou encaminhar seu atendimento para a equipe de Suporte N1 da inChurch.",
                    "answer_status": "human_handoff", "transfer_requested": True,
                    "handoff_reason": "Pedido explícito do cliente para falar com o suporte.",
                    "scope_policy_version": SCOPE_POLICY_VERSION, "model_used": "deterministic_handoff"}
        if explicit_external_request(message_text):
            logger.info("Pergunta externa bloqueada antes do agente", extra={"event": "scope.input_blocked", "reason": "deterministic_rule"})
            return {"success": True, "response": SCOPE_REDIRECT, "answer_status": "out_of_scope",
                    "transfer_requested": False, "scope_policy_version": SCOPE_POLICY_VERSION}
        attachments = message.get("raw", {}).get("attachments", [])
        current_ids = set(message.get("source_message_ids", [message.get("id")]))
        history = history_before([m for m in self.store.conversation_messages(thread_id) if m["id"] not in current_ids], message)
        kwargs = {"conversation_history": history}
        selected, truncated = bounded_history(history)
        logger.info("Contexto da conversa preparado", extra={"event": "context.loaded", "thread_id": str(thread_id),
            "message_id": message.get("id"), "context_messages": len(selected), "context_truncated": truncated,
            "context_chars": sum(len(m["content"]) for m in selected), "source": "hubspot_and_delivery_cache"})
        if attachments:
            logger.info("Anexos recebidos", extra={"event": "attachment.received", "thread_id": str(thread_id),
                "message_id": message.get("id"), "attachment_count": len(attachments)})
        try:
            for attachment in attachments:
                url = attachment.get("url", "")
                kind = str(attachment.get("type", "")).lower()
                mime = attachment.get("mimeType") or attachment.get("contentType") or mimetypes.guess_type(urlsplit(url).path)[0] or ""
                extension = self._attachment_extension(attachment)
                logger.info("Anexo classificado", extra={"event": "attachment.classified",
                    "attachment_kind": kind or "unknown", "attachment_format": extension or "unknown",
                    "content_type": mime or "unknown"})
                if mime.startswith("image/") or kind == "image" or extension in self.IMAGE_EXTENSIONS:
                    if "image_base64" in kwargs:
                        raise ValueError("multiple_images")
                    kwargs["image_base64"] = self._download_attachment_as_base64(url)
                    kwargs["image_mime_type"] = mime if mime.startswith("image/") else "image/jpeg"
                elif mime.startswith("audio/") or kind == "audio" or extension in self.AUDIO_EXTENSIONS:
                    if "audio_base64" in kwargs:
                        raise ValueError("multiple_audio")
                    kwargs["audio_base64"] = self._download_attachment_as_base64(url)
                    mime_format = {"audio/mpeg": "mp3", "audio/mp4": "mp4", "audio/x-m4a": "m4a",
                                   "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm",
                                   "audio/ogg": "ogg"}.get(mime.lower(), "")
                    kwargs["audio_format"] = {"oga": "ogg", "ptt": "ogg"}.get(
                        extension, extension if extension in self.AUDIO_EXTENSIONS else mime_format or "mp4")
                else:
                    raise ValueError("unsupported_attachment")
        except ValueError as exc:
            logger.warning("Anexo rejeitado", extra={"event": "attachment.rejected", "reason": str(exc),
                "thread_id": str(thread_id), "message_id": message.get("id")})
            return {"response": "Não consegui ler esse anexo. Envie uma imagem ou áudio por vez, ou descreva a dúvida em texto.",
                    "success": False, "transfer_requested": False, "answer_status": "unavailable", "scope_policy_version": SCOPE_POLICY_VERSION}
        try:
            result = self.agent.process_message(
                message=message_text,
                session_id=self.get_session_id_for_thread(thread_id),
                originating_channel="whatsapp", **kwargs,
            )
        except Exception as exc:
            logger.error("Message generation failed | type=%s", type(exc).__name__)
            result = {"success": False, "answer_status": "unavailable", "transfer_requested": False}
        if not result.get("response"):
            result["response"] = "Não consegui consultar a orientação agora. Tente novamente em instantes ou peça para falar com um atendente."
        if result.get("audio_transcription"):
            self.store.remember_messages(thread_id, [{**message, "is_from_visitor": True,
                "text": "\n".join(filter(None, [message.get("text"), result["audio_transcription"]]))}])
        # A documented knowledge gap is actionable in the WhatsApp helpdesk.
        if result.get("answer_status") == "no_match":
            result["transfer_requested"] = True
            if not result.get("handoff_reason"):
                result["handoff_reason"] = "A base oficial não contém orientação suficiente para concluir o atendimento."
        if result.get("transfer_requested"):
            result["response"] = "Vou encaminhar seu atendimento para a equipe de Suporte N1 da inChurch."
        result["response"] = format_whatsapp(result["response"])
        if (obvious_external_answer(result["response"]) or
                (result.get("scope_policy_version") != SCOPE_POLICY_VERSION and
                 self.agent.validate_response_scope(message.get("text", ""), result["response"]) is not True)):
            result.update(response=SCOPE_UNAVAILABLE, answer_status="scope_blocked", transfer_requested=False)
            logger.warning("Resposta impedida de entrar na fila", extra={"event": "scope.output_blocked"})
        result["scope_policy_version"] = SCOPE_POLICY_VERSION
        return result

    def _deliver(self, entry, ticket_id):
        # Never trust a caller's stale outbox snapshot.
        entry = self.store.get(entry["thread_id"], entry["message_id"])
        if entry["complete"]:
            return {"message_id": entry["message_id"], "sent": False, "already_complete": True}
        thread_id, message_id, payload = entry["thread_id"], entry["message_id"], entry["payload"]
        if not approved_delivery(payload):
            # Retain the payload and confirmed-part receipts for audit. Do not
            # replay a legacy/unvalidated reply after deploying a stricter policy.
            self.store.quarantine(thread_id, message_id, "scope_approval_missing_or_changed")
            logger.warning("Entrega nao aprovada retida para auditoria", extra={"event": "delivery.quarantined",
                "thread_id": thread_id, "message_id": message_id, "reason": "scope_approval_missing_or_changed"})
            return {"message_id": message_id, "sent": False, "answer_status": "scope_blocked"}
        for index in range(entry["sent_parts"], len(payload["parts"])):
            state = self.store.begin_part(thread_id, message_id, index)
            if state == "confirmed":
                continue
            if state != "send":
                logger.debug("Entrega retida aguardando conferencia", extra={"event": "delivery.held", "reason": state})
                return {"message_id": message_id, "sent": False, "error": "delivery_uncertain", "needs_review": True}
            try:
                sent = reply_to_visitor(thread_id, payload["parts"][index])
            except HubSpotSendRejected:
                self.store.failed_part(thread_id, message_id, index, retryable=True)
                logger.warning("Envio rejeitado; nova tentativa permitida", extra={"event": "delivery.rejected", "part": index + 1})
                return {"message_id": message_id, "sent": False, "error": "delivery_rejected"}
            except Exception:
                sent = None
            if not sent or not sent.get("id"):
                self.store.failed_part(thread_id, message_id, index)
                logger.error("Envio sem confirmacao; reenvio automatico bloqueado", extra={"event": "delivery.uncertain",
                    "thread_id": thread_id, "message_id": message_id, "part": index + 1})
                return {"message_id": message_id, "sent": False, "error": "delivery_uncertain", "needs_review": True}
            self.store.confirm_delivery_part(thread_id, message_id, index, sent, payload["parts"][index])
            self._save_memory(thread_id)
            logger.info("Parte da resposta enviada", extra={"event": "delivery.part_sent", "thread_id": thread_id,
                "message_id": message_id, "part": index + 1, "parts": len(payload["parts"])})
        if payload.get("transfer_requested"):
            note_body = payload.get("handoff_note_body")
            if not note_body:
                note_body = build_handoff_note(
                    thread_id=thread_id, message_id=message_id,
                    messages=self.store.conversation_messages(thread_id),
                    reason=payload.get("handoff_reason"), sources=payload.get("sources"),
                )
                entry = self.store.update_payload(thread_id, message_id, {"handoff_note_body": note_body})
                payload = entry["payload"]
            note_state = self.store.begin_handoff_note(thread_id, message_id)
            if note_state == "send":
                try:
                    note = create_ticket_handoff_note(ticket_id, note_body)
                except HubSpotNoteRejected:
                    self.store.failed_handoff_note(thread_id, message_id, retryable=True)
                    logger.warning("Observacao rejeitada; nova tentativa permitida", extra={
                        "event": "handoff.note_rejected", "thread_id": thread_id, "message_id": message_id})
                    return {"message_id": message_id, "sent": True, "transferred": False,
                            "error": "handoff_note_rejected"}
                except Exception:
                    note = None
                if not note or not note.get("id"):
                    self.store.failed_handoff_note(thread_id, message_id)
                    logger.error("Observacao sem confirmacao; transferencia retida", extra={
                        "event": "handoff.note_uncertain", "thread_id": thread_id, "message_id": message_id})
                    return {"message_id": message_id, "sent": True, "transferred": False,
                            "error": "handoff_note_uncertain", "needs_review": True}
                self.store.confirm_handoff_note(thread_id, message_id, note)
                logger.info("Observacao de transferencia criada", extra={
                    "event": "handoff.note_created", "thread_id": thread_id, "message_id": message_id,
                    "note_id": str(note["id"])})
            elif note_state != "confirmed":
                logger.warning("Transferencia retida: observacao exige conferencia", extra={
                    "event": "handoff.note_held", "thread_id": thread_id, "message_id": message_id,
                    "reason": note_state})
                return {"message_id": message_id, "sent": True, "transferred": False,
                        "error": "handoff_note_uncertain", "needs_review": True}
            if not ticket_id or not transfer_ticket_to_human_support(ticket_id):
                # Parts remain confirmed; next poll retries only the handoff.
                return {"message_id": message_id, "sent": True, "transferred": False,
                        "error": "Transferência pendente; será tentada novamente"}
        self.store.complete(thread_id, message_id)
        logger.info("Entrega concluida", extra={"event": "delivery.completed", "thread_id": thread_id,
            "message_id": message_id, "handoff": bool(payload.get("transfer_requested"))})
        return {"message_id": message_id, "user_message": payload.get("user_message", ""),
                "bot_response": payload["response"], "sent": True,
                "transferred": bool(payload.get("transfer_requested")),
                "answer_status": payload.get("answer_status", "answered")}

    def process_thread(self, thread_id, ticket_id=None):
        with self.store.thread_lock(thread_id) as acquired:
            if not acquired:
                logger.debug("Outro processo esta atendendo a conversa", extra={"event": "turn.skipped", "reason": "process_locked"})
                return []
            return self._process_thread_locked(thread_id, ticket_id)

    def _process_thread_locked(self, thread_id, ticket_id=None):
        lock = self._locks[hash(thread_id) % len(self._locks)]
        if not lock.acquire(blocking=False):
            logger.debug("Conversa ja esta em processamento", extra={"event": "turn.skipped", "thread_id": str(thread_id), "reason": "thread_locked"})
            return []
        try:
            # Recheck eligibility even for the direct thread-processing endpoint.
            if not ticket_id:
                return []
            if not self._eligible(get_ticket_by_id(ticket_id)):
                return []
            responses = []
            for entry in self.store.pending(thread_id):
                with log_context(thread_id=str(thread_id), ticket_id=str(ticket_id), message_id=entry["message_id"],
                                 session_id=self.get_session_id_for_thread(thread_id), run_id=entry["payload"].get("run_id")):
                    logger.info("Retomando entrega pendente", extra={"event": "delivery.retry"})
                    delivered = self._deliver(entry, ticket_id)
                responses.append(delivered)
                if delivered.get("error") or delivered.get("transferred"):
                    return responses
            try:
                pending = self.get_unprocessed_visitor_messages(thread_id)
            except HubSpotReadError:
                logger.warning("Processamento adiado: historico indisponivel", extra={"event": "turn.deferred", "reason": "history_unavailable"})
                return responses + [{"error": "history_unavailable", "sent": False}]
            generations = 0
            while pending and generations < self.MAX_GENERATIONS_PER_CYCLE:
                pending = [m for m in pending if not self.store.get(thread_id, m["id"])]
                if not pending:
                    break
                batch_start = message_time(pending[0].get("created_at"))
                if batch_start is None:
                    break
                batch_cutoff = batch_start + timedelta(seconds=self.MAX_DEBOUNCE_WAIT_SECONDS)
                deadline = time.monotonic() + min(self.MAX_DEBOUNCE_WAIT_SECONDS,
                    max(0, (batch_cutoff - datetime.now(timezone.utc)).total_seconds()))
                try:
                    pending = self._wait_for_quiet(thread_id, pending, deadline, batch_cutoff)
                except HubSpotReadError:
                    return responses + [{"error": "history_unavailable", "sent": False}]
                if not pending:
                    break
                batch = [m for m in pending if message_time(m.get("created_at")) <= batch_cutoff]
                message = self._coalesce_pending(thread_id, batch)[0]
                observed_ids = {str(m["id"]) for m in pending}
                # Ownership may change while a previous response was generated.
                if not self._eligible(get_ticket_by_id(ticket_id)):
                    break
                run_id = uuid.uuid4().hex
                with log_context(thread_id=str(thread_id), ticket_id=str(ticket_id), message_id=message["id"],
                                 session_id=self.get_session_id_for_thread(thread_id), run_id=run_id):
                    started = time.perf_counter()
                    logger.info("Processando mensagem", extra={"event": "turn.started"})
                    self._save_memory(thread_id)
                    result = self.process_message(thread_id, message)
                    generations += 1
                    logger.info("Resposta preparada", extra={"event": "turn.generated", "model": result.get("model_used"),
                        "answer_status": result.get("answer_status"), "handoff": bool(result.get("transfer_requested")),
                        "duration_ms": round((time.perf_counter() - started) * 1000)})
                    # No outbox row/receipt has been created yet: this draft can
                    # safely be discarded when a complement arrives mid-generation.
                    try:
                        observed = self.get_unprocessed_visitor_messages(thread_id)
                    except HubSpotReadError:
                        logger.warning("Envio adiado: nao foi possivel conferir novas mensagens", extra={
                            "event": "turn.deferred", "reason": "pre_send_history_unavailable"})
                        return responses + [{"error": "history_unavailable", "sent": False}]
                    pending = self._merge_pending(pending, observed)
                    if {str(m["id"]) for m in observed
                            if message_time(m.get("created_at")) <= batch_cutoff} - observed_ids:
                        logger.info("Complemento recebido; resposta sera atualizada", extra={
                            "event": "turn.draft_superseded", "pending_count": len(pending)})
                        continue
                    parts = split_whatsapp(result["response"], WHATSAPP_MAX_MESSAGE_LENGTH)
                    entry = self.store.enqueue(thread_id, message["id"], {
                        "response": result["response"], "parts": parts, "run_id": run_id,
                        "user_message": message.get("text", ""),
                        "source_message_ids": message.get("source_message_ids", [message["id"]]),
                        "transfer_requested": bool(result.get("transfer_requested")),
                        "handoff_reason": result.get("handoff_reason"),
                        "sources": result.get("sources", []),
                        "answer_status": result.get("answer_status", "answered"),
                        "scope_policy_version": result.get("scope_policy_version"),
                        "scope_digest": approval_digest(result["response"], parts),
                    })
                    if result.get("transfer_requested"):
                        note_body = build_handoff_note(
                            thread_id=thread_id, message_id=message["id"],
                            messages=self.store.conversation_messages(thread_id),
                            reason=result.get("handoff_reason"), sources=result.get("sources", []),
                        )
                        entry = self.store.update_payload(thread_id, message["id"], {"handoff_note_body": note_body})
                    if not self._eligible(get_ticket_by_id(ticket_id)):
                        logger.info("Envio suspenso: ticket fora dos filtros", extra={"event": "delivery.deferred", "reason": "eligibility_changed"})
                        break
                    delivered = self._deliver(entry, ticket_id)
                    responses.append(delivered)
                    if delivered.get("error") or delivered.get("transferred"):
                        break
            if generations >= self.MAX_GENERATIONS_PER_CYCLE and any(not self.store.get(thread_id, m["id"]) for m in pending):
                logger.info("Mais mensagens aguardam o proximo ciclo", extra={"event": "turn.deferred", "reason": "generation_cycle_limit"})
            return responses
        finally:
            lock.release()

    def process_ticket(self, ticket_id):
        reason = self._ineligible_reason(get_ticket_by_id(ticket_id))
        if reason:
            logger.debug("Ticket nao processado", extra={"event": "ticket.skipped", "ticket_id": str(ticket_id), "reason": reason})
            return {"success": False, "ticket_id": ticket_id, "responses": [], "error": "Ticket fora dos filtros do Salomão", "reason": reason}
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
        with self.store.thread_lock(thread_id) as acquired:
            if not acquired:
                return {"success": False, "error": "Conversa em processamento"}
            return self._send_manual_locked(ticket_id, thread_id, question)

    def _send_manual_locked(self, ticket_id, thread_id, question):
        lock = self._locks[hash(thread_id) % len(self._locks)]
        with lock:
            if self.store.pending(thread_id):
                return {"success": False, "error": "Há uma entrega pendente nesta conversa"}
            observed = parse_incoming_messages(get_thread_messages(thread_id, strict=True))
            self.store.remember_messages(thread_id, observed)
            result = self.process_message(thread_id, {"text": question, "id": "manual", "created_at": datetime.now(timezone.utc).isoformat()})
            parts = split_whatsapp(result["response"], WHATSAPP_MAX_MESSAGE_LENGTH)
            entry = self.store.enqueue(thread_id, "manual_" + uuid.uuid4().hex, {
                **result, "user_message": question,
                "parts": parts, "scope_digest": approval_digest(result["response"], parts),
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
