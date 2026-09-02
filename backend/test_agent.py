import requests
import json
import uuid

API_URL = "http://localhost:8000"

def test_chat(message: str, session_id: str = "test"):
    """Testa o endpoint de chat."""
    print(f"\n{'='*60}")
    print(f"PERGUNTA: {message}")
    print(f"{'='*60}")

    response = requests.post(
        f"{API_URL}/chat",
        json={
            "message": message,
            "session_id": session_id
        }
    )

    data = response.json()

    if data.get("success"):
        print(f"\nRESPOSTA DO SALOMÃO:")
        print(data["response"])
        tokens = data.get("tokens", {})
        print(f"\n📊 TOKENS: prompt={tokens.get('prompt', 0)} | completion={tokens.get('completion', 0)} | TOTAL={tokens.get('total', 0)}")
        print(f"🤖 Modelo: {data.get('model_used')}")
    else:
        print(f"\nERRO: {data.get('error')}")

    return data

def test_saudacao_repetida():
    """Testa se o agente NÃO repete saudações no meio da conversa."""
    print("\n" + "="*60)
    print("TESTE: Saudação NÃO deve repetir no meio da conversa")
    print("="*60)

    session_id = f"test-saudacao-{uuid.uuid4().hex[:8]}"

    print("\n1. Primeira mensagem (deve saudar):")
    r1 = test_chat("Olá!", session_id)

    print("\n2. Pergunta sobre inChurch:")
    r2 = test_chat("Como criar cupom de desconto?", session_id)

    print("\n3. Pergunta fora do escopo (NÃO deve repetir saudação):")
    r3 = test_chat("Quem ganhou o brasileiro de 87?", session_id)

    if "Eu sou o Salomão" in r3.get("response", ""):
        print("\n❌ FALHA: Agente repetiu a saudação!")
    else:
        print("\n✅ SUCESSO: Agente NÃO repetiu a saudação!")

    return r3

def main():
    print("\n" + "="*60)
    print("TESTANDO AGENTE SALOMÃO - MELHORIAS")
    print("="*60)

    test_saudacao_repetida()

    print("\n" + "="*60)
    print("TESTES CONCLUÍDOS!")
    print("="*60)

if __name__ == "__main__":
    main()
