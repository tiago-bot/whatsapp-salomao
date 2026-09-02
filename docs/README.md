# 🤖 Salomão AI - Assistente Inteligente para WhatsApp via HubSpot

## Visão Geral

O **Salomão** é um agente de IA desenvolvido para atendimento automatizado via WhatsApp, integrado com a plataforma HubSpot Conversations. Ele foi projetado especificamente para suporte à plataforma **inChurch**, oferecendo:

- ✅ Respostas inteligentes baseadas em base de conhecimento (Pinecone)
- ✅ Diagnóstico automático de eventos e usuários
- ✅ Transcrição de áudios (Whisper)
- ✅ Análise de imagens (GPT-4o Vision)
- ✅ Transferência automática para atendimento humano
- ✅ Continuação de conversas anteriores

---

## 📁 Estrutura do Projeto

```
salomao-v1/
├── backend/
│   ├── main_hubspot.py      # API FastAPI principal
│   ├── hubspot_bot.py       # Bot de integração HubSpot
│   ├── hubspot_service.py   # Serviços da API HubSpot
│   ├── salomao_agent.py     # Agente de IA (OpenAI)
│   ├── knowledge_base.py    # Integração Pinecone
│   ├── database.py          # Persistência Supabase
│   ├── inchurch_api.py      # API inChurch (diagnósticos)
│   ├── assets/
│   │   └── evento-id-exemplo.png  # Imagem de ajuda
│   └── .env                 # Variáveis de ambiente
└── docs/
    └── README.md            # Esta documentação
```

---

## 🔧 Configuração

### Variáveis de Ambiente (.env)

```env
# OpenAI
OPENAI_API_KEY=sk-...

# HubSpot
HUBSPOT_ACCESS_TOKEN=pat-na1-...
HUBSPOT_TARGET_PIPELINE=636594474
HUBSPOT_TARGET_STATUS=1269308450

# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJ...

# Pinecone
PINECONE_API_KEY=pcsk_...
PINECONE_INDEX_NAME=inchurch-knowledge-base

# inChurch API
INCHURCH_API_KEY=...
INCHURCH_API_BASE_URL=https://api.inchurch.com.br
```

### Pipelines HubSpot

| Pipeline | ID | Descrição |
|----------|-----|-----------|
| Atendimento IA | `636594474` | Tickets processados pelo Salomão |
| Atendimento Humano | `636459134` | Tickets transferidos para humanos |

| Status | ID | Descrição |
|--------|-----|-----------|
| Novo (IA) | `1269308450` | Aguardando processamento do bot |
| Novo (Humano) | `939275049` | Aguardando atendente humano |

---

## 🚀 Execução

### Iniciar o Servidor

```bash
cd backend
python main_hubspot.py
```

O servidor inicia em `http://localhost:8000` com:
- **Polling automático** a cada 10 segundos
- **Verificação de tickets** na pipeline configurada
- **Processamento de mensagens** de texto, áudio e imagem

### Endpoints Disponíveis

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/health` | Health check |
| GET | `/status` | Status do bot |
| POST | `/webhook/hubspot` | Webhook para mensagens (tempo real) |
| POST | `/process/ticket/{id}` | Processa ticket específico |
| DELETE | `/session/{id}` | Limpa sessão |

---

## 🧠 Funcionamento do Agente

### Fluxo de Processamento

```
1. Polling detecta ticket na pipeline
        ↓
2. Busca mensagens não processadas do thread
        ↓
3. Para cada mensagem:
   a. Verifica se é áudio → Transcreve (Whisper)
   b. Verifica se é imagem → Processa (GPT-4o Vision)
   c. Busca contexto na base de conhecimento (Pinecone)
   d. Verifica diagnósticos (evento/usuário)
   e. Gera resposta (GPT-4o-mini ou GPT-4o)
        ↓
4. Pós-processamento:
   - Formata quebras de linha
   - Verifica pedido de transferência
        ↓
5. Envia resposta para WhatsApp via HubSpot
        ↓
