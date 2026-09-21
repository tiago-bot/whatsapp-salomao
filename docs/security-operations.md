# Segurança e operação do backend HubSpot

Estas mudanças se aplicam ao serviço WhatsApp iniciado com `main_hubspot:app`.
O antigo chat web em `main.py` é outro entrypoint e não deve ser usado para
publicar este serviço.

## Configuração antes da publicação

Defina no ambiente do serviço, usando `backend/.env.example` como referência:

| Variável | Uso |
| --- | --- |
| `ADMIN_API_TOKEN` | Token aleatório com pelo menos 32 caracteres. Gerar com `python -c "import secrets; print(secrets.token_urlsafe(32))"` e guardar como segredo. |
| `HUBSPOT_CLIENT_SECRET` | Client secret do aplicativo que assina os webhooks. É diferente de `HUBSPOT_ACCESS_TOKEN`. |
| `HUBSPOT_WEBHOOK_URL` | URL HTTPS pública exata cadastrada no aplicativo, incluindo query string e sua ordem, se houver. |
| `HUBSPOT_WEBHOOK_ALLOW_V1` | `false` por padrão; usar `true` somente para aplicativos que enviam exclusivamente assinatura v1. |
| `CORS_ORIGINS` | Origens administrativas autorizadas, separadas por vírgula. Sem wildcard; vazio desabilita acesso entre origens. |
| `DELIVERY_DB_PATH` | Arquivo SQLite em volume persistente. Compartilhado pela fila de entregas e pela nova fila de webhooks. |
| `HEALTH_POLLING_MAX_AGE_SECONDS` | Máximo sem ciclo bem-sucedido; padrão 300 segundos. Ajustar ao intervalo e duração real de um ciclo completo. |
| `HEALTH_DELIVERY_MAX_AGE_SECONDS` | Idade máxima de uma entrega ou webhook pendente; padrão 300 segundos. |

Use **uma instância e um worker**, preservando o volume já utilizado. O serviço
cria as novas tabelas/colunas automaticamente, sem apagar recibos existentes.
No Railway, configure as variáveis e o volume no próprio serviço; o Compose
não é usado pelo Railway. Nenhuma credencial nem configuração remota é alterada
pelo código. Sem configuração de segurança, as rotas correspondentes retornam
503 e a prontidão não fica verde.

## Autenticação das rotas

Todas as rotas de negócio, inclusive listagem, históricos, exclusões,
processamento manual, transferências, `/config`, `/test/chat` e `/admin/health`,
exigem `Authorization: Bearer <ADMIN_API_TOKEN>`. O token não deve ser incorporado
ao frontend ou enviado pela URL. Ausência ou divergência retorna 401.
A comparação usa tempo constante. Documentação e OpenAPI públicos estão desativados.

Somente `GET /`, `GET /health`, `GET /ready` e `POST /webhook/hubspot` dispensam o
token administrativo; o último exige a autenticação própria do HubSpot.

## Autenticidade, replay e erros do webhook

A assinatura v3 usa HMAC SHA-256, corpo bruto, método, URL pública configurada e
timestamp, com comparação em tempo constante. Requisições com timestamp mais
de cinco minutos no passado ou futuro são recusadas. Cabeçalhos arbitrários
`Host`/`X-Forwarded-*` não mudam a URL assinada.

