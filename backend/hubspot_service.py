"""
Serviço de integração com HubSpot Conversations API.
Permite buscar conversas, mensagens e enviar respostas via WhatsApp.
"""

import os
import logging
import requests
from typing import Optional, List, Dict, Any
from datetime import datetime
from dotenv import load_dotenv
from config import WHATSAPP_MAX_MESSAGE_LENGTH
from whatsapp_formatting import format_whatsapp, message_length, whatsapp_rich_text

load_dotenv()

logger = logging.getLogger('salomao.hubspot')

HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN")
HUBSPOT_API_BASE = "https://api.hubapi.com"

# Pipeline e Status onde o Salomão atua
SALOMAO_PIPELINE = os.getenv("HUBSPOT_SALOMAO_PIPELINE", "636594474")
SALOMAO_STATUS = os.getenv("HUBSPOT_SALOMAO_STATUS", "1269308450")
SALOMAO_ENTRY_PROPERTY = os.getenv(
    "HUBSPOT_SALOMAO_ENTRY_PROPERTY", f"hs_v2_date_entered_{SALOMAO_STATUS}"
)

# Pipeline e Status para transferir para humano
HUMAN_PIPELINE = os.getenv("HUBSPOT_HUMAN_PIPELINE", "636459134")
HUMAN_STATUS = os.getenv("HUBSPOT_HUMAN_STATUS", "939275049")

# Actor ID do Salomão (usuário que envia mensagens)
SALOMAO_ACTOR_ID = os.getenv("HUBSPOT_SALOMAO_ACTOR_ID", "A-81908844")

# Aliases para compatibilidade
TARGET_PIPELINE = SALOMAO_PIPELINE
TARGET_STATUS = SALOMAO_STATUS

_cached_bot_actor_id = None


