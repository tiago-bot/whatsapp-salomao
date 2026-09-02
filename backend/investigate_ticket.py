"""
Script para investigar ticket 39171243852 e entender motivo da transferência.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from hubspot_service import (
    get_ticket_by_id,
    get_conversation_thread_by_ticket,
    get_thread_messages,
    parse_incoming_messages,
    get_conversation_context
)

def investigate_ticket(ticket_id: str):
    print("=" * 60)
    print(f"🔍 INVESTIGANDO TICKET: {ticket_id}")
    print("=" * 60)

    # 1. Buscar dados do ticket
    print("\n1️⃣ Buscando dados do ticket...")
    ticket = get_ticket_by_id(ticket_id)
    if ticket:
        props = ticket.get("properties", {})
        print(f"   Subject: {props.get('subject', 'N/A')}")
        print(f"   Pipeline: {props.get('hs_pipeline', 'N/A')}")
        print(f"   Stage: {props.get('hs_pipeline_stage', 'N/A')}")
        print(f"   Created: {props.get('createdate', 'N/A')}")
    else:
        print("   ❌ Ticket não encontrado")
        return

    # 2. Buscar thread associado
    print("\n2️⃣ Buscando thread da conversa...")
    thread = get_conversation_thread_by_ticket(ticket_id)
    if not thread:
        print("   ❌ Thread não encontrado")
        return

    thread_id = thread.get("id")
    print(f"   Thread ID: {thread_id}")

    # 3. Buscar mensagens
    print("\n3️⃣ Buscando mensagens...")
    messages = get_thread_messages(thread_id, limit=50)
    processed = parse_incoming_messages(messages)

    print(f"   Total de mensagens: {len(processed)}")

    # 4. Mostrar conversa completa
    print("\n4️⃣ CONVERSA COMPLETA:")
    print("-" * 60)

    for msg in processed:
        role = "👤 CLIENTE" if msg["is_from_visitor"] else "🤖 SALOMÃO"
        text = msg.get("text", "")[:500]
        time = msg.get("created_at", "")[:19]

        print(f"\n{role} ({time}):")
        print(f"   {text}")

        # Verificar se tem TRANSFERIR_SUPORTE
        if "TRANSFERIR_SUPORTE" in text or "transferir" in text.lower():
            print("   ⚠️ >>> TRANSFERÊNCIA DETECTADA <<<")

    # 5. Análise
    print("\n" + "=" * 60)
    print("📊 ANÁLISE DA TRANSFERÊNCIA:")
    print("=" * 60)

    # Buscar última mensagem do cliente antes da transferência
    visitor_messages = [m for m in processed if m["is_from_visitor"]]
    bot_messages = [m for m in processed if not m["is_from_visitor"]]

    print(f"\n   Mensagens do cliente: {len(visitor_messages)}")
    print(f"   Mensagens do bot: {len(bot_messages)}")

    if visitor_messages:
        last_visitor = visitor_messages[-1]
        print(f"\n   Última mensagem do cliente:")
        print(f"   '{last_visitor.get('text', '')[:200]}'")

    # Verificar padrões de transferência
    transfer_keywords = ['humano', 'atendente', 'suporte', 'pessoa', 'falar com alguém']

    for msg in visitor_messages:
        text_lower = msg.get("text", "").lower()
        for kw in transfer_keywords:
            if kw in text_lower:
                print(f"\n   ⚠️ Cliente pediu atendente: '{kw}' encontrado em mensagem")
                print(f"   Mensagem: '{msg.get('text', '')[:100]}'")


if __name__ == "__main__":
    ticket_id = sys.argv[1] if len(sys.argv) > 1 else "39171243852"
    investigate_ticket(ticket_id)
