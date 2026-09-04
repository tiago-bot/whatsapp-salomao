# Imagem e áudio no WhatsApp — avaliação inicial

Existe implementação funcional no backend e cobertura isolada, mas ainda falta a
homologação ponta a ponta após o próximo deploy no WhatsApp.

## Imagens

Hoje o parser reconhece anexos por tipo/MIME/extensão; o backend baixa até 20 MB
de hosts HubSpot permitidos e envia uma imagem ao agente. A chave do HubSpot não
é enviada ao CDN. Redirecionamentos são recusados e a saída passa pelo guardrail.

Faltam antes de homologar:

- Capturar um anexo real do canal e verificar se vem como URL, fileId ou URL
  autenticada/temporária; resolver IDs e redirecionamentos com allowlist por salto.
- Validar conteúdo binário, dimensões e MIME real, não só metadados/extensão.
- Registrar descrição visual/OCR associada ao ID do anexo, para continuar depois
  do print sem ter de reinterpretar a imagem ou perder suas informações.
- Testar print de tela recortado, imagem ilegível, múltiplos anexos, arquivo
  expirado e prompt injection contido na imagem. Pedir esclarecimento sem fingir
  que enxergou algo ausente. Não ampliar o escopo para curiosidades externas.

O modelo configurado aceita imagem. Isso não comprova que a cadeia de anexos do
HubSpot esteja pronta. [Entradas de imagem — OpenAI](https://developers.openai.com/api/docs/guides/images-vision).

## Áudio

Existe transcrição via `gpt-transcribe`. O parser reconhece MP3, MP4, MPEG, MPGA,
M4A, WAV e WebM pelo MIME, tipo ou nome/URL do arquivo. O caso real do HubSpot em
que um M4A chega como `type=FILE` está coberto por regressão. O container inclui
FFmpeg e converte OGG/Opus para WAV antes da transcrição.
[Transcrição de arquivos — OpenAI](https://developers.openai.com/api/docs/guides/speech-to-text).

Próximos passos:

- Homologar um áudio real após o deploy e confirmar a transcrição e a resposta
  contextual no ticket, sem conservar a mídia temporária.
- Cachear a transcrição por conversa + ID de mensagem/anexo + versão do modelo,
  evitando custo repetido e mantendo legenda e transcrição nos acompanhamentos.
- Persistir transcrição enriquecida separada do texto original: uma nova leitura
  do HubSpot não pode sobrescrever a transcrição com a legenda vazia ou parcial.
- Submeter legenda **e** transcrição ao mesmo controle de escopo. Esta revisão
  já impede que uma legenda substitua o conteúdo falado nessa classificação.
- Testar silêncio, ruído, fala truncada, português, nomes de módulos e conteúdo
  externo. Informar dificuldade de compreensão sem inventar uma transcrição.

## Entrega e segurança

Download/transcrição devem acontecer uma vez por entrada lógica; toda resposta
de imagem/áudio deve passar pela mesma fila e registro de tentativas do texto.
Anexo não pode contornar idempotência, validação de escopo ou filtros do ticket.
Não tornar arquivos públicos, expor URLs assinadas em logs nem conservar mídia
bruta indefinidamente. Credenciais e mudanças de provedor/modelo exigem avaliação
específica; os modelos atuais não foram trocados aqui.

Ver [Conversations API — HubSpot](https://developers.hubspot.com/docs/api-reference/legacy/conversations/guide)
para os contratos de mensagens e anexos.
