"""
API Backend do Salomão para HubSpot.
FLUXO COM POLLING:
- Polling a cada 10s busca tickets que atendem os 3 filtros:
  1. Pipeline do Salomão (636594474)
  2. Status do Salomão (1269308450)
  3. Proprietário = Salomão (81908844)
- Processa APENAS tickets que passam nos 3 filtros
"""

import os
import uuid
import logging
import asyncio
import time
from typing import Optional, List
from datetime import datetime
from contextlib import asynccontextmanager
from contextlib import suppress
from config import HUBSPOT_POLLING_ENABLED, HUBSPOT_POLLING_INTERVAL
from logging_config import configure_logging, log_context
from scope_policy import SCOPE_POLICY_VERSION

configure_logging()

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from hubspot_bot import hubspot_bot, process_single_ticket
from hubspot_service import (
    get_tickets_for_salomao,
    get_conversation_thread_by_ticket,
    get_thread_messages,
    get_thread_by_id,
    get_ticket_by_id,
    get_ticket_contact,
    parse_incoming_messages,
    reply_to_visitor,
    transfer_to_human,
    SALOMAO_PIPELINE,
    SALOMAO_STATUS,
    SALOMAO_ENTRY_PROPERTY,
    SALOMAO_ACTOR_ID,
    HUMAN_PIPELINE,
    HUMAN_STATUS
)
from hubspot_database import hubspot_db
from salomao_agent import salomao
from database import db

logger = logging.getLogger('salomao.api')

POLLING_INTERVAL = HUBSPOT_POLLING_INTERVAL
polling_active = False