def get_headers() -> dict:
    """Retorna headers para requisições à API do HubSpot."""
    return {
        "Authorization": f"Bearer {HUBSPOT_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }


def get_tickets_in_pipeline(pipeline_id: str = TARGET_PIPELINE, stage_id: str = TARGET_STATUS) -> List[dict]:
    """
    Busca todos os tickets em uma pipeline/stage específica.

    Args:
        pipeline_id: ID da pipeline
        stage_id: ID do estágio

    Returns:
        Lista de tickets
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "hs_pipeline",
                            "operator": "EQ",
                            "value": pipeline_id
                        },
                        {
                            "propertyName": "hs_pipeline_stage",
                            "operator": "EQ",
                            "value": stage_id
                        }
                    ]
                }
            ],
            "properties": [
                "subject",
                "content",
                "hs_pipeline",
                "hs_pipeline_stage",
                "hs_ticket_priority",
                "createdate",
                "hs_lastmodifieddate"
            ],
            "limit": 100
        }

        response = requests.post(url, headers=get_headers(), json=payload, timeout=30)

        if response.status_code == 200:
            data = response.json()
            logger.info(f"✅ Encontrados {len(data.get('results', []))} tickets na pipeline {pipeline_id}")
            return data.get("results", [])
        else:
            logger.error(f"❌ Erro ao buscar tickets: {response.status_code} - {response.text}")
            return []

    except Exception as e:
        logger.error(f"❌ Exceção ao buscar tickets: {str(e)}")
        return []


HUMAN_SUPPORT_PIPELINE = HUMAN_PIPELINE
HUMAN_SUPPORT_STAGE = HUMAN_STATUS


def transfer_ticket_to_human_support(ticket_id: str) -> bool:
    """
    Transfere um ticket para a pipeline de atendimento humano.
    - Limpa o proprietário (para liberar para equipe humana)
    - Move para pipeline e status de atendimento humano

    Args:
        ticket_id: ID do ticket a ser transferido

    Returns:
        True se transferido com sucesso, False caso contrário
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}"
        payload = {
            "properties": {
                "hs_pipeline": HUMAN_SUPPORT_PIPELINE,
                "hs_pipeline_stage": HUMAN_SUPPORT_STAGE,
                "hubspot_owner_id": ""  # Limpa o proprietário para liberar para equipe humana
            }
        }

        logger.info(f"🔄 Transferindo ticket {ticket_id} para atendimento humano...")
        logger.info(f"   Pipeline: {HUMAN_SUPPORT_PIPELINE}")
        logger.info(f"   Stage: {HUMAN_SUPPORT_STAGE}")
        logger.info(f"   Owner: (limpo)")

        response = requests.patch(url, headers=get_headers(), json=payload, timeout=30)

        if response.status_code == 200:
            logger.info(f"✅ Ticket {ticket_id} transferido para atendimento humano com sucesso!")
            return True
        else:
            logger.error(f"❌ Erro ao transferir ticket: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.error(f"❌ Exceção ao transferir ticket: {str(e)}")
        return False


def get_ticket_by_id(ticket_id: str) -> Optional[dict]:
    """
    Busca um ticket específico pelo ID.

    Args:
        ticket_id: ID do ticket

    Returns:
        Dados do ticket ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}"
        params = {
            "properties": "subject,content,hs_pipeline,hs_pipeline_stage,hs_ticket_priority,createdate"
        }

        response = requests.get(url, headers=get_headers(), params=params, timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"❌ Erro ao buscar ticket {ticket_id}: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"❌ Exceção ao buscar ticket: {str(e)}")
        return None


def get_conversation_thread_by_ticket(ticket_id: str) -> Optional[dict]:
    """
    Busca o thread de conversa associado a um ticket.

    Args:
        ticket_id: ID do ticket

    Returns:
        Dados do thread ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/conversations/v3/conversations/threads"
        params = {
            "associatedTicketId": ticket_id,
            "associationType": "TICKET"
        }

        response = requests.get(url, headers=get_headers(), params=params, timeout=30)

        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            if results:
                logger.info(f"✅ Thread encontrado para ticket {ticket_id}: {results[0].get('id')}")
                return results[0]
            else:
                logger.warning(f"⚠️ Nenhum thread encontrado para ticket {ticket_id}")
                return None
        else:
            logger.error(f"❌ Erro ao buscar thread: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"❌ Exceção ao buscar thread: {str(e)}")
        return None


def get_thread_by_id(thread_id: str) -> Optional[dict]:
    """
    Busca um thread específico pelo ID.

    Args:
        thread_id: ID do thread

    Returns:
        Dados do thread ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/conversations/v3/conversations/threads/{thread_id}"

        response = requests.get(url, headers=get_headers(), timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"❌ Erro ao buscar thread {thread_id}: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"❌ Exceção ao buscar thread: {str(e)}")
        return None


def get_thread_messages(thread_id: str, limit: int = 50) -> List[dict]:
    """
    Busca as mensagens de um thread de conversa.

    Args:
        thread_id: ID do thread
        limit: Número máximo de mensagens

    Returns:
        Lista de mensagens ordenadas por data
    """
    try:
        url = f"{HUBSPOT_API_BASE}/conversations/v3/conversations/threads/{thread_id}/messages"
        params = {"limit": limit}

        response = requests.get(url, headers=get_headers(), params=params, timeout=30)

        if response.status_code == 200:
            data = response.json()
            messages = data.get("results", [])

            messages.sort(key=lambda x: x.get("createdAt", ""))

            logger.info(f"✅ {len(messages)} mensagens encontradas no thread {thread_id}")
            return messages
        else:
            logger.error(f"❌ Erro ao buscar mensagens: {response.status_code} - {response.text}")
            return []

    except Exception as e:
        logger.error(f"❌ Exceção ao buscar mensagens: {str(e)}")
        return []


def get_actor_info(actor_id: str) -> Optional[dict]:
    """
    Busca informações de um actor (remetente/destinatário).

    Args:
        actor_id: ID do actor (ex: V-123, A-456)

    Returns:
        Informações do actor ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/conversations/v3/conversations/actors/{actor_id}"

        response = requests.get(url, headers=get_headers(), timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            return None

    except Exception as e:
        logger.error(f"❌ Exceção ao buscar actor: {str(e)}")
        return None


def get_hubspot_bot_actor_id() -> Optional[str]:
    """
    Obtém o actorId do bot/integração para enviar mensagens.
    Busca o primeiro owner disponível no HubSpot.

    Returns:
        ActorId no formato A-{userId} ou None
    """
    global _cached_bot_actor_id

    if _cached_bot_actor_id:
        return _cached_bot_actor_id

    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/owners"
        response = requests.get(url, headers=get_headers(), timeout=30)

        if response.status_code == 200:
            data = response.json()
            owners = data.get("results", [])

            if owners:
                owner_id = owners[0].get("userId") or owners[0].get("id")
                if owner_id:
                    _cached_bot_actor_id = f"A-{owner_id}"
                    logger.info(f"✅ Bot actorId configurado: {_cached_bot_actor_id}")
                    return _cached_bot_actor_id

        logger.warning("⚠️ Nenhum owner encontrado no HubSpot")
        return None

    except Exception as e:
        logger.error(f"❌ Erro ao buscar bot actorId: {str(e)}")
        return None


def get_agent_actor_from_thread(thread_id: str) -> Optional[str]:
    """
    Busca um actorId de agente válido a partir das mensagens do thread.

    Args:
        thread_id: ID do thread

    Returns:
        ActorId de um agente ou None
    """
    try:
        messages = get_thread_messages(thread_id, limit=20)

        for msg in messages:
            direction = msg.get("direction", "")
            if direction == "OUTGOING":
                senders = msg.get("senders", [])
                for sender in senders:
                    actor_id = sender.get("actorId", "")
                    if actor_id.startswith("A-"):
                        logger.info(f"✅ Agent actorId encontrado no thread: {actor_id}")
                        return actor_id

        return get_hubspot_bot_actor_id()

    except Exception as e:
        logger.error(f"❌ Erro ao buscar agent actor: {str(e)}")
        return None


def send_message_to_thread(
    thread_id: str,
    text: str,
    channel_id: str,
    channel_account_id: str,
    sender_actor_id: str,
    recipients: List[dict]
) -> Optional[dict]:
    """
    Envia uma mensagem para um thread de conversa.

    Args:
        thread_id: ID do thread
        text: Texto da mensagem
        channel_id: ID do canal (1000 = chat, 1001 = Facebook, 1003 = WhatsApp)
        channel_account_id: ID da conta do canal
        sender_actor_id: ID do remetente (agent)
        recipients: Lista de destinatários

    Returns:
        Dados da mensagem enviada ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/conversations/v3/conversations/threads/{thread_id}/messages"

        # Converter quebras de linha para HTML no richText
        text = format_whatsapp(text)
        if not text or message_length(text) > WHATSAPP_MAX_MESSAGE_LENGTH:
            logger.error("Mensagem vazia ou acima do limite; use split_whatsapp antes do envio")
            return None

        payload = {
            "type": "MESSAGE",
            "text": text,
            "richText": whatsapp_rich_text(text),
            "senderActorId": sender_actor_id,
            "channelId": channel_id,
            "channelAccountId": channel_account_id,
            "recipients": recipients
        }

        response = requests.post(url, headers=get_headers(), json=payload, timeout=30)

        if response.status_code in [200, 201]:
            logger.info(f"✅ Mensagem enviada para thread {thread_id}")
            return response.json()
        else:
            logger.error(f"❌ Erro ao enviar mensagem: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"❌ Exceção ao enviar mensagem: {str(e)}")
        return None


def send_image_to_thread(
    thread_id: str,
    image_url: str,
    caption: str,
    channel_id: str,
    channel_account_id: str,
    sender_actor_id: str,
    recipients: List[dict]
) -> Optional[dict]:
    """
    Envia uma imagem para um thread de conversa.

    Args:
        thread_id: ID do thread
        image_url: URL pública da imagem
        caption: Texto/legenda da imagem
        channel_id: ID do canal
        channel_account_id: ID da conta do canal
        sender_actor_id: ID do remetente (agent)
        recipients: Lista de destinatários

    Returns:
        Dados da mensagem enviada ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/conversations/v3/conversations/threads/{thread_id}/messages"

        payload = {
            "type": "MESSAGE",
            "text": caption,
            "richText": f"<p>{caption}</p>",
            "senderActorId": sender_actor_id,
            "channelId": channel_id,
            "channelAccountId": channel_account_id,
            "recipients": recipients,
            "attachments": [
                {
                    "type": "FILE",
                    "url": image_url
                }
            ]
        }

        response = requests.post(url, headers=get_headers(), json=payload, timeout=30)

        if response.status_code in [200, 201]:
            logger.info(f"✅ Imagem enviada para thread {thread_id}")
            return response.json()
        else:
            logger.error(f"❌ Erro ao enviar imagem: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"❌ Exceção ao enviar imagem: {str(e)}")
        return None


def parse_incoming_messages(messages: List[dict]) -> List[dict]:
    """
    Processa mensagens recebidas e extrai informações relevantes.

    Args:
        messages: Lista de mensagens do HubSpot

    Returns:
        Lista de mensagens processadas
    """
    processed = []

    for msg in messages:
        msg_type = msg.get("type", "")
        direction = msg.get("direction", "")
        text = msg.get("text", "")
        created_at = msg.get("createdAt", "")
        msg_id = msg.get("id", "")
        attachments = msg.get("attachments", [])

        # Incluir mensagens com texto OU com attachments (áudio/imagem)
        has_content = text or attachments

        if msg_type in ["MESSAGE", "WELCOME_MESSAGE"] and has_content:
            senders = msg.get("senders", [])
            sender_id = senders[0].get("actorId", "") if senders else ""

            is_visitor = direction == "INCOMING" and sender_id.startswith("V-")

            # Para áudio sem texto, usar placeholder
            display_text = text or ""

            processed.append({
                "id": msg_id,
                "text": display_text,
                "created_at": created_at,
                "direction": direction,
                "is_from_visitor": is_visitor,
                "sender_id": sender_id,
                "type": msg_type,
                "raw": msg
            })

    return processed


def get_last_visitor_message(thread_id: str) -> Optional[dict]:
    """
    Busca a última mensagem enviada pelo visitante em um thread.

    Args:
        thread_id: ID do thread

    Returns:
        Última mensagem do visitante ou None
    """
    messages = get_thread_messages(thread_id)
    processed = parse_incoming_messages(messages)

    visitor_messages = [m for m in processed if m["is_from_visitor"]]

    if visitor_messages:
        return visitor_messages[-1]

    return None


def get_conversation_context(thread_id: str, max_messages: int = 10) -> str:
    """
    Monta o contexto da conversa para o agente.

    Args:
        thread_id: ID do thread
        max_messages: Número máximo de mensagens a incluir

    Returns:
        String com o contexto formatado
    """
    messages = get_thread_messages(thread_id)
    processed = parse_incoming_messages(messages)

    recent = processed[-max_messages:] if len(processed) > max_messages else processed

    context_parts = []
    for msg in recent:
        role = "Visitante" if msg["is_from_visitor"] else "Agente"
        context_parts.append(f"{role}: {msg['text']}")

    return "\n".join(context_parts)


def get_thread_metadata(thread_id: str) -> dict:
    """
    Extrai metadados importantes do thread para envio de resposta.

    Args:
        thread_id: ID do thread

    Returns:
        Dicionário com channel_id, channel_account_id, visitor_actor_id, etc
    """
    messages = get_thread_messages(thread_id, limit=50)

    metadata = {
        "channel_id": None,
        "channel_account_id": None,
        "visitor_actor_id": None,
        "visitor_delivery_identifier": None
    }

    for msg in messages:
        if not metadata["channel_id"]:
            metadata["channel_id"] = msg.get("channelId")
            metadata["channel_account_id"] = msg.get("channelAccountId")

        direction = msg.get("direction", "")
        if direction == "INCOMING" and not metadata["visitor_actor_id"]:
            senders = msg.get("senders", [])
            if senders:
                sender = senders[0]
                actor_id = sender.get("actorId", "")
                if actor_id.startswith("V-"):
                    metadata["visitor_actor_id"] = actor_id
                    delivery_id = sender.get("deliveryIdentifier", {})
                    if delivery_id:
                        metadata["visitor_delivery_identifier"] = delivery_id
                    logger.info(f"👤 Visitante encontrado: {actor_id}")

    if not metadata["visitor_actor_id"]:
        logger.warning(f"⚠️ Visitante não encontrado em {len(messages)} mensagens")

    return metadata


def reply_to_visitor(thread_id: str, response_text: str) -> Optional[dict]:
    """
    Envia uma resposta para o visitante em um thread.
    USA O ACTOR ID DO SALOMÃO (A-81908844) como remetente.

    Args:
        thread_id: ID do thread
        response_text: Texto da resposta

    Returns:
        Dados da mensagem enviada ou None
    """
    metadata = get_thread_metadata(thread_id)

    if not metadata["channel_id"] or not metadata["channel_account_id"]:
        logger.error("❌ Não foi possível obter metadados do canal")
        return None

    if not metadata["visitor_actor_id"]:
        logger.error("❌ Não foi possível identificar o visitante")
        return None

    recipients = [{
        "actorId": metadata["visitor_actor_id"]
    }]

    if metadata["visitor_delivery_identifier"]:
        recipients[0]["deliveryIdentifier"] = metadata["visitor_delivery_identifier"]

    # SEMPRE usa o actor do Salomão
    return send_message_to_thread(
        thread_id=thread_id,
        text=response_text,
        channel_id=metadata["channel_id"],
        channel_account_id=metadata["channel_account_id"],
        sender_actor_id=SALOMAO_ACTOR_ID,
        recipients=recipients
    )


def get_ticket_owner(ticket_id: str) -> Optional[str]:
    """
    Obtém o proprietário de um ticket.

    Args:
        ticket_id: ID do ticket

    Returns:
        ID do proprietário ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}"
        params = {"properties": "hubspot_owner_id"}
        response = requests.get(url, headers=get_headers(), params=params, timeout=30)

        if response.status_code == 200:
            props = response.json().get("properties", {})
            return props.get("hubspot_owner_id")
        return None
    except Exception as e:
        logger.error(f"❌ Erro ao obter proprietário: {str(e)}")
        return None


def update_ticket_owner(ticket_id: str, owner_id: str = None) -> bool:
    """
    Atualiza o proprietário de um ticket.
    Se owner_id for None, remove o proprietário.

    Args:
        ticket_id: ID do ticket
        owner_id: ID do proprietário (None para remover)

    Returns:
        True se sucesso, False caso contrário
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}"
        payload = {
            "properties": {
                "hubspot_owner_id": owner_id or ""
            }
        }
        response = requests.patch(url, headers=get_headers(), json=payload, timeout=30)

        if response.status_code == 200:
            logger.info(f"✅ Proprietário do ticket {ticket_id} atualizado")
            return True
        else:
            logger.error(f"❌ Erro ao atualizar proprietário: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Exceção ao atualizar proprietário: {str(e)}")
        return False


def update_ticket_pipeline_status(ticket_id: str, pipeline_id: str, stage_id: str) -> bool:
    """
    Atualiza pipeline e status de um ticket.

    Args:
        ticket_id: ID do ticket
        pipeline_id: ID da pipeline
        stage_id: ID do estágio

    Returns:
        True se sucesso, False caso contrário
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}"
        payload = {
            "properties": {
                "hs_pipeline": pipeline_id,
                "hs_pipeline_stage": stage_id
            }
        }
        response = requests.patch(url, headers=get_headers(), json=payload, timeout=30)

        if response.status_code == 200:
            logger.info(f"✅ Ticket {ticket_id} movido para pipeline {pipeline_id} / status {stage_id}")
            return True
        else:
            logger.error(f"❌ Erro ao mover ticket: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Exceção ao mover ticket: {str(e)}")
        return False


def transfer_to_human(ticket_id: str) -> bool:
    """
    Transfere um ticket para atendimento humano.
    Remove o proprietário e move para pipeline/status de humano.

    Args:
        ticket_id: ID do ticket

    Returns:
        True se sucesso, False caso contrário
    """
    logger.info(f"🔄 Transferindo ticket {ticket_id} para humano...")

    # Remove o proprietário
    owner_cleared = update_ticket_owner(ticket_id, None)

    # Move para pipeline de humano
    moved = update_ticket_pipeline_status(ticket_id, HUMAN_PIPELINE, HUMAN_STATUS)

    if owner_cleared and moved:
        logger.info(f"✅ Ticket {ticket_id} transferido para humano com sucesso")
        return True
    else:
        logger.error(f"❌ Falha ao transferir ticket {ticket_id} para humano")
        return False


def get_tickets_for_salomao() -> List[dict]:
    """
    Busca tickets que o Salomão deve processar.
    Filtros: Pipeline do Salomão + Status do Salomão + Proprietário do Salomão (81908844)

    Returns:
        Lista de tickets
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/search"

        # Extrai o ID numérico do actor (A-81908844 -> 81908844)
        owner_id = SALOMAO_ACTOR_ID.replace("A-", "")

        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "hs_pipeline",
                            "operator": "EQ",
                            "value": SALOMAO_PIPELINE
                        },
                        {
                            "propertyName": "hs_pipeline_stage",
                            "operator": "EQ",
                            "value": SALOMAO_STATUS
                        },
                        {
                            "propertyName": "hubspot_owner_id",
                            "operator": "EQ",
                            "value": owner_id
                        }
                    ]
                }
            ],
            "properties": [
                "subject",
                "content",
                "hs_pipeline",
                "hs_pipeline_stage",
                "hubspot_owner_id",
                "createdate",
                "hs_lastmodifieddate"
            ],
            "limit": 100
        }

        response = requests.post(url, headers=get_headers(), json=payload, timeout=30)

        if response.status_code == 200:
            data = response.json()
            tickets = data.get("results", [])
            logger.info(f"✅ Encontrados {len(tickets)} tickets para o Salomão")
            return tickets
        else:
            logger.error(f"❌ Erro ao buscar tickets: {response.status_code}")
            return []

    except Exception as e:
        logger.error(f"❌ Exceção ao buscar tickets: {str(e)}")
        return []


