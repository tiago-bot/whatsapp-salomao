# Salomão Widget - Guia de Integração

Widget de chat embedável para integrar o assistente Salomão em qualquer site.

## 🚀 Integração Rápida

### 1. Adicione o script ao seu site

```html
<!-- Antes de </body> -->
<script>
  window.SALOMAO_API_URL = 'https://sua-api.com';
</script>
<script src="https://cdn.seusite.com/salomao-widget.js"></script>
```

### 2. Configurações Opcionais

```html
<script>
  // URL da API do Salomão (obrigatório em produção)
  window.SALOMAO_API_URL = 'https://api.inchurch.com.br';

  // Cor principal (padrão: verde #22c55e)
  window.SALOMAO_PRIMARY_COLOR = '#22c55e';

  // Título do widget
  window.SALOMAO_TITLE = 'Salomão';

  // Subtítulo/status
  window.SALOMAO_SUBTITLE = 'Online';

  // Placeholder do input
  window.SALOMAO_PLACEHOLDER = 'Digite sua pergunta...';

  // Sugestões de perguntas
  window.SALOMAO_SUGGESTIONS = [
    'Como começar?',
    'Ajuda técnica',
    'Eventos'
  ];
</script>
```

## 📁 Arquivos

- `salomao-widget.js` - Widget principal (inclua no seu site)
- `index.html` - Página de demonstração

## 🧪 Testar Localmente

1. Inicie o backend do Salomão:
```bash
cd ../backend
python main.py
```

2. Sirva o widget (qualquer servidor HTTP):
```bash
cd ../widget
python -m http.server 8080
```

3. Acesse http://localhost:8080

## 🔧 API Endpoints Utilizados

O widget usa os seguintes endpoints:

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/chat` | POST | Enviar mensagem |
| `/rating/message` | POST | Avaliar mensagem (like/dislike) |
| `/rating/session` | POST | Avaliação final do atendimento |

## 📱 Responsivo

O widget é totalmente responsivo e se adapta automaticamente a dispositivos móveis.

## 🎨 Personalização Avançada

Para personalização mais profunda, você pode modificar o arquivo `salomao-widget.js` diretamente, alterando os estilos CSS na função `injectStyles()`.

## 🔒 CORS

Certifique-se de que o backend do Salomão permite requisições do domínio onde o widget será hospedado.

```python
# main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://centraldeajuda.inchurch.com.br"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## 📊 Analytics

O widget envia automaticamente:
- Avaliações de mensagens individuais (like/dislike)
- Avaliação final do atendimento (1-5 estrelas)
- Session ID para rastreamento de conversas
