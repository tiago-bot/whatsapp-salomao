# Documentação Completa do Salomão - Assistente Inteligente inChurch

## Índice

1. [Visão Geral](#visão-geral)
2. [Arquitetura do Sistema](#arquitetura-do-sistema)
3. [Estrutura do Projeto](#estrutura-do-projeto)
4. [Configurações e Variáveis de Ambiente](#configurações-e-variáveis-de-ambiente)
5. [Componentes Principais](#componentes-principais)
   - [Agente Salomão (salomao_agent.py)](#agente-salomão-salomao_agentpy)
   - [Base de Conhecimento (knowledge_base.py)](#base-de-conhecimento-knowledge_basepy)
   - [API HubSpot (main_hubspot.py)](#api-hubspot-main_hubspotpy)
   - [Serviço HubSpot (hubspot_service.py)](#serviço-hubspot-hubspot_servicepy)
   - [Bot HubSpot (hubspot_bot.py)](#bot-hubspot-hubspot_botpy)
   - [Banco de Dados HubSpot (hubspot_database.py)](#banco-de-dados-hubspot-hubspot_databasepy)
   - [Serviço de Eventos (event_service.py)](#serviço-de-eventos-event_servicepy)
   - [Serviço de Usuários (user_service.py)](#serviço-de-usuários-user_servicepy)
6. [Fluxos de Funcionamento](#fluxos-de-funcionamento)
   - [Fluxo de Webhook](#fluxo-de-webhook)
   - [Fluxo de Polling](#fluxo-de-polling)
   - [Fluxo de Processamento de Mensagens](#fluxo-de-processamento-de-mensagens)
   - [Fluxo de Transferência para Humano](#fluxo-de-transferência-para-humano)
7. [Exemplos de Uso](#exemplos-de-uso)
8. [Troubleshooting](#troubleshooting)
9. [Apêndices](#apêndices)

## Visão Geral

O Salomão é um assistente virtual inteligente desenvolvido para a plataforma inChurch, especializado em fornecer suporte técnico e informações sobre todos os módulos da plataforma. O sistema utiliza inteligência artificial avançada (OpenAI GPT-4) combinada com uma base de conhecimento vetorial (Pinecone) para oferecer respostas precisas e contextuais.

O projeto é composto por dois componentes principais:
1. **Agente de IA Salomão**: Responsável pelo processamento de linguagem natural e geração de respostas
2. **Integração com HubSpot**: Permite que o Salomão interaja com clientes via WhatsApp através da plataforma HubSpot

### Principais Funcionalidades

- 💬 **Chat inteligente** com memória de conversa
- 📚 **Base de conhecimento** integrada com Pinecone
- 🖼️ **Análise de imagens** (capturas de tela, prints)
- 🎤 **Transcrição de áudio** via Whisper
- 🧠 **Memória automática** para personalização
- 🔄 **Contexto conversacional** mantido entre mensagens
- 🎫 **Diagnóstico de eventos** em tempo real
- 👤 **Verificação de contas de usuários**
- 🔄 **Integração com HubSpot** para atendimento via WhatsApp

## Arquitetura do Sistema

O Salomão utiliza uma arquitetura modular baseada em serviços, onde cada componente tem uma responsabilidade específica:

```
┌─────────────────┐     ┌───────────────┐     ┌────────────────┐
│  HubSpot API    │◄────┤  Salomão API  │◄────┤  Salomão Agent │
│  (Webhooks)     │     │  (FastAPI)    │     │  (GPT-4)       │
└────────┬────────┘     └───────┬───────┘     └────────┬───────┘
         │                      │                      │
         ▼                      ▼                      ▼
┌─────────────────┐     ┌───────────────┐     ┌────────────────┐
│  HubSpot        │     │  Supabase     │     │  Pinecone      │
│  Conversations  │     │  (Sessions)   │     │  (Knowledge)   │
└─────────────────┘     └───────────────┘     └────────────────┘
```

### Fluxo de Dados

1. **Entrada**: Mensagens recebidas via webhook do HubSpot ou polling de tickets
2. **Processamento**: O agente Salomão processa as mensagens usando GPT-4 e a base de conhecimento
3. **Persistência**: As sessões e históricos são armazenados no Supabase
4. **Saída**: Respostas enviadas de volta ao HubSpot para entrega ao usuário via WhatsApp

## Estrutura do Projeto

```
backend/
├── .env                    # Variáveis de ambiente
├── config.py               # Configurações centralizadas
├── database.py             # Interface com Supabase (genérica)
├── event_service.py        # Serviço para diagnóstico de eventos
├── hubspot_bot.py          # Bot para integração com HubSpot
├── hubspot_database.py     # Gerenciamento de sessões no Supabase
├── hubspot_service.py      # Serviços de integração com HubSpot API
├── knowledge_base.py       # Integração com Pinecone (base de conhecimento)
├── main.py                 # API principal (FastAPI)
├── main_hubspot.py         # API específica para HubSpot (webhook/polling)
├── polling_service.py      # Serviço de polling para HubSpot
├── salomao_agent.py        # Agente principal de IA (GPT-4)
├── test_agent.py           # Testes para o agente
├── test_hubspot.py         # Testes para integração HubSpot
└── user_service.py         # Serviço para diagnóstico de usuários
```

## Configurações e Variáveis de Ambiente

O sistema utiliza um arquivo `.env` para configuração. Abaixo estão as principais variáveis de ambiente necessárias:

```
# OpenAI
OPENAI_API_KEY=sk-...

# Pinecone (Base de Conhecimento)
PINECONE_API_KEY=...
PINECONE_HOST=...
PINECONE_INDEX_NAME=inchurch-hubspot-kb

# HubSpot
HUBSPOT_ACCESS_TOKEN=...
HUBSPOT_GRAPHQL_API=...

# Pipeline e Status do Salomão
HUBSPOT_SALOMAO_PIPELINE=636594474
HUBSPOT_SALOMAO_STATUS=1269308450

# Pipeline e Status para transferir para humano
HUBSPOT_HUMAN_PIPELINE=636459134
HUBSPOT_HUMAN_STATUS=939275049

# Actor ID do Salomão (usuário que envia mensagens)
HUBSPOT_SALOMAO_ACTOR_ID=A-81908844

# Supabase
SUPABASE_URL=https://...
SUPABASE_KEY=...
```

## Componentes Principais

### Agente Salomão (salomao_agent.py)

O coração do sistema é o `SalomaoAgent`, responsável por processar mensagens usando o GPT-4 e gerar respostas contextuais.

#### Principais Classes e Funções

- `SalomaoAgent`: Classe principal que gerencia a interação com o modelo GPT-4
  - `process_message()`: Processa uma mensagem e gera uma resposta
  - `get_conversation_history()`: Obtém o histórico de uma conversa
  - `clear_conversation()`: Limpa o histórico de uma conversa

- `get_system_prompt()`: Gera o prompt do sistema baseado no contexto da conversa

#### Exemplo de Uso

```python
from salomao_agent import salomao

# Processar uma mensagem
result = salomao.process_message(
    message="Como criar um cupom de desconto?",
    session_id="session_123"
)

# Obter resposta
response = result.get("response")
print(response)
```

### Base de Conhecimento (knowledge_base.py)

Gerencia a integração com o Pinecone para armazenar e recuperar informações da base de conhecimento.

#### Principais Classes e Funções

- `KnowledgeBase`: Classe que gerencia a base de conhecimento vetorial
  - `query()`: Busca informações relevantes na base de conhecimento
  - `get_relevant_context()`: Obtém contexto relevante para uma pergunta

#### Exemplo de Uso

```python
from knowledge_base import knowledge_base

# Buscar informações relevantes
results = knowledge_base.query(
    query_text="Como configurar um evento?",
    top_k=3
)

# Exibir resultados
for result in results:
    print(f"Score: {result['score']}")
    print(f"Conteúdo: {result['text']}")
```

### API HubSpot (main_hubspot.py)

Implementa a API FastAPI para receber webhooks do HubSpot e gerenciar o polling de tickets.

#### Principais Endpoints

- `POST /webhook/hubspot`: Recebe eventos de webhook do HubSpot
- `GET /ticket/{ticket_id}/thread`: Obtém o thread de conversa de um ticket
- `POST /ticket/{ticket_id}/transfer-to-human`: Transfere um ticket para atendimento humano
- `GET /config`: Retorna a configuração atual do Salomão

#### Funções Principais

- `polling_loop()`: Loop assíncrono que verifica tickets a cada intervalo definido
- `create_session_for_conversation()`: Cria uma sessão no Supabase para uma nova conversa

#### Exemplo de Fluxo de Webhook

```
1. HubSpot envia evento conversation.creation para /webhook/hubspot
2. API recebe o evento e extrai o thread_id
3. create_session_for_conversation() é chamada em background
4. Sessão é criada no Supabase com dados do contato
5. API retorna status 200 para o HubSpot
```

### Serviço HubSpot (hubspot_service.py)

Fornece funções para interagir com a API do HubSpot, incluindo busca de tickets, threads e envio de mensagens.

#### Principais Funções

- `get_tickets_for_salomao()`: Busca tickets com os filtros do Salomão (pipeline, status, proprietário)
- `get_thread_messages()`: Obtém mensagens de um thread
- `reply_to_visitor()`: Envia uma resposta para um visitante
- `transfer_to_human()`: Transfere um ticket para atendimento humano
- `parse_incoming_messages()`: Processa mensagens recebidas do HubSpot

#### Exemplo de Envio de Mensagem

```python
from hubspot_service import reply_to_visitor

# Enviar resposta para um visitante
success = reply_to_visitor(
    thread_id="123456",
    text="Olá! Como posso ajudar?"
)

if success:
    print("Mensagem enviada com sucesso!")
else:
    print("Erro ao enviar mensagem")
```

### Bot HubSpot (hubspot_bot.py)

Implementa a lógica de processamento de tickets e threads do HubSpot, conectando o agente Salomão com o HubSpot.

#### Principais Classes e Funções

- `HubSpotSalomaoBot`: Classe principal que gerencia a integração
  - `process_message()`: Processa uma mensagem do HubSpot
  - `process_thread()`: Processa um thread completo
  - `was_message_processed()`: Verifica se uma mensagem já foi processada

- `process_single_ticket()`: Processa um ticket específico

#### Exemplo de Processamento de Ticket

```python
from hubspot_bot import process_single_ticket

# Processar um ticket
result = process_single_ticket("ticket_123")

# Verificar resultados
if result.get("responses"):
    print(f"Enviadas {len(result['responses'])} respostas")
else:
    print("Nenhuma resposta enviada")
```

### Banco de Dados HubSpot (hubspot_database.py)

Gerencia a persistência de sessões e mapeamento de threads do HubSpot para sessões do Salomão no Supabase.

#### Principais Classes e Funções

- `HubSpotDatabase`: Classe principal para gerenciamento de sessões
  - `get_or_create_session()`: Obtém ou cria uma sessão para um thread
  - `update_session()`: Atualiza uma sessão existente
  - `get_active_sessions()`: Obtém sessões ativas

#### Exemplo de Uso

```python
from hubspot_database import hubspot_db

# Obter ou criar uma sessão
session = hubspot_db.get_or_create_session(
    thread_id="thread_123",
    ticket_id="ticket_456",
    visitor_name="João Silva",
    visitor_email="joao@exemplo.com"
)

# Atualizar contagem de mensagens
hubspot_db.update_session(
    thread_id="thread_123",
    message_count=5,
    last_message_time="2023-01-01T12:00:00Z"
)
```

### Serviço de Eventos (event_service.py)

Fornece funções para diagnóstico de eventos da plataforma inChurch.

#### Principais Funções

- `fetch_event_details()`: Busca detalhes de um evento pelo ID
- `analyze_event_visibility()`: Analisa problemas de visibilidade de um evento
- `check_event_and_respond()`: Verifica um evento e gera uma resposta

### Serviço de Usuários (user_service.py)

Fornece funções para diagnóstico de contas de usuários da plataforma inChurch.

#### Principais Funções

- `fetch_user_by_email()`: Busca dados de um usuário pelo email
- `analyze_user_issues()`: Analisa problemas na conta de um usuário
- `format_user_analysis_response()`: Formata a resposta da análise de usuário

## Fluxos de Funcionamento

### Fluxo de Webhook

O sistema utiliza webhooks do HubSpot apenas para detectar novas conversas:

```
┌─────────────┐     ┌───────────────┐     ┌─────────────────┐
│  HubSpot    │────►│ /webhook/     │────►│ create_session_ │
│  Webhook    │     │ hubspot       │     │ for_conversation│
└─────────────┘     └───────────────┘     └────────┬────────┘
                                                   │
                                                   ▼
                                          ┌─────────────────┐
                                          │   Supabase      │
                                          │   (Sessions)    │
                                          └─────────────────┘
```

1. HubSpot envia evento `conversation.creation` para o endpoint `/webhook/hubspot`
2. O sistema extrai o `thread_id` do evento
3. A função `create_session_for_conversation()` é chamada em background
4. A função busca informações do thread e do contato associado
5. Uma nova sessão é criada no Supabase com os dados do contato
6. O sistema retorna status 200 para o HubSpot imediatamente (para evitar timeouts)

### Fluxo de Polling

O sistema utiliza polling para processar mensagens em tickets ativos:

```
┌─────────────┐     ┌───────────────┐     ┌─────────────────┐
│  Polling    │────►│ get_tickets_  │────►│ process_single_ │
│  Loop       │     │ for_salomao   │     │ ticket          │
└─────┬───────┘     └───────────────┘     └────────┬────────┘
      │                                            │
      │                                            ▼
      │                                   ┌─────────────────┐
      └───────────────────────────────────┤  Sleep 10s      │
                                          └─────────────────┘
```

1. O `polling_loop()` é iniciado quando a aplicação é iniciada
2. A cada 10 segundos, o loop chama `get_tickets_for_salomao()`
3. A função busca tickets com os 3 filtros:
   - Pipeline do Salomão (`SALOMAO_PIPELINE`)
   - Status do Salomão (`SALOMAO_STATUS`)
   - Proprietário = Salomão (`SALOMAO_ACTOR_ID`)
4. Para cada ticket encontrado, `process_single_ticket()` é chamada
5. O loop aguarda 10 segundos e repete o processo

### Fluxo de Processamento de Mensagens

O processamento de mensagens ocorre dentro da função `process_single_ticket()`:

```
┌─────────────┐     ┌───────────────┐     ┌─────────────────┐
│  process_   │────►│ get_thread_   │────►│ parse_incoming_ │
│  single_    │     │ messages      │     │ messages        │
│  ticket     │     └───────────────┘     └────────┬────────┘
└─────────────┘                                    │
                                                   ▼
┌─────────────┐     ┌───────────────┐     ┌─────────────────┐
│  reply_to_  │◄────┤ salomao.      │◄────┤ hubspot_bot.    │
│  visitor    │     │ process_      │     │ process_message │
└─────────────┘     │ message       │     └─────────────────┘
                    └───────────────┘
```

1. `process_single_ticket()` obtém o thread associado ao ticket
2. `get_thread_messages()` busca as mensagens do thread
3. `parse_incoming_messages()` processa as mensagens para identificar mensagens do visitante
4. Para cada mensagem não processada, `hubspot_bot.process_message()` é chamada
5. O bot extrai texto, imagens ou áudio da mensagem
6. `salomao.process_message()` gera uma resposta usando o GPT-4
7. `reply_to_visitor()` envia a resposta de volta ao thread do HubSpot

### Fluxo de Transferência para Humano

Quando um usuário solicita falar com um atendente humano:

```
┌─────────────┐     ┌───────────────┐     ┌─────────────────┐
│  salomao.   │────►│ hubspot_bot.  │────►│ transfer_to_    │
│  process_   │     │ _pending_     │     │ human           │
│  message    │     │ transfer      │     └────────┬────────┘
└─────────────┘     └───────────────┘              │
                                                   ▼
                                          ┌─────────────────┐
                                          │ update_ticket_  │
                                          │ pipeline_status │
                                          └────────┬────────┘
                                                   │
                                                   ▼
                                          ┌─────────────────┐
                                          │ update_ticket_  │
                                          │ owner (null)    │
                                          └─────────────────┘
```

1. `salomao.process_message()` detecta uma solicitação de transferência
2. O bot define `_pending_transfer_ticket_id` com o ID do ticket
3. Após enviar a resposta, `transfer_to_human()` é chamada
4. A função remove o proprietário do ticket
5. A função move o ticket para a pipeline e status de humano
6. O ticket fica disponível para atendentes humanos na fila do HubSpot

## Exemplos de Uso

### Exemplo 1: Processamento de Mensagem

```python
# Importar componentes necessários
from salomao_agent import salomao
from hubspot_service import reply_to_visitor

# Processar uma mensagem
result = salomao.process_message(
    message="Como criar um cupom de desconto?",
    session_id="session_123"
)

# Enviar resposta
if result.get("response"):
    reply_to_visitor("thread_456", result["response"])
```

### Exemplo 2: Transferência para Humano

```python
# Importar componentes necessários
from hubspot_service import transfer_to_human

# Transferir ticket para atendimento humano
success = transfer_to_human("ticket_123")

if success:
    print("Ticket transferido para atendimento humano")
else:
    print("Erro ao transferir ticket")
```

### Exemplo 3: Diagnóstico de Evento

```python
# Importar componentes necessários
from event_service import fetch_event_details, analyze_event_visibility

# Buscar detalhes do evento
event_data = fetch_event_details("1016252")

if event_data:
    # Analisar visibilidade
    analysis = analyze_event_visibility(event_data)

    # Verificar problemas
    if not event_data.get("is_active"):
        print("Evento está desativado")

    if event_data.get("published_for") == "Igreja Local":
        print("Evento publicado apenas para uma igreja específica")
```

## Troubleshooting

### Problemas Comuns e Soluções

1. **Webhook não está sendo recebido**
   - Verifique se a URL do webhook está correta no app do HubSpot
   - Verifique se o servidor está acessível publicamente
   - Verifique os logs para erros de processamento

2. **Polling não está processando tickets**
   - Verifique se os IDs de pipeline e status estão corretos
   - Verifique se o proprietário do ticket está definido corretamente
   - Verifique se o token de acesso do HubSpot é válido

3. **Salomão não está respondendo corretamente**
   - Verifique se a chave da API OpenAI é válida
   - Verifique se a base de conhecimento está configurada corretamente
   - Verifique os logs para erros de processamento

4. **Transferência para humano não está funcionando**
   - Verifique se os IDs de pipeline e status para humano estão corretos
   - Verifique se o token de acesso do HubSpot tem permissões suficientes
   - Verifique os logs para erros de processamento

## Apêndices

### Formato de Prompt do Sistema

O Salomão utiliza um prompt de sistema detalhado para orientar o comportamento do modelo GPT-4. O prompt inclui:

- Identidade e escopo do assistente
- Módulos da plataforma inChurch
- Processo de resposta
- Formatação de respostas para WhatsApp
- Diagnóstico de eventos
- Diagnóstico de usuários

### Formato de Mensagens do HubSpot

O HubSpot envia mensagens no seguinte formato:

```json
{
  "type": "MESSAGE",
  "senderActorId": "A-81908844",
  "channelId": "channel_id",
  "channelAccountId": "account_id",
  "text": "Mensagem de resposta"
}
```

### Estrutura de Sessões no Supabase

As sessões são armazenadas no Supabase com a seguinte estrutura:

```
hubspot_sessions
├── id (UUID)
├── created_at (timestamp)
├── thread_id (string)
├── ticket_id (string)
├── session_id (string)
├── visitor_actor_id (string)
├── visitor_name (string)
├── visitor_email (string)
├── channel_id (string)
├── channel_account_id (string)
├── message_count (integer)
├── last_message_time (timestamp)
└── is_active (boolean)
```

---

## Salomão Agent V2 - Agno Framework

### Visão Geral da V2

O Salomão Agent V2 é uma evolução completa do agente original, utilizando o framework **Agno** para implementar funcionalidades avançadas de IA.

### Funcionalidades Implementadas

#### 🧠 Agentic RAG (Retrieval-Augmented Generation)
- Busca inteligente na base de conhecimento Pinecone
- Contexto dinâmico baseado na pergunta do usuário
- Verificação de relevância dos documentos antes de responder

#### 📊 Tracing e Métricas (Supabase)
Todas as métricas são salvas no Supabase nas seguintes tabelas:

| Tabela | Descrição |
|--------|-----------|
| `salomao_feedback` | Feedback do usuário (Sim/Não) + comentários |
| `salomao_conversations` | Métricas de conversas (duração, transferências) |
| `salomao_user_profiles` | Perfil do usuário (nível de expertise) |
| `agno_user_memories` | Memórias de preferências |
| `agno_sessions` | Sessões de conversa |
| `agno_traces` | Traces de execução |

#### 👤 Análise de Expertise do Cliente
O sistema analisa automaticamente o nível de conhecimento do cliente:
- **Iniciante**: Mensagens curtas, termos como "não sei", "como faz"
- **Intermediário**: Mensagens de tamanho médio, perguntas objetivas
- **Avançado**: Termos técnicos (API, webhook, permissões), mensagens detalhadas

#### 💡 Formato de Respostas
Todas as respostas seguem o padrão:
1. Resposta clara com passos numerados (1️⃣ 2️⃣ 3️⃣)
2. Formatação WhatsApp (*negrito* com um asterisco)
3. Seção *💡 Dica* ao final com informação útil adicional

### Fluxo de Feedback

```
1. Usuário pergunta
2. Salomão responde + 💡 Dica
3. Pergunta: "Sua dúvida foi respondida? 1️⃣ Sim / 2️⃣ Não"
4. Se SIM → "Fico feliz em ajudar!" (conversa continua disponível)
5. Se NÃO → Envia link de pesquisa → Transfere para humano
```

### Pesquisa de Satisfação

Quando o usuário responde NÃO, recebe:
```
Você poderia acessar esse link e avaliar meu atendimento, por favor? 🥰

http://pesquisa.binds.co/pesquisa-fresh-sup-cs

Pra gente é muito importante saber como se sentiu nesse atendimento com o Salomão.

Muito obrigado! Deus abençoe! 💚
```

### Exemplo Completo de Conversa

#### 👁️ O que o CLIENTE vê:

```
👤 Cliente: Como criar um cupom de desconto?

🤖 Salomão: Para criar um *cupom de desconto* no inChurch, siga os passos:

1️⃣ Acesse o *evento* pelo Painel V2

2️⃣ Clique na aba de *Ingressos*

3️⃣ Desça até a seção de *Cupom de Desconto*

4️⃣ Clique em *Criar novo cupom*

5️⃣ Escolha o *Tipo de Desconto*:
   • Valor: Define um valor fixo
   • Porcentagem: Aplica uma % do total

6️⃣ Preencha os campos e clique em *Continuar*

*💡 Dica:* Você pode limitar o cupom por quantidade de usos ou por período de validade!

---

🤖 Salomão: 📊 *Sua dúvida foi respondida?*

1️⃣ Sim
2️⃣ Não

---

👤 Cliente: 1

🤖 Salomão: Ótimo! Fico feliz em ajudar! 😊 Se precisar de mais alguma coisa, é só chamar!
```

#### ⚙️ O que roda nos BASTIDORES:

```
14:30:01 | INFO | 📩 Processando mensagem: Como criar um cupom de desconto?...
14:30:01 | INFO | ==================================================
14:30:01 | INFO | 📩 Nova mensagem | Session: hubspot_thread_10268...
14:30:01 | INFO | 📝 Mensagem: Como criar um cupom de desconto?...
14:30:02 | INFO | 👤 Nível do usuário: intermediário
14:30:03 | INFO | 📚 Pinecone: 3 documentos encontrados para 'Como criar um cupom...'
14:30:03 | INFO | 📚 3 docs do Pinecone adicionados ao contexto
14:30:15 | INFO | 📊 Trace salvo: process_message (12543ms)
14:30:15 | INFO | ✅ Resposta gerada (584 chars) em 12543ms
14:30:16 | INFO | ✅ 50 mensagens encontradas no thread 10268843879
14:30:16 | INFO | 👤 Visitante encontrado: V-107866461956
14:30:17 | INFO | ✅ Mensagem enviada para thread 10268843879
14:30:17 | INFO | 📝 Feedback pendente salvo: dcbf6262...
14:30:18 | INFO | ✅ Mensagem enviada para thread 10268843879
14:30:18 | INFO | 📊 Pergunta de feedback enviada

--- Usuário responde "1" ---

14:30:45 | INFO | 📩 Processando mensagem: 1...
14:30:45 | INFO | 👍 Feedback positivo recebido
14:30:46 | INFO | ✅ Mensagem enviada para thread 10268843879
```

#### 📊 O que é SALVO no Supabase:

**Tabela: salomao_feedback**
```json
{
  "id": "dcbf6262-6f55-4dad-9196-82416f65a333",
  "session_id": "hubspot_thread_10268843879",
  "ticket_id": "39171243852",
  "question": "Como criar um cupom de desconto?",
  "response": "Para criar um *cupom de desconto*...",
  "feedback": "sim",
  "feedback_at": "2026-01-13T14:30:45.000Z"
}
```

**Tabela: salomao_user_profiles**
```json
{
  "session_id": "hubspot_thread_10268843879",
  "expertise_level": "intermediário",
  "interaction_count": 1,
  "avg_message_length": 35
}
```

**Tabela: agno_traces**
```json
{
  "agent_name": "Salomão",
  "session_id": "hubspot_thread_10268843879",
  "operation_name": "process_message",
  "status": "success",
  "duration_ms": 12543,
  "input_tokens": 1250,
  "output_tokens": 380,
  "model_used": "gpt-4o-mini"
}
```

### Variáveis de Ambiente Adicionais (V2)

```
# Supabase (obrigatório para V2)
SUPABASE_URL=https://iveiuwhirbwrfgzrjynz.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Dependências Adicionais (V2)

```
agno>=2.3.24
opentelemetry-api>=1.39.0
opentelemetry-sdk>=1.39.0
openinference-instrumentation-agno>=0.1.25
python-dateutil>=2.8.2
```

---

Documentação atualizada em: Janeiro de 2026
