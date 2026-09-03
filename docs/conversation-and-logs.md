# Continuidade de conversa e logs

A revisão posterior do caso "quais são os obrigatórios?", incluindo memória no
Supabase e retenção de envios incertos, está em [context-memory-idempotency.md](context-memory-idempotency.md).
A situação real de imagem/áudio está em [multimodal-readiness.md](multimodal-readiness.md).

## Correções locais de 02/09/2026

O incidente analisado foi uma continuação de estorno ("o botão não aparece")
respondida com orientações de células. A correção não se limita ao prompt:

- O canal WhatsApp fornece o histórico real do HubSpot em ordem cronológica.
  A consulta começa pelas mensagens mais recentes e respeita a paginação.
- Um cache por conversa, no mesmo SQLite persistente das entregas, mantém até
  100 mensagens observadas ou efetivamente enviadas. Rascunhos não enviados
  não entram como respostas anteriores. Conversas diferentes ficam separadas.
- Cada geração recebe até 30 mensagens anteriores, limitadas a 24.000 caracteres
  no total e 4.500 por mensagem. Quando precisa abreviar, preserva começo e fim,
  incluindo a pergunta pendente. Não é memória ilimitada.
- O contexto do canal prevalece sobre o histórico auxiliar do Supabase. Falha
  ao consultar o HubSpot adia a geração, em vez de responder sem o histórico.
- Busca e triagem usam o assunto do cliente. Sintomas genéricos e respostas
  curtas mantêm o assunto; mudanças explícitas podem substituí-lo. Artigos de
  outro assunto são filtrados nas continuações reconhecidas. Respostas antigas
  equivocadas não são tratadas como comprovação de regras do produto.
- As instruções compartilhadas orientam acolhimento, uma pergunta decisiva por
  vez, aproveitamento dos dados já informados e distinção entre hipótese e causa
  confirmada. O formato WhatsApp mantém blocos curtos, links e negrito compatível,
  sem emojis decorativos.

Também foram corrigidas duas falhas de integração verificadas na API:
a leitura do ticket não solicitava `hubspot_owner_id`; a associação recebida
em `threadAssociations.associatedTicketId` não era normalizada para o webhook.
Isso podia impedir o processamento antes de chegar ao modelo. Os filtros foram
preservados: Triagem N1 (`636594474`), Teste de IA (`1269308450`) e proprietário
Suporte inChurch (`81908844`).

Na nova leitura do ticket também apareceu uma resposta a uma pergunta sobre
futebol, fora do escopo do atendimento. A política compartilhada reforça o foco
na inChurch; o classificador foi validado com essa pergunta, mesmo precedida
por histórico de estorno. Transcrições de áudio sem texto também ficam no cache
para sustentar a próxima mensagem.

## Logs

Um único handler escreve JSON de uma linha no stdout, com severidade explícita
em `level`. O Railway classifica stderr como erro mesmo quando o texto contém
"INFO"; por isso as mensagens rotineiras apareciam vermelhas. Erros reais
continuam com nível `error`, e avisos com `warn`.

Eventos principais: `turn.started`, `context.loaded`, `knowledge.retrieved`,
`turn.generated`, `delivery.part_sent`, `delivery.completed`, `delivery.retry`,
`turn.deferred`, `webhook.filtered` e `polling.summary`.

Cada geração e entrega compartilha `ticket_id`, `thread_id`, `message_id`,
`session_id` e `run_id`; uma entrega retomada preserva o identificador da geração.
Os registros incluem quantidade de contexto, fontes recuperadas, modelo, duração,
estado da resposta e motivo de bloqueio. Não registram corpo de conversa,
credenciais, valores de propriedades ou respostas completas das APIs. Exceções
mantêm tipo e localização de código, omitindo corpo e variáveis locais.

O polling registra um resumo quando o estado muda, há resposta, ou após 60s.
Verificações repetidas ficam em `DEBUG`. Uma falha de busca não aparece como
um ciclo saudável com zero tickets. `LOG_LEVEL=INFO` é o padrão.

## Validação sem publicar

Na pasta `backend`, execute `python run_offline_tests.py`. Esse executor bloqueia
conexões externas e usa dados sintéticos; não execute indiscriminadamente os
scripts antigos `test_*.py`, pois alguns consultam integrações reais.

Na pasta `frontend`, execute `npm test`.

A regressão cobre o incidente, continuações sucessivas, correção de assunto,
respostas numéricas, isolamento entre conversas, reinício, falha do histórico,
paginação, filtros de elegibilidade, formato de WhatsApp e severidade dos logs.
Também foram avaliados cinco cenários sintéticos com os modelos configurados,
sem enviar mensagens ao HubSpot nem gravar conversas no Supabase.

Para a futura publicação autorizada, manter um único worker e o volume persistente
em `DELIVERY_DB_PATH`. A tabela do cache é criada de forma aditiva, sem apagar
recibos de entrega. A nova configuração não altera retroativamente os logs já
armazenados no Railway. As correções precisam ser publicadas para valer no serviço.

Referências: [estado de conversa da OpenAI](https://developers.openai.com/api/docs/guides/conversation-state)
e [severidade de logs no Railway](https://docs.railway.com/observability/logs).

## Reforço de escopo autorizado para publicação

Política `2026-09-02-strict-v1`:

- Perguntas externas explícitas, incluindo a pergunta sobre Libertadores, são
  bloqueadas por código antes da geração, inclusive quando acompanhadas de anexos.
- Classificação incerta, inválida ou indisponível não libera geração de texto:
  o cliente recebe uma pergunta curta para esclarecer o vínculo com a plataforma.
- Um validador separado confere a resposta final, incluindo respostas com fontes
  e respostas de diagnóstico. Só aprovação explícita com confiança mínima de 0,9
  libera o texto. Falha, timeout ou reprovação produzem uma mensagem fixa segura.
- A fila exige aprovação da política atual vinculada ao texto e às partes que
  serão enviadas. Entregas antigas sem aprovação ou alteradas são retidas com
  motivo de bloqueio, preservando conteúdo e recibos para auditoria, sem envio.
- O contexto de um atendimento anterior não autoriza curiosidades externas;
  termos esportivos em nomes de eventos, por si só, não são proibidos.

O validador usa o modelo leve já configurado, com limite de 10s e sem retentativa
automática. Acrescenta uma etapa de validação às respostas geradas. As regras
determinísticas cobrem padrões explícitos; a classificação semântica não oferece
garantia matemática para toda linguagem possível. Os bloqueios e as falhas são
registrados, e o comportamento padrão em dúvida é não liberar a resposta candidata.

Essas barreiras seguem a separação entre verificações de entrada, saída e envio
descrita na [documentação oficial da OpenAI](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals).
`/health` e `service.started` informam a versão da política para conferir o deploy.
