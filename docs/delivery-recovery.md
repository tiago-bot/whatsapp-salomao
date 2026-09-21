# Persistência e recuperação de entregas

## Armazenamento

O Docker Compose monta o volume nomeado `salomao-delivery-data` em `/data` e fixa
`DELIVERY_DB_PATH=/data/salomao-delivery.sqlite3` no serviço `salomao-bot`.
A definição em `environment` prevalece sobre `.env`. O volume guarda recibos,
tentativas, entradas pendentes, checkpoints de leitura, contexto e auditoria.
As travas entre processos ficam ao lado do banco, no mesmo volume.

Recriar o contêiner reutiliza esse volume. Execute uma instância/worker e mantenha
o mesmo volume em todos os reinícios. `docker compose down -v` remove os dados;
não usar essa opção em produção. Um volume vazio não recupera recibos anteriores.

### Migração de uma instalação sem volume

Antes de recriar o contêiner antigo:

1. Suspenda o processamento (polling e entrada de webhooks) e pare o worker.
   Mantenha o contêiner antigo até concluir o backup.
2. Identifique o caminho efetivo de `DELIVERY_DB_PATH`; sem a variável, o padrão
   no contêiner é `/app/.local/delivery.sqlite3`.
3. Preserve o diretório completo do banco parado, incluindo arquivos auxiliares
   de SQLite, e faça uma cópia de segurança consistente. Se precisar copiar com
   o processo ativo, utilize `sqlite3.Connection.backup`, nunca apenas o arquivo
   principal enquanto houver gravações.
4. Coloque a cópia consistente em `/data/salomao-delivery.sqlite3` no volume novo,
   conferindo permissões, `PRAGMA quick_check` e contagens de recibos antes de
   iniciar a nova versão. A inicialização cria tabelas novas sem apagar recibos.
5. Inicie uma única instância. Confira o caminho e a montagem efetivos, o audit
   abaixo e as mesmas contagens após uma recriação controlada do contêiner.

### Verificação de produção em 21/09/2026

Consulta somente de leitura pelo CLI autenticado do Railway:

| Item | Valor observado |
| --- | --- |
| Projeto / ambiente / serviço | `gleaming-purpose` / `production` / `whatsapp-salomao` |
| Volume | `whatsapp-salomao-volume`, estado `READY` |
| Montagem | `/data` |
| `DELIVERY_DB_PATH` | `/data/salomao-delivery.sqlite3` |
| Deployment ativo | `95ecbcaa-e63d-498a-8596-d9b69d802fa6`, `SUCCESS`, montagem `/data` |
| Réplicas | 1 |

A configuração persistente **já existe em produção**. Railway usa Dockerfile e
`railway.json`; o Compose local não altera o serviço hospedado.
Não foi possível executar `quick_check` nem inspecionar o arquivo no contêiner:
o acesso SSH foi recusado por ausência de chave local. Não foi feito deploy,
alteração de variáveis, reinício ou reconciliação de dados reais nesta revisão.
O Docker local também está sem daemon; recriação real de contêiner não foi testada.

No shell do contêiner, a conferência deve incluir:

```sh
python -c 'import os; from pathlib import Path; p=Path(os.environ["DELIVERY_DB_PATH"]).resolve(); print(p, p.is_file(), os.path.ismount("/data")); assert p.is_relative_to(Path("/data")) and p.is_file() and os.path.ismount("/data")'
python -c 'import os,sqlite3; from pathlib import Path; c=sqlite3.connect(Path(os.environ["DELIVERY_DB_PATH"]).resolve().as_uri()+"?mode=ro",uri=True); print(c.execute("PRAGMA quick_check").fetchone()[0]); c.close()'
```

## Entradas que aguardam

A primeira leitura de uma conversa admite apenas a janela recente de cinco
minutos. Leituras seguintes retomam do último checkpoint, com sobreposição de
cinco segundos para diferenças de horário. A coleta acontece antes de retomar
uma entrega bloqueada, sem permitir novos envios enquanto a incerteza existir.

As mensagens admitidas, incluindo metadados de anexos, são gravadas em
`inbound_pending`. Não expiram quando ficam antigas, quando desaparecem da
página recente ou quando o processo reinicia. A geração de uma resposta só
retira essas entradas na mesma transação que grava a entrega e os IDs agrupados.
O cache de contexto de 100 mensagens não limita essa fila.

Se já existe uma entrega pendente da versão anterior, a primeira coleta pode
retomar do horário da tentativa documentada. A reconciliação também preserva
esse marco caso ocorra antes da primeira coleta da versão nova. Históricos
anteriores sem essa evidência continuam excluídos; restauração da memória não
se transforma em autorização para reenviar.

