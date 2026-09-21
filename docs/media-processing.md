# Leitura de imagens e áudios

## Imagens: somente telas da inChurch

Antes de responder sobre um print, o serviço verifica o arquivo e consulta um
classificador dedicado apenas à identidade da interface. A imagem só segue para
o atendimento quando a decisão é `inchurch`, a confiança é de pelo menos 0,9 e
há evidência de domínio oficial ou marca integrada à interface. A legenda e o
histórico não são enviados a esse classificador e não liberam a imagem.

Telas genéricas de igreja, fotos, documentos, conversas e capturas sem
identificação não autorizam a leitura de conteúdo. Em dúvida, saída inválida ou
falha do classificador, o atendimento pede outro print com identificação visível.
Esse reconhecimento visual depende do modelo; não autentica a origem de uma
captura nem garante detectar uma montagem sofisticada.

PNG, JPEG, WebP e GIF estático são verificados pelos bytes, limitados a 20 MiB e
20 milhões de pixels. Arquivos animados são recusados. A orientação EXIF é
corrigida e os metadados são removidos. A normalização preserva as dimensões; os
mesmos bytes aprovados chegam ao atendimento em alta resolução. As instruções
proíbem inventar texto ilegível ou obedecer a comandos embutidos na imagem.

## Áudios

O serviço identifica o contêiner pelos bytes, corrige divergências de extensão
e converte o áudio para WAV PCM mono de 16 kHz antes de transcrever. OGG/Opus do
WhatsApp, nomes `.ptt`/`.oga`, M4A, MP3, WAV, WebM e FLAC são contemplados.
É necessário `ffmpeg` no PATH; o Dockerfile já o instala.

Limites: 20 MiB por arquivo, até 10 minutos, conversão com prazo de 45 segundos
e chamada de transcrição com timeout de 60 segundos e uma repetição para erros
transitórios. O áudio é rejeitado se exceder a duração; não é transcrito apenas
um trecho sem avisar. Arquivos vazios, corrompidos, praticamente silenciosos e
respostas de transcrição vazias não chegam ao atendimento. O detector de silêncio
não é um detector de fala: ruído e fala muito difícil ainda dependem do modelo.

O padrão continua `TRANSCRIPTION_MODEL=gpt-transcribe`, com `languages=["pt"]`
e uma dica curta de grafia (`inChurch`). Modelos anteriores usam `language="pt"`;
diarização usa `chunking_strategy="auto"`, sem `prompt`. Referência:
[documentação de transcrição da OpenAI](https://developers.openai.com/api/docs/guides/speech-to-text).

A transcrição preserva a fala recebida, acompanha a legenda e passa pela política
de escopo do texto. Os arquivos temporários são removidos tanto em sucesso quanto
em erro. Os logs registram motivos técnicos sem o conteúdo do áudio ou as imagens.

## Downloads e API

Downloads do HubSpot têm limite durante a leitura e até três redirecionamentos.
Cada destino precisa ser HTTPS em um domínio HubSpot permitido. Credenciais são
enviadas apenas a `api.hubapi.com`, nunca ao CDN. URLs não confiáveis são recusadas.
MIME com parâmetros e letras maiúsculas é normalizado.

`/chat` aceita uma mensagem somente com mídia e base64 puro ou data URL.
`/chat/upload` limita a leitura e retorna 413 para excesso de tamanho e 422 para
arquivo vazio. Falhas de mídia retornam mensagem ao cliente e não iniciam a
geração de orientações. O processamento bloqueante roda fora do loop da API.

A política de entrega passa a `2026-09-21-media-v3`; respostas pendentes aprovadas
sob a política antiga deixam de estar autorizadas para envio automático.

## Validação

Execute `python run_offline_tests.py` dentro de `backend`, com as dependências
de `requirements-lock.txt` instaladas. A suíte bloqueia rede externa e não envia
mensagens a clientes. `test_media_processing.py` cobre validação dos arquivos,
aprovação e bloqueio do escopo, parâmetros de transcrição, limpeza, erros,
downloads e contratos da API. Com FFmpeg no PATH, também executa conversões reais
de OGG/Opus, WebM, M4A, MP3 e FLAC gerados localmente; sem ele, esse teste é omitido.

As respostas dos modelos são simuladas nesses testes. Para avaliar a precisão
visual e de fala em homologação, use prints autorizados da inChurch, exemplos
externos, prints recortados e áudios representativos com ruído e sotaques. Essa
avaliação com os serviços reais não é substituída pela suíte offline.
