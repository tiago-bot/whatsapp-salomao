# Imagem e áudio no WhatsApp — avaliação inicial

Existe implementação parcial, mas não foi homologada ponta a ponta no WhatsApp.
Não foi feito deploy nem ativada uma nova integração multimodal nesta revisão.

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

Existe transcrição via `whisper-1`. O caminho atual reconhece OGA/PTT/Opus como
OGG, mas **trocar extensão não converte o codec**. O container atual não inclui
FFmpeg. A documentação de transcrição lista MP3, MP4, MPEG, MPGA, M4A, WAV e WebM;
não se deve assumir que o formato de voz recebido do WhatsApp será aceito.
[Transcrição de arquivos — OpenAI](https://developers.openai.com/api/docs/guides/speech-to-text).

Próximos passos:

- Confirmar formato do áudio real e converter quando necessário, com FFmpeg
  isolado, timeout, limite de duração/tamanho e limpeza segura de temporários.
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