A leitura percorre páginas até o marco conhecido e mantém as páginas antigas
fora da seleção de novas entradas. Falha de página ou limite de 100 páginas
interrompe o ciclo sem avançar o checkpoint. É preciso resolver a falha de
leitura para retomar; um resultado parcial não é tratado como histórico completo.

## Reconciliação com recibo encontrado

Execute dentro do contêiner atualizado, com as credenciais usuais do HubSpot e
o mesmo banco/volume do worker. O worker atualizado deve ter iniciado pelo menos
uma vez para criar o esquema de auditoria. O acesso ao shell/volume é a fronteira
de autorização; não há endpoint público de reconciliação. `--operator` é uma
identificação informada pelo operador, não um login autenticado pela aplicação.

1. Consulte as entregas retidas e localize no HubSpot a mensagem ou nota exata:

   ```sh
   python delivery_audit.py --db /data/salomao-delivery.sqlite3
   python delivery_audit.py --db /data/salomao-delivery.sqlite3 --inputs
   ```

2. Solicite uma prévia. `--message` é o ID da entrada que gerou a resposta;
   `--remote-id` é o ID da mensagem enviada encontrada no HubSpot. Na CLI,
   `--part` começa em **1**; o campo `part` do audit bruto começa em **0**.

   ```sh
   python delivery_reconcile.py --db /data/salomao-delivery.sqlite3 \
     --thread THREAD_ID --message INPUT_ID --part 1 --remote-id REMOTE_ID \
     --operator "nome-do-operador" --reason "Incidente 42: recibo conferido no HubSpot"
   ```

   A operação consulta por GET o ID remoto na conversa exata. Exige uma mensagem
   de saída do actor do Salomão, texto idêntico à parte armazenada e horário
   compatível com a tentativa (tolerância de cinco segundos). Rejeita recibo
   reutilizado, parte fora de ordem, entrega em quarentena e operação concorrente.
   A prévia não altera o banco. Recibo ausente ou incompatível mantém o bloqueio.

3. Confira a prévia e repita o comando acrescentando `--apply`. A transação grava
   recibo, avanço das partes, contexto entregue e auditoria juntos. Uma falha
   desfaz tudo. A última parte encerra a entrega apenas se não houver transferência
   humana pendente. Partes restantes e transferências seguem o fluxo normal no
   próximo polling/webhook, com nova verificação de elegibilidade do ticket.
   Repetir a mesma reconciliação retorna `already_reconciled`, sem duplicar auditoria.

Para uma **nota de transferência incerta**, use `--note --ticket TICKET_ID` em
vez de `--part 1`. A ferramenta verifica a associação conversa–ticket, a
associação nota–ticket e o conteúdo exato da nota persistida. Exemplo de prévia:

```sh
python delivery_reconcile.py --db /data/salomao-delivery.sqlite3 \
  --thread THREAD_ID --message INPUT_ID --note --ticket TICKET_ID \
  --remote-id NOTE_ID --operator "nome-do-operador" --reason "Incidente 42: nota encontrada"
```

Após aplicar, a nota fica confirmada; o próximo ciclo tenta somente a transferência,
sem repetir o POST da nota nem a resposta já confirmada.

Confira a trilha e as pendências restantes:

```sh
python delivery_audit.py --db /data/salomao-delivery.sqlite3 --reconciliations
python delivery_audit.py --db /data/salomao-delivery.sqlite3
```

A trilha guarda operador, motivo, horário UTC, estado anterior, conversa,
entrada, parte/tipo, ID remoto e hash do conteúdo verificado. Não grava credenciais
nem repete o texto do cliente. A reconciliação não envia mensagens ao HubSpot.

Não existe opção para presumir falha e reenviar. Ausência na tela recente não
prova ausência de envio. Registros legados com horário de tentativa reconstruído
depois do recibo ou sem aprovação válida podem ser recusados; precisam de revisão
específica, sem apagar registros ou forçar contadores manualmente.

Referências de consulta: [mensagens por ID no HubSpot](https://developers.hubspot.com/docs/api-reference/legacy/conversations/guide)
e [notas e associações](https://developers.hubspot.com/docs/api-reference/legacy/crm/activities/notes/guide).

## Validação da alteração

`run_offline_tests.py`: **163 testes passaram**, com rede externa bloqueada.
Cobrem reinício, entradas antigas retidas, anexos, paginação, falha de leitura,
agrupamento, reconciliação antes da primeira coleta, partes restantes, notas,
prévia da CLI, concorrência, recibos incompatíveis/reutilizados e rollback da
transação inteira. `docker compose config` confirmou o caminho e a montagem
de volume; não substitui o teste de recriação real mencionado acima.