def get_contact_info(contact_id: str) -> Optional[dict]:
    """
    Busca informações de um contato pelo ID.

    Args:
        contact_id: ID do contato

    Returns:
        Informações do contato ou None
    """
    try:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{contact_id}"
        params = {
            "properties": "firstname,lastname,email,phone,company"
        }
        response = requests.get(url, headers=get_headers(), params=params, timeout=30)

        if response.status_code == 200:
            data = response.json()
            props = data.get("properties", {})
            return {
                "id": data.get("id"),
                "firstname": props.get("firstname", ""),
                "lastname": props.get("lastname", ""),
                "email": props.get("email", ""),
                "phone": props.get("phone", ""),
                "company": props.get("company", "")
            }
        return None
    except Exception as e:
        logger.error(f"❌ Erro ao buscar contato: {str(e)}")
        return None


def get_ticket_contact(ticket_id: str) -> Optional[dict]:
    """
    Busca o contato associado a um ticket.

    Args:
        ticket_id: ID do ticket

    Returns:
        Informações do contato ou None
    """
    try:
        # Busca associações do ticket
        url = f"{HUBSPOT_API_BASE}/crm/v4/objects/tickets/{ticket_id}/associations/contacts"
        response = requests.get(url, headers=get_headers(), timeout=30)

        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                contact_id = results[0].get("toObjectId")
                if contact_id:
                    return get_contact_info(str(contact_id))
        return None
    except Exception as e:
        logger.error(f"❌ Erro ao buscar contato do ticket: {str(e)}")
        return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO)

    print("="*60)
    print("TESTE: Buscando tickets na pipeline alvo")
    print("="*60)

    tickets = get_tickets_in_pipeline()
    for ticket in tickets[:5]:
        print(f"Ticket ID: {ticket.get('id')} - {ticket.get('properties', {}).get('subject', 'Sem assunto')}")

    print("\n" + "="*60)
    print("TESTE: Buscando conversa do ticket 39006138916")
    print("="*60)

    thread = get_conversation_thread_by_ticket("39006138916")
    if thread:
        print(f"Thread ID: {thread.get('id')}")

        messages = get_thread_messages(thread.get('id'))
        processed = parse_incoming_messages(messages)

        print(f"\nÚltimas mensagens:")
        for msg in processed[-5:]:
            role = "👤 Visitante" if msg["is_from_visitor"] else "🤖 Agente"
            print(f"{role}: {msg['text'][:100]}...")
