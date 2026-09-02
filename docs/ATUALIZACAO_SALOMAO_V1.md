# Atualização do Salomão — 02/09/2026

## Publicação no repositório dedicado

Destino: `tiago-bot/whatsapp-salomao`, sobre o commit inicial com a licença.
Os repositórios anteriores não recebem commits nem pushes desta publicação.
Este snapshot não importa o histórico antigo: `.env`, bancos de conversas,
credenciais, caches e `node_modules` ficaram fora da publicação. Credenciais
encontradas na documentação antiga foram substituídas por exemplos.

Webhook alterado para `hs_v2_date_entered_1269308450`, no evento
`ticket.propertyChange`. Pipeline `636594474`, status `1269308450` e os demais
parâmetros de atendimento foram preservados. A data apenas dispara a verificação;
os três filtros continuam sendo consultados no HubSpot antes do processamento.

Validação desta publicação: 52 testes backend (incluindo quatro novos cenários
de webhook) e quatro testes de formatação do frontend. Nenhum teste dispara
mensagens reais. O push não é um deploy no HubSpot/Railway.

## Resultado

Referência clonada em `../salomao-v1-reference`, commit `63a8868`
(`Fix Salomao knowledge retrieval and standardize responses`).
O código de atendimento, busca publicada, normalização de consultas, referências,
resumos e métricas veio dessa versão. A integração HubSpot/WhatsApp foi adaptada
ao mesmo motor. O antigo módulo `salomao_agent_v2.py` virou um import de compatibilidade.

O frontend e o widget existentes foram preservados visualmente. A renderização
agora interpreta títulos, negrito e fontes da referência e escapa HTML antes
de inseri-lo no DOM. O endereço da API web aceita `VITE_API_URL`.

## Atendimento e apresentação

- Busca de artigos antes de responder; fontes limitadas aos documentos recuperados.
- Histórico recente em ordem cronológica; continuações curtas preservam o assunto.
- Escopo semântico, sem bloquear dúvidas só por falta de palavras-chave.
- Retorno de trechos documentais quando a geração falha, com limites de espera
  e intervalo antes de tentar a geração novamente, conforme a referência.
- Imagens, áudio e diagnóstico de eventos continuam disponíveis. Áudio ilegível
  pede reenvio, sem transformar o erro de transcrição em pergunta para o modelo.
- Pedido explícito de pessoa é reconhecido sem chamar o modelo; palavras
  incidentais como “gestão de pessoas” não disparam transferência.
- No WhatsApp, lacuna confirmada de documentação encaminha para a equipe;
  indisponibilidade é informada honestamente. Sem pesquisa obrigatória antes da transferência.
- Conversão determinística: títulos em `*negrito*`, passos separados,
  tabelas como campos e valores, links visíveis, sem marcadores internos.
- Textos longos divididos por parágrafos/linhas/palavras, sem cortar URLs,
  caracteres compostos ou emojis. Limite padrão conservador: 3.500 unidades UTF-16.

Exemplo de apresentação (ilustrativo, não uma orientação real de produto):

```text
*Título da orientação*

Explicação direta baseada no artigo.

1. Primeiro passo com *nome da tela*.

2. Próximo passo documentado.

Fonte: Título do artigo
https://portal.inchurch.com.br/pt-br
```

## Entrega e proteção

- Mensagens recebidas são registradas em uma fila de saída SQLite antes do envio.
- Cada parte é confirmada após o HubSpot aceitar; falha retoma a parte pendente.
- Reinício do processo preserva confirmações se o arquivo SQLite for persistido.
- Falha de transferência repete somente a transferência, não a resposta nem pesquisa.
- Locks por conversa evitam corrida entre polling e webhook dentro do processo.
- Pipeline, status e proprietário são conferidos antes de gerar e enviar.
- Anexos têm limite de tamanho e domínio permitido; credenciais HubSpot não são
  enviadas para URLs arbitrárias. Tipos não suportados pedem texto/reenvio.
