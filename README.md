# Salomão - Assistente Inteligente inChurch

Repositório de publicação: [tiago-bot/whatsapp-salomao](https://github.com/tiago-bot/whatsapp-salomao).

## HubSpot: gatilho de entrada no atendimento

- Pipeline: `636594474`.
- Status: `1269308450`.
- Propriedade do webhook: `hs_v2_date_entered_1269308450`.
- Evento: `ticket.propertyChange`.
- Proprietário mantido: `A-81908844` (owner `81908844`).

A propriedade envia uma **data de entrada**, não o código do status. Quando
recebe uma data não vazia, o backend consulta o ticket e confirma pipeline,
status e proprietário antes de processar. Limpeza da data ou mudanças em outras
propriedades não acionam esse fluxo. Eventos de conversa e polling foram preservados.

As demais configurações são carregadas do ambiente, sem troca de credenciais.
O `.env` não é publicado. Configure no serviço de hospedagem as variáveis atuais
e, opcionalmente, `HUBSPOT_SALOMAO_ENTRY_PROPERTY` (o padrão já é o valor acima).
O arquivo de assinatura do webhook em `hubspot-app` foi atualizado; publicar o
código no GitHub não altera sozinho as configurações do aplicativo no HubSpot.

Para hospedar o WhatsApp, use `backend` como diretório raiz e o comando de
`backend/Procfile` ou a configuração `backend/railway.json` existente. Preserve
a URL de destino do webhook e as variáveis do serviço atual se ele for reutilizado.

## Atualização — Salomão v1 + WhatsApp (02/09/2026)

O motor de atendimento foi alinhado ao `tiago-bot/salomao-v1`, commit `63a8868`.
API web, endpoint de teste e bot HubSpot usam agora `backend/salomao_agent.py`.
A integração do WhatsApp continua neste repositório; não foi substituída pelo site.

Consulte **[o relatório da atualização](docs/ATUALIZACAO_SALOMAO_V1.md)** para
mudanças, testes, configurações e limites operacionais. As instruções antigas
abaixo descrevem o chat web; o WhatsApp usa `main_hubspot:app`.

Teste seguro, sem APIs externas nem clientes reais (Python 3.12):

```powershell
cd backend
..\.venv\Scripts\python.exe run_offline_tests.py
```

Instale `backend/requirements-lock.txt` para reproduzir as versões validadas.
Para o frontend, use Node 22.12+ ou 24, `npm ci`, `npm test` e `npm run build`.

**Não use os scripts antigos `test_agent.py`, `test_hubspot.py` ou polling para
testes offline:** eles podem acessar serviços reais.

Agente de IA para suporte ao cliente da plataforma inChurch. Utiliza GPT-4 e Pinecone para fornecer respostas precisas baseadas na base de conhecimento.

## Funcionalidades

- 💬 **Chat inteligente** com memória de conversa
- 📚 **Base de conhecimento** integrada com Pinecone
- 🖼️ **Análise de imagens** (capturas de tela, prints)
- 🎤 **Transcrição de áudio** via Whisper
- 🧠 **Memória automática** para personalização
- 🔄 **Contexto conversacional** mantido entre mensagens

## Estrutura do Projeto

```
ia-suporte/
├── backend/
│   ├── .env                 # Variáveis de ambiente
│   ├── requirements.txt     # Dependências Python
│   ├── config.py           # Configurações
│   ├── knowledge_base.py   # Integração com Pinecone
│   ├── salomao_agent.py    # Agente principal
│   └── main.py             # API FastAPI
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── App.jsx         # Componente principal
│   │   ├── main.jsx
│   │   └── index.css
│   └── index.html
└── README.md
```

## Instalação

### Backend

```bash
cd backend

# Criar ambiente virtual
python -m venv venv

# Ativar ambiente (Windows)
venv\Scripts\activate

# Ativar ambiente (Linux/Mac)
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt
```

### Frontend

```bash
cd frontend

# Instalar dependências
npm install
```

## Configuração

Configure estas variáveis no ambiente ou em `backend/.env` local (não versionado):

- `OPENAI_API_KEY` - Chave da API OpenAI
- `PINECONE_API_KEY` - Chave da API Pinecone
- `PINECONE_HOST` - Host do índice Pinecone
- `PINECONE_INDEX_NAME` - Nome do índice

## Execução

### Iniciar Backend

```bash
cd backend
python main.py
```

O servidor iniciará em `http://localhost:8000`

### Iniciar Frontend

```bash
cd frontend
npm run dev
```

O frontend iniciará em `http://localhost:3000`

## Endpoints da API

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/` | Status da API |
| GET | `/health` | Health check |
| POST | `/chat` | Enviar mensagem (JSON) |
| POST | `/chat/upload` | Enviar com arquivos |
| GET | `/conversation/{id}` | Histórico da conversa |
| DELETE | `/conversation/{id}` | Limpar conversa |
| POST | `/session/new` | Nova sessão |

## Exemplo de Uso

### Chat via API

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Como criar cupom de desconto?",
    "session_id": "minha-sessao"
  }'
```

### Com Imagem (base64)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "O que você vê nesta imagem?",
    "session_id": "minha-sessao",
    "image_base64": "..."
  }'
```

## Tecnologias

- **Backend**: Python, FastAPI, OpenAI, Pinecone
- **Frontend**: React, Vite, TailwindCSS
- **IA**: GPT-4o, GPT-4o-mini, Whisper
- **Vector DB**: Pinecone (text-embedding-3-small)
