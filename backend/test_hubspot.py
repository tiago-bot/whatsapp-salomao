"""
Script de teste para integração HubSpot Salomão.
Testa todas as funcionalidades da integração.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)

def print_header(title: str):
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)


def print_result(label: str, value, success: bool = True):
    icon = "✅" if success else "❌"
    print(f"{icon} {label}: {value}")


def test_env_variables():
    """Testa se as variáveis de ambiente estão configuradas."""
    print_header("TESTE 1: Variáveis de Ambiente")

    required_vars = [
        "OPENAI_API_KEY",
        "PINECONE_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "HUBSPOT_ACCESS_TOKEN"
    ]

    all_set = True
    for var in required_vars:
        value = os.getenv(var)
        if value and value != "your_hubspot_access_token_here":
            masked = value[:10] + "..." if len(value) > 10 else value
            print_result(var, masked, True)
        else:
            print_result(var, "NÃO CONFIGURADO", False)
            all_set = False

    return all_set


def test_hubspot_connection():
    """Testa conexão com a API do HubSpot."""
    print_header("TESTE 2: Conexão HubSpot API")

    from hubspot_service import get_tickets_in_pipeline, HUBSPOT_ACCESS_TOKEN

    if not HUBSPOT_ACCESS_TOKEN or HUBSPOT_ACCESS_TOKEN == "your_hubspot_access_token_here":
        print_result("HubSpot Token", "Token não configurado", False)
        return False

    try:
        tickets = get_tickets_in_pipeline()
        print_result("Conexão HubSpot", "OK", True)
        print_result("Tickets encontrados", len(tickets), True)
        return True
    except Exception as e:
        print_result("Conexão HubSpot", str(e), False)
        return False


def test_specific_ticket(ticket_id: str = "39006138916"):
    """Testa busca de um ticket específico."""
    print_header(f"TESTE 3: Ticket Específico ({ticket_id})")

    from hubspot_service import (
        get_ticket_by_id,
        get_conversation_thread_by_ticket,
        get_thread_messages,
        parse_incoming_messages
    )

    ticket = get_ticket_by_id(ticket_id)
    if ticket:
        print_result("Ticket encontrado", ticket.get("id"), True)
        subject = ticket.get("properties", {}).get("subject", "N/A")
        print_result("Assunto", subject[:50] if subject else "N/A", True)
    else:
        print_result("Ticket", "Não encontrado", False)
        return None

    thread = get_conversation_thread_by_ticket(ticket_id)
    if thread:
        thread_id = thread.get("id")
        print_result("Thread ID", thread_id, True)
        print_result("Status", thread.get("status", "N/A"), True)

        messages = get_thread_messages(thread_id)
        processed = parse_incoming_messages(messages)
        print_result("Total de mensagens", len(processed), True)

        if processed:
            print("\n  📨 Últimas 5 mensagens:")
            for msg in processed[-5:]:
                role = "👤 Visitante" if msg["is_from_visitor"] else "🤖 Sistema"
                text = msg["text"][:80] + "..." if len(msg["text"]) > 80 else msg["text"]
                print(f"     {role}: {text}")

        return thread_id
    else:
        print_result("Thread", "Não encontrado", False)
        return None


def test_salomao_response(message: str = "Olá, como faço para criar um cupom de desconto?"):
    """Testa resposta do agente Salomão."""
    print_header("TESTE 4: Agente Salomão")

    from salomao_agent import salomao

    session_id = "test_integration_session"

    print(f"  📝 Mensagem de teste: {message}")

    try:
        result = salomao.process_message(
            message=message,
            session_id=session_id
        )

        if result.get("success"):
            response = result.get("response", "")
            print_result("Processamento", "OK", True)
            print_result("Tokens usados", result.get("tokens", {}).get("total", 0), True)
            print(f"\n  💬 Resposta ({len(response)} chars):")
            print(f"     {response[:300]}...")

            salomao.clear_conversation(session_id)
            return True
        else:
            print_result("Processamento", result.get("error", "Erro desconhecido"), False)
            return False

    except Exception as e:
        print_result("Salomão", str(e), False)
        return False


def test_full_flow(ticket_id: str = "39006138916"):
    """Testa o fluxo completo de processamento."""
    print_header("TESTE 5: Fluxo Completo")

    from hubspot_bot import hubspot_bot, process_single_ticket

    print(f"  🎫 Processando ticket: {ticket_id}")

    try:
        result = process_single_ticket(ticket_id)

        if result.get("success"):
            print_result("Ticket processado", result.get("ticket_id"), True)
            print_result("Thread ID", result.get("thread_id"), True)

            responses = result.get("responses", [])
            print_result("Respostas geradas", len(responses), True)

            for i, resp in enumerate(responses):
                print(f"\n  📩 Resposta {i+1}:")
                print(f"     Usuário: {resp.get('user_message', '')[:50]}...")
                print(f"     Bot: {resp.get('bot_response', '')[:100]}...")
                print(f"     Enviada: {'✅' if resp.get('sent') else '❌'}")

            return True
        else:
            print_result("Processamento", result.get("error", "Erro"), False)
            return False

    except Exception as e:
        print_result("Fluxo completo", str(e), False)
        return False


def test_supabase_session():
    """Testa persistência de sessão no Supabase."""
    print_header("TESTE 6: Supabase Sessions")

    from database import db

    test_session_id = "test_supabase_session_12345"

    try:
        session = db.get_or_create_session(test_session_id)
        print_result("Criar sessão", session.get("session_id"), True)

        msg = db.add_message(
            session_id=test_session_id,
            role="user",
            content="Teste de mensagem"
        )
        print_result("Adicionar mensagem", msg.get("id", "OK"), True)

        history = db.get_conversation_history(test_session_id)
        print_result("Buscar histórico", f"{len(history)} mensagens", True)

        db.clear_session(test_session_id)
        print_result("Limpar sessão", "OK", True)

        return True

    except Exception as e:
        print_result("Supabase", str(e), False)
        return False


def run_all_tests():
    """Executa todos os testes."""
    print("\n" + "="*70)
    print("  🧪 SUITE DE TESTES - SALOMÃO HUBSPOT INTEGRATION")
    print("="*70)

    results = {}

    results["env"] = test_env_variables()

    results["supabase"] = test_supabase_session()

    results["salomao"] = test_salomao_response()

    if os.getenv("HUBSPOT_ACCESS_TOKEN") and os.getenv("HUBSPOT_ACCESS_TOKEN") != "your_hubspot_access_token_here":
        results["hubspot"] = test_hubspot_connection()

        if results["hubspot"]:
            thread_id = test_specific_ticket()
            results["ticket"] = thread_id is not None

            if results["ticket"]:
                results["full_flow"] = test_full_flow()
    else:
        print_header("TESTES HUBSPOT PULADOS")
        print("  ⚠️ Configure HUBSPOT_ACCESS_TOKEN no .env para testar integração")
        results["hubspot"] = None
        results["ticket"] = None
        results["full_flow"] = None

    print_header("RESUMO DOS TESTES")

    passed = 0
    failed = 0
    skipped = 0

    for test_name, result in results.items():
        if result is None:
            status = "⏭️ PULADO"
            skipped += 1
        elif result:
            status = "✅ PASSOU"
            passed += 1
        else:
            status = "❌ FALHOU"
            failed += 1
        print(f"  {test_name.upper()}: {status}")

    print("\n" + "-"*40)
    print(f"  Total: {passed} passou | {failed} falhou | {skipped} pulado")
    print("="*70 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
