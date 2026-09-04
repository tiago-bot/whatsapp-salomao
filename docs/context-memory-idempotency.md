# Contexto, memória e entrega — revisão de 02/09/2026 (BRT)

Somente `tiago-bot/whatsapp-salomao`, o Salomão novo. Alterações preparadas
localmente, sem publicar nem alterar variáveis de produção nesta revisão.

## Causa confirmada

No ticket 48159466650, às 21:34:59, o agente recebeu 30 mensagens anteriores.
A entrada "quais são os obrigatórios?" foi aprovada como suporte (0,98), mas
a busca registrou `contextualized=false`: não incorporou o cadastro de membro.
Recuperou outras fontes e o verificador de saída bloqueou a resposta às 21:35:13.
Não era simplesmente falta de acesso ao histórico. O espaçamento excessivo
também era acrescentado pelo formatador, não apenas pelo modelo.

## Correção de continuidade e texto

- Perguntas sobre atributos mantêm o objeto: campos, obrigatoriedade, prazo,
  permissões, detalhes citados e respostas curtas. Mudanças explícitas prevalecem.
- O classificador semântico também fornece uma pergunta contextualizada, sem
  responder ou inventar fatos. A busca recebe essa resolução quando necessário.
- A fonte citada na última resposta é recuperada novamente do catálogo/índice;
  uma resposta anterior não vira prova de uma regra de produto.
- O objetivo extraído das falas do cliente pode ser preservado além da janela
  de 30 mensagens. Há até 31 registros de contexto, limitados a 24 mil caracteres,
  extraídos das últimas 100 mensagens observadas. Não é memória ilimitada.
- Mensagens textuais consecutivas, sem resposta intermediária, formam um turno.
  Todos os IDs de entrada ficam vinculados à mesma entrega de forma atômica.
- Pré-requisitos antes dos passos; detalhes diretamente relacionados ao pedido;
  acompanhamentos sem reiniciar o tutorial; listas curtas sem linhas vazias
  entre itens. Parágrafos diferentes continuam separados.
- A barreira contra assuntos externos permanece antes da geração e na saída.

## Supabase funcionando com o esquema existente

`SUPABASE_CONVERSATION_MEMORY_ENABLED=true` (padrão) habilita checkpoints em
`salomao_sessions.metadata`, usando `session_id=whatsapp_salomao_memory_<thread>`.
Não precisa criar tabelas nem alterar permissões. A gravação usa upsert pela
chave única de sessão, foi testada com registro sintético e recuperada com sucesso.
O registro de teste foi removido ao terminar; nenhum ticket recebeu mensagem.

O checkpoint contém somente mensagens observadas no HubSpot ou com recibo de
envio. Não inclui respostas geradas e ainda não enviadas. Possui isolamento por
conversa, limite de 100 mensagens e checksum para detectar corrupção acidental.
O checksum não substitui autenticação/RLS. Não ampliar acesso público às tabelas;
as credenciais permanecem somente no backend e são as já usadas pela aplicação.

A memória é recuperada quando o cache local está vazio. Falhas de Supabase
preservam o cache local e geram `memory.unavailable`, com recuo de 60 segundos.
`memory.saved` e `memory.restored` confirmam o funcionamento sem expor conteúdo.
O histórico de gerações/métricas também passa a usar namespace exclusivo
`whatsapp_salomao_thread_<thread>`, sem reutilizar as sessões do bot antigo.

Importante: o checkpoint não é recibo de envio. Se o volume local for perdido,
entradas antigas recuperadas sem recibo ficam marcadas para revisão, sem replay
automático (`memory.receipts_review`). Mensagens novas continuam independentes.

## Idempotência e limites reais

A entrega exige **um serviço, um volume persistente em DELIVERY_DB_PATH e um
worker em produção**. A trava entre processos cobre o mesmo volume, inclusive
reinícios sobrepostos; não coordena bancos/volumes independentes nem outro bot.
O Supabase de memória não é usado como trava distribuída.

1. A fila tem chave única por conversa e mensagem recebida.
2. Uma trava de arquivo por conversa impede duas gerações simultâneas no mesmo
   volume e é liberada pelo sistema operacional após encerramento do processo.
3. Antes do POST, grava-se duravelmente uma tentativa por parte, em transação
   exclusiva. Duas conexões disputando a mesma parte têm um único vencedor.
4. Recibo remoto, avanço da fila e mensagem entregue são confirmados na mesma
   transação SQLite. Partes confirmadas não são repetidas, mesmo com objeto de
   fila antigo em memória.
5. Rejeição explícita (400/401/403/404/422/429) permite outra tentativa. Timeout,
   conexão interrompida, 5xx ou resposta sem ID são **incertos**: não se reenvia.