6. Se solicitado: Transfere ticket para atendimento humano
```

### Detecção de Intenções

#### Transferência para Atendimento Humano

O Salomão detecta automaticamente quando o usuário quer falar com um humano:

**Frases detectadas:**
- "quero falar com atendente"
- "chamar suporte"
- "falar com pessoa"
- "atendimento humano"

**Ação:** O ticket é movido para a pipeline `636459134` (Atendimento Humano).

#### Diagnóstico de Eventos

Quando o usuário menciona problemas com eventos:

**Frases detectadas:**
- "evento não aparece"
- "não vejo o evento"
- "evento sumiu"
- "criei um evento mas..."

**Ação:**
1. Pede o ID ou link do evento
2. Busca dados na API inChurch
3. Analisa visibilidade, status, publicação
4. Envia imagem de ajuda mostrando onde encontrar o ID

#### Diagnóstico de Usuários

Quando o usuário menciona problemas de acesso:

**Frases detectadas:**
- "não consigo acessar"
- "minha conta está bloqueada"
- "não consigo comprar ingresso"

**Ação:**
1. Pede o email do usuário
2. Busca dados na API inChurch
3. Analisa status da conta, verificação, security score
4. Orienta sobre próximos passos

---

## 📝 Formatação de Respostas

### Regras Aplicadas

1. **Passos em linhas separadas** - Cada passo numerado fica em sua própria linha
2. **Emojis numerados** - Usa 1️⃣, 2️⃣, 3️⃣ para passos
3. **Negrito** - Destaca termos importantes com **negrito**
4. **Sem links** - Não menciona URLs (não funcionam no WhatsApp via HubSpot)
5. **Concisão** - Respostas diretas e objetivas

### Exemplo de Resposta Formatada

```
Para criar um cupom de desconto, siga os passos:

1️⃣ Acesse o **Painel V2** e vá em **Ingressos**

2️⃣ Desça até a área **Cupom de Desconto**

3️⃣ Clique em **Criar novo cupom**

4️⃣ Preencha os campos e clique em **Continuar**

Pronto! Se precisar de mais ajuda, me avise.
```

---

## 🎵 Processamento de Áudio

### Formatos Suportados

- `.mp3`, `.wav`, `.ogg`, `.m4a`, `.opus`
- `.oga`, `.ptt` (formatos do WhatsApp)

### Fluxo

1. Detecta attachment de áudio na mensagem
2. Baixa o arquivo via API HubSpot
3. Converte para base64
4. Transcreve usando OpenAI Whisper
5. Processa a transcrição como texto normal

---

## 🖼️ Processamento de Imagens

### Formatos Suportados

- `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`

### Fluxo

1. Detecta attachment de imagem na mensagem
2. Baixa o arquivo via API HubSpot
3. Converte para base64
4. Envia para GPT-4o Vision junto com o contexto
5. Gera resposta baseada na análise visual

---

## 📊 Base de Conhecimento (Pinecone)

### Configuração

- **Index:** `inchurch-knowledge-base`
- **Modelo de Embedding:** `multilingual-e5-large`
- **Namespace:** `default`

### Busca Semântica

O Salomão busca artigos relevantes na base de conhecimento para cada mensagem:

1. Gera embedding da pergunta do usuário
2. Busca os 3 artigos mais similares
3. Inclui o conteúdo no contexto do prompt
4. O LLM usa essas informações para responder

---

## 💾 Persistência (Supabase)

### Tabelas

#### `salomao_sessions`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| session_id | text | ID único da sessão |
| message_count | int | Contador de mensagens |
| topics_discussed | jsonb | Tópicos abordados |
| last_activity | timestamp | Última atividade |

#### `salomao_messages`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | uuid | ID da mensagem |
| session_id | text | ID da sessão |
| role | text | 'user' ou 'assistant' |
| content | text | Conteúdo da mensagem |
| has_image | boolean | Se contém imagem |
| has_audio | boolean | Se contém áudio |
| audio_transcription | text | Transcrição do áudio |
| model_used | text | Modelo usado |
| transfer_requested | boolean | Se pediu transferência |
| created_at | timestamp | Data de criação |

---

## 🔄 Continuação de Conversas

Quando um usuário retorna após um tempo (4+ horas), o Salomão oferece continuar a conversa anterior:

**Exemplo:**
```
Olá! Vi que estávamos conversando sobre cupom de desconto.
Deseja continuar de onde paramos?
```

### Lógica

1. Detecta saudação do usuário
2. Verifica última atividade da sessão
3. Se > 4 horas, oferece continuação
4. Inclui resumo dos tópicos discutidos

---

## 🌐 Webhook vs Polling

### Polling (Atual)

- Verifica tickets a cada 10 segundos
- Funciona sem configuração externa
- Ideal para desenvolvimento/testes

### Webhook (Recomendado para Produção)

Para usar webhooks em vez de polling:

#### 1. Expor o Servidor

Use ngrok ou deploy em servidor público:

```bash
ngrok http 8000
```

#### 2. Configurar no HubSpot

1. Acesse **Settings > Integrations > Private Apps**
2. Selecione seu app
3. Vá em **Webhooks**
4. Adicione subscription:
   - **Object:** Conversations
   - **Event:** `conversation.newMessage`
   - **URL:** `https://seu-dominio.com/webhook/hubspot`