- HTML do envio e dos chats é escapado. Token inRadar saiu do código e foi
  preservado apenas na configuração local ignorada pelo Git.

## Configuração necessária

1. Preserve as variáveis atuais de OpenAI, Pinecone, Supabase e HubSpot.
2. Para paridade da busca textual da Central, defina `KB_SUPABASE_URL` e
   `KB_SUPABASE_ANON_KEY` com acesso somente aos artigos publicados.
   Elas não estavam no `.env` original. Sem elas, o Pinecone continua como busca;
   o fallback independente de embeddings exige o catálogo configurado.
3. Configure `INRADAR_AUTH_TOKEN` no ambiente de produção. O token local
   anteriormente embutido foi preservado em `backend/.env.local`, sem copiá-lo
   para documentação ou arquivos de exemplo.
4. Use `HUBSPOT_POLLING_ENABLED=false` para teste local. O exemplo usa false;
   produção existente sem essa variável mantém polling ativo por compatibilidade.
5. Produção: configure `DELIVERY_DB_PATH` em volume persistente e execute **uma
   instância com um worker**. Ex.: `/data/salomao-delivery.sqlite3`.
6. O frontend atualizado requer Node 20.19+ ou 22.12+; validado com Node 24.
7. Instale `backend/requirements-lock.txt` e `frontend/package-lock.json` para
   reproduzir as dependências verificadas.

Nenhuma migração remota de banco foi executada. Métricas usam as tabelas já
utilizadas pela referência (`salomao_memories`). O SQLite é criado automaticamente.

## Validação

```powershell
cd backend
..\.venv\Scripts\python.exe run_offline_tests.py
cd ..\frontend
npm test
npm run build
```

Suíte backend: 48 testes de escopo, busca, contexto, formatação, anexos,
retomada de envio, transferência e contrato HTTP. O runner bloqueia conexões
externas, permitindo somente loopback necessário ao asyncio no Windows.
Frontend: quatro testes de formatação/escape e build de produção. Vite foi
atualizado para 8.2.2 e o plugin React para 6.1.1; `npm audit` ficou com zero
vulnerabilidades conhecidas na execução, após nove alertas no estado anterior.
Também foram verificadas sintaxe Python/JavaScript e compatibilidade Agno.

## Limites e pendências de produção

- Não houve deploy, envio a clientes, transferência real nem teste de qualidade
  de respostas com a chave OpenAI de produção. As integrações foram simuladas.
- O aceite HTTP do HubSpot não comprova entrega/leitura no aparelho. Falha de rede
  ambígua ou encerramento entre aceite remoto e confirmação local ainda pode
  exigir reconciliação; a API atual não fornece idempotência nessa chamada.
- O SQLite exige volume persistente, backup, controle de acesso e política de
  retenção, pois contém mensagens. Não use múltiplas réplicas sem fila compartilhada.
- `.env` e `node_modules` foram retirados do índice do Git, preservando os
  arquivos locais. Essas remoções do versionamento estão staged; não houve commit.
  O `.gitignore` impede novas inclusões acidentais, mas não apaga o histórico.
  Rotacione as credenciais anteriormente versionadas antes de publicar.
- Autenticação de endpoints administrativos e validação de assinatura do webhook
  ainda dependem da proteção da implantação existente; não exponha a API sem ela.
- A equivalência é do motor de atendimento com adaptação de canal, não uma
  substituição do frontend pelo produto web completo do repositório de referência.

## Referências técnicas

- [Código-base](https://github.com/tiago-bot/salomao-v1/tree/63a8868)
- [OpenAI — Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Agno — Teams](https://docs.agno.com/teams/building-teams)
- [Meta — texto no WhatsApp](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/types/TextObject/)
- [Vite — migração](https://vite.dev/guide/migration)

A consulta via OpenAI Docs orientou a preservação dos parâmetros de geração
compatíveis com o motor de referência; nenhum modelo foi trocado silenciosamente.