6. Queda entre POST e confirmação local permanece retida para conferência.
   Pendências da versão anterior sem registro de tentativa também ficam retidas.

Isso prioriza impedir duplicidade, não fingir entrega quando há incerteza.
Não há garantia matemática de exactly-once ponta a ponta: o endpoint de mensagens
utilizado não documenta chave de idempotência de envio. O campo da API de
**custom channels** não deve ser presumido válido para esta integração.

Eventos: `delivery.rejected` (repetição permitida), `delivery.uncertain` (conferir),
`delivery.held` (continua retida, DEBUG), `delivery.part_sent` e `delivery.completed`.
Uma pendência incerta suspende novos envios nessa conversa, preservando a ordem.

## Observação na transferência para o Suporte N1

Antes de mover o ticket para Suporte N1 / Novo, o backend cria uma nota associada
ao ticket (`POST /crm/v3/objects/notes`, associação Note → Ticket `228`). Ela fica
visível na área de Observações e contém somente informações já observadas:

- problema e contexto relatados pelo cliente;
- orientações já enviadas pelo Salomão;
- resultado explicitamente informado, ou a ausência dessa confirmação;
- motivo da transferência;
- fontes registradas na conversa;
- referência interna determinística para auditoria.

O HTML é escapado antes do envio e a nota tem limite próprio, sem registrar o
conteúdo nos logs. A criação da nota também possui claim e recibo persistentes.
Uma rejeição explícita permite repetição. Timeout, 5xx ou resposta sem ID retêm a
transferência para conferência e não repetem o POST automaticamente. Depois que a
nota é confirmada, falhas ao mover o ticket repetem apenas a mudança de pipeline,
sem reenviar mensagens e sem criar uma segunda observação.

Referências: [Notes API](https://developers.hubspot.com/docs/api-reference/legacy/crm/activities/notes/guide)
e [tipos padrão de associação](https://developers.hubspot.com/docs/api-reference/latest/crm/associations/associate-records/guide).

Inspeção sem modificar dados, no ambiente com o volume montado:

```sh
python delivery_audit.py --db /data/salomao-delivery.sqlite3
```

Para reconciliar, conferir o recibo/mensagem correspondente no HubSpot. Não apagar
a fila, limpar o volume ou redefinir `sent_parts` para forçar outra tentativa.
Uma ausência na página recente de mensagens não prova que o envio falhou.

## Validação

### Agrupamento com pausa de 5 segundos

`HUBSPOT_MESSAGE_DEBOUNCE_SECONDS=5` é o padrão. Cada chegada reinicia a pausa,
usando os horários do HubSpot (um webhook duplicado não reinicia o relógio).
O lote fecha no máximo 20 segundos após sua primeira mensagem, mesmo se o
cliente continuar escrevendo. Mensagens posteriores iniciam o próximo lote.

Antes de enfileirar/enviar a resposta, uma nova leitura confere complementos.
Se pertencem ao lote aberto, o rascunho não enviado é descartado e regenerado
com os novos textos, sem criar recibos falsos. Todos os IDs entram na transação
da mesma entrega. Falha nessa leitura suspende o envio; mudança de proprietário
continua bloqueando o processamento. Há até três gerações por ciclo para limitar
uso do worker, preservando as mensagens restantes para o ciclo seguinte.

Os 5 segundos são a pausa de agrupamento, não promessa de resposta em 5 segundos:
o intervalo de polling e o tempo do modelo também contam. Uma mensagem que chegue
após a última leitura/POST pode entrar no turno seguinte; não existe transação
atômica entre leitura de novas mensagens e envio no provedor. Uma entrega já
iniciada/confirmada não é reescrita, mantendo as regras de idempotência acima.

### Execução dos testes

- `python run_offline_tests.py`: suite isolada de regressão, sem rede externa.
- `python evaluate_context.py --live`: opt-in com modelos/base reais, sem envio
  ao HubSpot nem gravação de conversas reais. Usa fixture sem dados pessoais.
- `--supabase-check`: adicionalmente grava, recupera e remove exatamente um
  checkpoint sintético de teste no Supabase.
- Na avaliação real, "quais são os obrigatórios?" respondeu nome completo,
  sexo, data de nascimento, país e endereço com a fonte do cadastro de membros.
  "E telefone, precisa?" permaneceu no cadastro; futebol foi bloqueado.

Referências: [estado de conversa — OpenAI](https://developers.openai.com/api/docs/guides/conversation-state),
[upsert — Supabase](https://supabase.com/docs/reference/python/upsert),
[Conversations — HubSpot](https://developers.hubspot.com/docs/api-reference/legacy/conversations/guide).