#### 3. Vantagens do Webhook

- ⚡ Resposta instantânea (sem delay de polling)
- 📉 Menos requisições à API
- 💰 Menor consumo de recursos

---

## 🔒 Segurança

### Boas Práticas Implementadas

1. **Variáveis de ambiente** - Secrets nunca no código
2. **Validação de webhook** - Verificação de assinatura HubSpot
3. **Dados sensíveis** - CPF, telefone, IDs não expostos ao usuário
4. **Rate limiting** - Respeito aos limites da API HubSpot

### Dados Protegidos

O Salomão **nunca expõe** ao usuário:
- CPF ou documentos
- Telefones pessoais
- IDs internos do sistema
- Tokens ou chaves de API

---

## 🐛 Troubleshooting

### Erro: "Authentication credentials not found"

**Causa:** Token HubSpot não carregado.

**Solução:** Verifique se `load_dotenv()` está sendo chamado antes de acessar `HUBSPOT_ACCESS_TOKEN`.

### Erro: "Não foi possível identificar o visitante"

**Causa:** Não encontrou actorId do visitante no thread.

**Solução:** Verifique se há mensagens do visitante no thread.

### Mensagens não processadas

**Causa:** Mensagens muito antigas (> 5 minutos).

**Solução:** O bot só processa mensagens dos últimos 5 minutos para evitar reprocessamento. Envie uma nova mensagem.

### Formatação incorreta

**Causa:** LLM não seguiu instruções.

**Solução:** O pós-processamento `_format_response_with_linebreaks` força quebras de linha nos passos numerados.

---

## 📈 Métricas e Logs

### Logs Disponíveis

```
11:54:32 | INFO | 🚀 Salomão HubSpot Bot iniciado!
11:54:32 | INFO | 🔄 Polling iniciado - intervalo: 10s
11:54:33 | INFO | ✅ Encontrados 1 tickets na pipeline
11:54:33 | INFO | 📬 1 ticket(s) encontrado(s)
11:54:33 | INFO | 🎫 Processando ticket 39006138916
11:54:34 | INFO | 📩 Nova mensagem | Session: hubspot_thread_...
11:54:34 | INFO | 🤖 Modelo: gpt-4o-mini
11:54:35 | INFO | 🎯 TOKENS: prompt=3446 | completion=52 | TOTAL=3498
11:54:35 | INFO | ✅ Resposta enviada (220 chars)
```

### Monitoramento de Tokens

Cada resposta registra:
- Tokens de prompt
- Tokens de completion
- Total de tokens

---

## 🚀 Deploy

### Requisitos

- Python 3.10+
- Dependências: `pip install -r requirements.txt`

### Produção

1. Configure variáveis de ambiente no servidor
2. Use gunicorn ou uvicorn workers:

```bash
uvicorn main_hubspot:app --host 0.0.0.0 --port 8000 --workers 4
```

3. Configure HTTPS (obrigatório para webhooks)
4. Configure webhook no HubSpot

---

## 📞 Suporte

Para problemas técnicos com o Salomão:
- **WhatsApp:** (11) 94143-6554
- **Email:** suporte@inchurch.com.br

---

## 📄 Licença

Projeto proprietário da inChurch. Todos os direitos reservados.