async def polling_loop():
    """
    Loop de polling que busca tickets a cada POLLING_INTERVAL segundos.
    Busca APENAS tickets que atendem os 3 filtros:
    1. Pipeline do Salomão
    2. Status do Salomão
    3. Proprietário = Salomão
    """
    global polling_active
    owner_id = SALOMAO_ACTOR_ID.replace("A-", "")

    logger.info("Busca automatica iniciada", extra={"event": "polling.started", "interval_seconds": POLLING_INTERVAL,
        "pipeline_id": SALOMAO_PIPELINE, "stage_id": SALOMAO_STATUS, "owner_id": owner_id})
    previous_state, last_report = None, 0.0

    while polling_active:
        try:
            # Busca tickets com os 3 filtros (já filtrado na API)
            tickets = await asyncio.to_thread(get_tickets_for_salomao, strict=True)

            responses, errors = 0, 0
            reasons = set()
            if tickets:
                for ticket in tickets:
                    ticket_id = ticket.get("id")
                    try:
                        with log_context(ticket_id=str(ticket_id), source="polling"):
                            result = await asyncio.to_thread(process_single_ticket, ticket_id)
                        responses += sum(bool(r.get("sent")) for r in result.get("responses", []))
                        if result.get("error") or not result.get("success", True):
                            errors += 1
                            reasons.add(result.get("reason") or result.get("error") or "processing_failed")
                    except Exception as e:
                        errors += 1
                        logger.exception("Falha no processamento do ticket", extra={"event": "ticket.failed", "ticket_id": str(ticket_id)})
            state = (tuple(str(t.get("id")) for t in tickets), tuple(sorted(reasons)), errors)
            if state != previous_state or responses or time.monotonic() - last_report >= 60:
                logger.log(logging.WARNING if errors else logging.INFO, "Resumo da busca automatica", extra={
                    "event": "polling.summary", "ticket_count": len(tickets), "response_count": responses,
                    "error_count": errors, "reason": ",".join(sorted(reasons)) or None})
                previous_state, last_report = state, time.monotonic()

        except Exception as e:
            logger.exception("Falha no ciclo de busca", extra={"event": "polling.failed"})

        await asyncio.sleep(POLLING_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia o ciclo de vida da aplicação."""
    global polling_active
    polling_active = HUBSPOT_POLLING_ENABLED
    task = asyncio.create_task(polling_loop()) if polling_active else None
    logger.info("Servico iniciado", extra={"event": "service.started", "polling_enabled": polling_active, "scope_policy_version": SCOPE_POLICY_VERSION})
    yield
    # Shutdown
    polling_active = False
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    logger.info("Servico encerrado", extra={"event": "service.stopped"})


app = FastAPI(
    title="Salomão HubSpot Bot",
    description="API do agente Salomão integrado com HubSpot Conversations (WhatsApp)",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProcessTicketRequest(BaseModel):
    ticket_id: str


class ProcessThreadRequest(BaseModel):
    thread_id: str


class WebhookPayload(BaseModel):
    subscriptionType: Optional[str] = None
    objectId: Optional[str] = None
    propertyName: Optional[str] = None
    propertyValue: Optional[str] = None
    messageId: Optional[str] = None
    messageType: Optional[str] = None


@app.get("/")
async def root():
    return {
        "message": "Salomão HubSpot Bot",
        "status": "online",
        "version": "2.0.0",
        "integration": "HubSpot Conversations API",
        "target_pipeline": SALOMAO_PIPELINE,
        "target_status": SALOMAO_STATUS
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "scope_policy_version": SCOPE_POLICY_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/tickets")
async def list_tickets():
    """Lista todos os tickets na pipeline do Salomão."""
    tickets = get_tickets_for_salomao()
    return {
        "pipeline_id": SALOMAO_PIPELINE,
        "status_id": SALOMAO_STATUS,
        "total": len(tickets),
        "tickets": [
            {
                "id": t.get("id"),
                "subject": t.get("properties", {}).get("subject", ""),
                "created": t.get("properties", {}).get("createdate", "")
            }
            for t in tickets
        ]
    }


@app.post("/process/ticket")
async def process_ticket_endpoint(request: ProcessTicketRequest):
    """Processa um ticket específico."""
    try:
        result = await asyncio.to_thread(process_single_ticket, request.ticket_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/process/all")
async def process_all_tickets_endpoint(background_tasks: BackgroundTasks):
    """Processa todos os tickets pendentes na pipeline do Salomão."""
    tickets = get_tickets_for_salomao()

    if not tickets:
        return {
            "message": "Nenhum ticket encontrado",
            "total": 0
        }

    async def process_all():
        for ticket in tickets:
            ticket_id = ticket.get("id")
            try:
                await asyncio.to_thread(process_single_ticket, ticket_id)
            except Exception as e:
                logger.error(f"Erro ao processar ticket {ticket_id}: {type(e).__name__}")

    background_tasks.add_task(process_all)

    return {
        "message": f"Processamento iniciado para {len(tickets)} tickets",
        "total": len(tickets),
        "status": "processing"
    }


@app.get("/thread/{thread_id}")
async def get_thread(thread_id: str):
    """Obtém informações de um thread específico."""
    thread = get_thread_by_id(thread_id)

    if not thread:
        raise HTTPException(status_code=404, detail="Thread não encontrado")

    messages = get_thread_messages(thread_id)
    processed = parse_incoming_messages(messages)

    return {
        "thread_id": thread_id,
        "status": thread.get("status"),
        "inbox_id": thread.get("inboxId"),
        "channel_id": thread.get("originalChannelId"),
        "created_at": thread.get("createdAt"),
        "message_count": len(processed),
        "messages": [
            {
                "id": m["id"],
                "text": m["text"][:200] + "..." if len(m["text"]) > 200 else m["text"],
                "from_visitor": m["is_from_visitor"],
                "created_at": m["created_at"]
            }
            for m in processed
        ]
    }


@app.get("/thread/{thread_id}/messages")
async def get_thread_messages_endpoint(thread_id: str):
    """Lista todas as mensagens de um thread."""
    messages = get_thread_messages(thread_id)
    processed = parse_incoming_messages(messages)

    return {
        "thread_id": thread_id,
        "total": len(processed),
        "messages": processed
    }


@app.post("/thread/{thread_id}/process")
async def process_thread_endpoint(thread_id: str):
    """Processa mensagens pendentes de um thread específico."""
    try:
        thread = await asyncio.to_thread(get_thread_by_id, thread_id)
        ticket_id = (thread or {}).get("associatedTicketId")
        responses = await asyncio.to_thread(hubspot_bot.process_thread, thread_id, ticket_id)
        return {
            "thread_id": thread_id,
            "processed": len(responses),
            "responses": responses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ticket/{ticket_id}/thread")
async def get_ticket_thread(ticket_id: str):
    """Obtém o thread de conversa associado a um ticket."""
    thread = get_conversation_thread_by_ticket(ticket_id)

    if not thread:
        raise HTTPException(status_code=404, detail="Thread não encontrado para este ticket")

    thread_id = thread.get("id")
    messages = get_thread_messages(thread_id)
    processed = parse_incoming_messages(messages)

    return {
        "ticket_id": ticket_id,
        "thread_id": thread_id,
        "status": thread.get("status"),
        "message_count": len(processed),
        "last_message": processed[-1] if processed else None
    }


@app.post("/webhook/hubspot")
async def hubspot_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint para receber webhooks do HubSpot.
    PROCESSA tickets que atendem os 3 filtros:
    1. Pipeline do Salomão
    2. Status do Salomão
    3. Proprietário = Salomão
    """
    try:
        body = await request.json()
        logger.info(f"Webhook recebido")

        events = body if isinstance(body, list) else [body]

        for event in events:
            subscription_type = event.get("subscriptionType", "")
            object_id = str(event.get("objectId", ""))

            logger.info(f"Evento: {subscription_type} | ID: {object_id}")

            # conversation.newMessage - Nova mensagem em conversa
            if subscription_type == "conversation.newMessage":
                thread_id = object_id
                logger.info(f"Nova mensagem no thread: {thread_id}")
                background_tasks.add_task(process_message_if_valid, thread_id)
                continue

            # conversation.creation - Nova conversa criada
            if subscription_type == "conversation.creation":
                thread_id = object_id
                logger.info(f"Nova conversa: {thread_id}")
                background_tasks.add_task(process_message_if_valid, thread_id)
                continue

            # ticket.propertyChange - Mudança de propriedade do ticket
            if subscription_type == "ticket.propertyChange":
                ticket_id = object_id
                property_name = event.get("propertyName", "")
                property_value = event.get("propertyValue", "")
                logger.debug("Propriedade do ticket alterada", extra={"event": "webhook.ticket_changed", "ticket_id": ticket_id})

                # O valor desta propriedade é a data de entrada, NÃO o ID do
                # status. Revalidar pipeline/status/proprietário no ticket.
                if property_name == SALOMAO_ENTRY_PROPERTY and str(property_value or "").strip():
                    background_tasks.add_task(process_ticket_if_valid, ticket_id)
                continue

            logger.debug(f"Evento ignorado: {subscription_type}")

        return {"status": "ok", "received": len(events)}

    except Exception as e:
        logger.error(f"Erro webhook: {type(e).__name__}")
        return {"status": "error", "message": str(e)}


async def process_ticket_if_valid(ticket_id: str):
    """Webhook usa os mesmos filtros e o mesmo processamento do polling."""
    with log_context(ticket_id=str(ticket_id), source="webhook"):
        try:
            ticket = await asyncio.to_thread(get_ticket_by_id, ticket_id)
            reason = hubspot_bot._ineligible_reason(ticket)
            if reason:
                logger.info("Ticket nao processado pelo webhook", extra={"event": "webhook.filtered", "reason": reason})
                return
            result = await asyncio.to_thread(process_single_ticket, ticket_id)
            filtered = bool(result.get("reason"))
            failed = not result.get("success", False) and not filtered
            logger.log(logging.WARNING if failed else logging.INFO, "Processamento do webhook concluido", extra={
                "event": "webhook.processed", "reason": result.get("reason") or ("processing_failed" if failed else None),
                "response_count": sum(bool(r.get("sent")) for r in result.get("responses", [])),
                "error_count": int(failed)})
        except Exception:
            logger.exception("Falha ao processar webhook", extra={"event": "webhook.failed"})


async def process_message_if_valid(thread_id: str):
    """
    Processa mensagem de um thread APENAS se o ticket associado atender os 3 filtros.
    """
    try:
        thread = await asyncio.to_thread(get_thread_by_id, thread_id)
        if not thread:
            logger.debug(f"Thread {thread_id} não encontrado")
            return

        # Busca ticket associado
        associated_ticket_id = thread.get("associatedTicketId")
        if not associated_ticket_id:
            logger.debug(f"Thread {thread_id} sem ticket associado")
            return

        # Verifica se o ticket passa nos filtros
        await process_ticket_if_valid(associated_ticket_id)

    except Exception as e:
        logger.error(f"Erro ao processar thread {thread_id}: {type(e).__name__}")


async def create_session_for_conversation(thread_id: str):
    """
    Cria uma sessão no Supabase quando uma nova conversa é detectada.
    Busca informações do contato para melhor interação.
    """
    try:
        logger.info(f"Criando sessão para conversa {thread_id}")

        # Busca informações do thread
        thread = get_thread_by_id(thread_id)
        if not thread:
            logger.warning(f"Thread {thread_id} não encontrado")
            return

        # Busca ticket associado
        associated_ticket_id = thread.get("associatedTicketId")

        visitor_name = None
        visitor_email = None
        channel_id = thread.get("originalChannelId")
        channel_account_id = thread.get("originalChannelAccountId")

        # Tenta buscar informações do contato via ticket
        if associated_ticket_id:
            contact = get_ticket_contact(associated_ticket_id)
            if contact:
                visitor_name = f"{contact.get('firstname', '')} {contact.get('lastname', '')}".strip()
                visitor_email = contact.get('email')
                logger.info("Contato associado localizado", extra={"event": "contact.found"})

        # Busca visitor_actor_id das mensagens
        messages = get_thread_messages(thread_id, limit=5)
        visitor_actor_id = None
        for msg in messages:
            if msg.get("direction") == "INCOMING":
                senders = msg.get("senders", [])
                if senders:
                    visitor_actor_id = senders[0].get("actorId")
                    break

        # Cria ou obtém sessão no Supabase
        session = hubspot_db.get_or_create_session(
            thread_id=thread_id,
            ticket_id=associated_ticket_id,
            visitor_actor_id=visitor_actor_id,
            channel_id=channel_id,
            channel_account_id=channel_account_id,
            visitor_name=visitor_name,
            visitor_email=visitor_email
        )

        logger.info(f"Sessão criada: {session.get('session_id')}")

    except Exception as e:
        logger.error(f"Erro ao criar sessão: {type(e).__name__}")


@app.post("/ticket/{ticket_id}/transfer-to-human")
async def transfer_ticket_to_human(ticket_id: str):
    """
    Transfere um ticket para atendimento humano.
    Remove o proprietário e move para pipeline/status de humano.
    """
    try:
        success = transfer_to_human(ticket_id)
        if success:
            return {
                "success": True,
                "ticket_id": ticket_id,
                "message": "Ticket transferido para atendimento humano",
                "new_pipeline": HUMAN_PIPELINE,
                "new_status": HUMAN_STATUS
            }
        else:
            raise HTTPException(status_code=500, detail="Falha ao transferir ticket")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config")
async def get_config():
    """Retorna configuração atual do Salomão."""
    owner_id = SALOMAO_ACTOR_ID.replace("A-", "")
    return {
        "salomao_pipeline": SALOMAO_PIPELINE,
        "salomao_status": SALOMAO_STATUS,
        "salomao_entry_property": SALOMAO_ENTRY_PROPERTY,
        "salomao_owner_id": owner_id,
        "salomao_actor_id": SALOMAO_ACTOR_ID,
        "human_pipeline": HUMAN_PIPELINE,
        "human_status": HUMAN_STATUS,
        "polling_interval": POLLING_INTERVAL
    }


@app.get("/session/{session_id}")
async def get_session(session_id: str):
    """Obtém histórico de uma sessão do Salomão."""
    history = salomao.get_conversation_history(session_id)
    return {
        "session_id": session_id,
        "messages": history,
        "message_count": len(history)
    }


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    """Limpa uma sessão do Salomão."""
    salomao.clear_conversation(session_id)
    return {
        "success": True,
        "message": f"Sessão {session_id} limpa"
    }


@app.post("/test/chat")
async def test_chat(message: str, session_id: Optional[str] = None):
    """Endpoint de teste para enviar mensagens diretamente ao Salomão."""
    session_id = session_id or f"test_{uuid.uuid4()}"

    result = await asyncio.to_thread(salomao.process_message,
        message=message,
        session_id=session_id,
        originating_channel="whatsapp",
    )

    return {
        "session_id": session_id,
        "user_message": message,
        "bot_response": result.get("response", ""),
        "success": result.get("success", False),
        "tokens": result.get("tokens", {}),
        "answer_status": result.get("answer_status", "answered"),
        "transfer_requested": result.get("transfer_requested", False),
        "sources": result.get("sources", []),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