O formato segue a [documentação oficial de validação do HubSpot](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/request-validation).
Algumas assinaturas de objetos CRM usam v1, conforme a
[documentação dos aplicativos legados](https://developers.hubspot.com/docs/apps/legacy-apps/authentication/validating-requests).
Para esse caso existe opt-in: SHA-256 do segredo mais corpo bruto, exigindo
`occurredAt` assinado com no máximo 24 horas, além da deduplicação em disco.
V1 não oferece a mesma janela de cinco minutos de v3. Uma v3 inválida nunca
cai para v1, mesmo quando os dois cabeçalhos estão presentes.

O lote inteiro é validado e gravado numa transação antes da confirmação HTTP:

| HTTP | Significado |
| --- | --- |
| 202 | Evento autenticado, validado e persistido para processamento. |
| 200 | Todos os eventos já foram aceitos; nenhuma duplicata foi enfileirada. |
| 400 / 422 | JSON inválido / estrutura do lote ou evento inválida. |
| 401 | Assinatura, URL ou timestamp inválido. |
| 413 | Corpo acima de 1 MiB. |
| 503 | Configuração ausente ou falha ao persistir; o remetente pode tentar novamente. |

No máximo 100 eventos por lote. A identidade usa o conteúdo canônico do evento
sem `attemptNumber`, permitindo reconhecer retries e lotes reorganizados.
Eventos pendentes sobrevivem a reinícios; falhas de processamento têm nova
tentativa após 30 segundos. Confirmações são conservadas por sete dias,
superando as janelas de replay aceitas. Falhas posteriores ao 202 ficam na fila,
aparecem nos logs e, quando persistem, tornam a prontidão indisponível.

## Paginação e troca de responsável

A busca mantém os três filtros em todas as páginas, segue `paging.next.after`
e remove IDs repetidos. Falhas em páginas posteriores, cursores repetidos ou
páginas inválidas não são tratados como consulta completa. O polling usa leitura
estrita e registra a falha. Limites/erros do provedor também produzem falha
explícita; não há truncamento silencioso. As requisições entre páginas são espaçadas.

Antes de cada parte, antes da observação interna e imediatamente antes da
transferência, o bot consulta pipeline, etapa e responsável. Uma mudança
confirmada encerra o rascunho anterior, preservando recibos e partes já enviadas.
Indisponibilidade da consulta suspende a entrega para uma tentativa posterior.
Isso também vale para entregas retomadas e envio manual. A transferência usa
um único PATCH para proprietário, pipeline e etapa.

Existe uma pequena janela entre a leitura do responsável e a chamada externa;
a implementação não oferece atomicidade entre operações independentes da API.

## Monitoramento

- `/health`: liveness, sem I/O externo. Continua responsivo durante chamadas lentas.
- `/ready`: 200 `ready` ou 503 `not_ready`, sem expor detalhes operacionais.
- `/admin/health`: causas da indisponibilidade, autenticado pelo token administrativo.

Um monitor independente executa sondas a cada 30 segundos. Ele confirma uma
gravação real no SQLite e um upsert com retorno confirmado no Supabase, na linha
reservada `salomao_sessions.session_id = whatsapp_salomao_healthcheck`. Essa
linha contém apenas um nonce de diagnóstico; não altera conversas de clientes.
A sonda comprova escrita nessa tabela com a credencial configurada, não em
todas as tabelas do projeto. Requer acesso de leitura e escrita nessa linha.

A prontidão considera configuração de segurança, sondas recentes (até 90s),
três ciclos consecutivos de polling com falha, tempo sem ciclo bem-sucedido,
entregas com confirmação incerta e idade de entregas/webhooks pendentes.
Polling desativado intencionalmente não produz alerta de polling parado.
Na inicialização, `/ready` fica em 503 até as primeiras verificações terminarem.

O log JSON emite `event=health.alert` com `reason` na mudança e a cada cinco
minutos enquanto a falha persiste. A recuperação emite `event=health.recovered`.
Configure o monitor externo para consultar `/ready` a cada 30 segundos e
notificar após três falhas consecutivas, ou use esses eventos no coletor de logs.
Não há envio de notificações a Slack/email nem criação automática de um monitor
externo. Docker/Compose consultam `/ready` no healthcheck. Para uma sonda de
reinício de processo use `/health`, evitando reiniciar por falha de fornecedor.

Todas as operações síncronas de HubSpot/Supabase das rotas assíncronas são
executadas em threads. As rotas de saúde respondem a partir do estado em memória,
sem esperar por esses serviços.

## Validação

Execute `python run_offline_tests.py` dentro de `backend` com as dependências de
`requirements-lock.txt`. O runner isola o SQLite em diretório temporário e bloqueia
a rede externa. Os testes incluem autenticação, adulteração e replay de webhooks,
retomada de fila, falha de persistência, paginação, mudança de responsável durante
envio/observação, sondas e responsividade do health check sob consultas lentas.
