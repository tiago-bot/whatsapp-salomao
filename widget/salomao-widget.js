(function() {
  'use strict';

  // Configuração padrão
  const CONFIG = {
    apiUrl: window.SALOMAO_API_URL || 'http://localhost:8000',
    position: window.SALOMAO_POSITION || 'bottom-right',
    primaryColor: window.SALOMAO_PRIMARY_COLOR || '#22c55e',
    title: window.SALOMAO_TITLE || 'Salomão',
    subtitle: window.SALOMAO_SUBTITLE || 'Online',
    placeholder: window.SALOMAO_PLACEHOLDER || 'Digite sua pergunta...',
    suggestions: window.SALOMAO_SUGGESTIONS || ['Como começar?', 'Ajuda técnica', 'Eventos']
  };

  // Estado do widget
  let state = {
    isOpen: false,
    isLoading: false,
    messages: [],
    sessionId: null
  };

  // Gerar ID de sessão único
  function generateSessionId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  // Criar estilos CSS
  function injectStyles() {
    const styles = `
      .salomao-widget * {
        box-sizing: border-box;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
      }

      .salomao-widget-button {
        position: fixed;
        bottom: 20px;
        right: 20px;
        width: 60px;
        height: 60px;
        border-radius: 50%;
        background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
        border: 2px solid ${CONFIG.primaryColor};
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        transition: all 0.3s ease;
        z-index: 999998;
      }

      .salomao-widget-button:hover {
        transform: scale(1.1);
        box-shadow: 0 6px 25px rgba(34, 197, 94, 0.4);
      }

      .salomao-widget-button-icon {
        width: 32px;
        height: 32px;
        background: ${CONFIG.primaryColor};
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        color: white;
        font-size: 18px;
      }

      .salomao-widget-container {
        position: fixed;
        bottom: 90px;
        right: 20px;
        width: 380px;
        height: 520px;
        background: #1a1a1a;
        border-radius: 16px;
        box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
        display: none;
        flex-direction: column;
        overflow: hidden;
        z-index: 999999;
        border: 1px solid #333;
      }

      .salomao-widget-container.open {
        display: flex;
        animation: salomaoSlideIn 0.3s ease;
      }

      @keyframes salomaoSlideIn {
        from {
          opacity: 0;
          transform: translateY(20px) scale(0.95);
        }
        to {
          opacity: 1;
          transform: translateY(0) scale(1);
        }
      }

      .salomao-widget-header {
        background: linear-gradient(135deg, #1f1f1f 0%, #2a2a2a 100%);
        padding: 16px;
        display: flex;
        align-items: center;
        gap: 12px;
        border-bottom: 1px solid #333;
      }

      .salomao-widget-avatar {
        width: 40px;
        height: 40px;
        background: ${CONFIG.primaryColor};
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        color: white;
        font-size: 20px;
      }

      .salomao-widget-info {
        flex: 1;
      }

      .salomao-widget-title {
        color: white;
        font-weight: 600;
        font-size: 16px;
        margin: 0;
      }

      .salomao-widget-status {
        color: ${CONFIG.primaryColor};
        font-size: 12px;
        display: flex;
        align-items: center;
        gap: 4px;
      }

      .salomao-widget-status::before {
        content: '';
        width: 8px;
        height: 8px;
        background: ${CONFIG.primaryColor};
        border-radius: 50%;
      }

      .salomao-widget-close {
        background: none;
        border: none;
        color: #888;
        cursor: pointer;
        padding: 8px;
        border-radius: 8px;
        transition: all 0.2s;
      }

      .salomao-widget-close:hover {
        background: #333;
        color: white;
      }

      .salomao-widget-messages {
        flex: 1;
        overflow-y: auto;
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }

      .salomao-widget-messages::-webkit-scrollbar {
        width: 6px;
      }

      .salomao-widget-messages::-webkit-scrollbar-track {
        background: #2a2a2a;
      }

      .salomao-widget-messages::-webkit-scrollbar-thumb {
        background: #444;
        border-radius: 3px;
      }

      .salomao-widget-message {
        max-width: 85%;
        padding: 12px 16px;
        border-radius: 16px;
        font-size: 14px;
        line-height: 1.5;
        animation: salomaoMessageIn 0.3s ease;
      }

      @keyframes salomaoMessageIn {
        from {
          opacity: 0;
          transform: translateY(10px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      .salomao-widget-message.user {
        background: ${CONFIG.primaryColor};
        color: white;
        align-self: flex-end;
        border-bottom-right-radius: 4px;
      }

      .salomao-widget-message.assistant {
        background: #2a2a2a;
        color: #e5e5e5;
        align-self: flex-start;
        border-bottom-left-radius: 4px;
      }

      .salomao-widget-message.assistant strong {
        color: ${CONFIG.primaryColor};
      }

      .salomao-widget-rating {
        display: flex;
        gap: 4px;
        margin-top: 8px;
      }

      .salomao-widget-rating button {
        background: #333;
        border: none;
        padding: 4px 8px;
        border-radius: 4px;
        cursor: pointer;
        color: #888;
        font-size: 12px;
        transition: all 0.2s;
      }

      .salomao-widget-rating button:hover {
        background: #444;
        color: white;
      }

      .salomao-widget-rating button.active {
        background: ${CONFIG.primaryColor};
        color: white;
      }

      .salomao-widget-typing {
        display: flex;
        gap: 4px;
        padding: 12px 16px;
        background: #2a2a2a;
        border-radius: 16px;
        align-self: flex-start;
        border-bottom-left-radius: 4px;
      }

      .salomao-widget-typing span {
        width: 8px;
        height: 8px;
        background: #666;
        border-radius: 50%;
        animation: salomaoTyping 1.4s infinite ease-in-out;
      }

      .salomao-widget-typing span:nth-child(2) {
        animation-delay: 0.2s;
      }

      .salomao-widget-typing span:nth-child(3) {
        animation-delay: 0.4s;
      }

      @keyframes salomaoTyping {
        0%, 60%, 100% {
          transform: translateY(0);
          opacity: 0.4;
        }
        30% {
          transform: translateY(-4px);
          opacity: 1;
        }
      }

      .salomao-widget-suggestions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        padding: 8px 16px;
        border-top: 1px solid #333;
        background: #1f1f1f;
      }

      .salomao-widget-suggestion {
        background: #2a2a2a;
        border: 1px solid #444;
        color: #ccc;
        padding: 8px 14px;
        border-radius: 20px;
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
      }

      .salomao-widget-suggestion:hover {
        background: ${CONFIG.primaryColor};
        border-color: ${CONFIG.primaryColor};
        color: white;
      }

      .salomao-widget-input-area {
        padding: 12px 16px;
        border-top: 1px solid #333;
        background: #1f1f1f;
        display: flex;
        gap: 8px;
        align-items: center;
      }

      .salomao-widget-input {
        flex: 1;
        background: #2a2a2a;
        border: 1px solid #444;
        border-radius: 24px;
        padding: 12px 18px;
        color: white;
        font-size: 14px;
        outline: none;
        transition: border-color 0.2s;
      }

      .salomao-widget-input:focus {
        border-color: ${CONFIG.primaryColor};
      }

      .salomao-widget-input::placeholder {
        color: #666;
      }

      .salomao-widget-send {
        width: 44px;
        height: 44px;
        background: ${CONFIG.primaryColor};
        border: none;
        border-radius: 50%;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s;
      }

      .salomao-widget-send:hover {
        background: #16a34a;
        transform: scale(1.05);
      }

      .salomao-widget-send:disabled {
        background: #444;
        cursor: not-allowed;
        transform: none;
      }

      .salomao-widget-send svg {
        width: 20px;
        height: 20px;
        fill: white;
      }

      .salomao-widget-welcome {
        text-align: center;
        padding: 20px;
        color: #888;
      }

      .salomao-widget-welcome-icon {
        width: 60px;
        height: 60px;
        background: ${CONFIG.primaryColor};
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 28px;
        color: white;
        margin: 0 auto 16px;
      }

      .salomao-widget-welcome h3 {
        color: white;
        margin: 0 0 8px;
        font-size: 18px;
      }

      .salomao-widget-welcome p {
        margin: 0;
        font-size: 14px;
        line-height: 1.5;
      }

      @media (max-width: 480px) {
        .salomao-widget-container {
          width: calc(100% - 20px);
          height: calc(100% - 100px);
          bottom: 80px;
          right: 10px;
          left: 10px;
          border-radius: 12px;
        }
      }
    `;

    const styleElement = document.createElement('style');
    styleElement.textContent = styles;
    document.head.appendChild(styleElement);
  }

  // Criar estrutura HTML
  function createWidget() {
    const container = document.createElement('div');
    container.className = 'salomao-widget';
    container.innerHTML = `
      <button class="salomao-widget-button" aria-label="Abrir chat">
        <div class="salomao-widget-button-icon">S</div>
      </button>

      <div class="salomao-widget-container">
        <div class="salomao-widget-header">
          <div class="salomao-widget-avatar">S</div>
          <div class="salomao-widget-info">
            <h4 class="salomao-widget-title">${CONFIG.title}</h4>
            <div class="salomao-widget-status">${CONFIG.subtitle}</div>
          </div>
          <button class="salomao-widget-close" aria-label="Fechar chat">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>

        <div class="salomao-widget-messages" id="salomao-messages">
          <div class="salomao-widget-welcome">
            <div class="salomao-widget-welcome-icon">👋</div>
            <h3>Olá! Eu sou o Salomão</h3>
            <p>Seu assistente virtual da inChurch.<br>Como posso ajudar você hoje?</p>
          </div>
        </div>

        <div class="salomao-widget-suggestions" id="salomao-suggestions">
          ${CONFIG.suggestions.map(s => `<button class="salomao-widget-suggestion">${s}</button>`).join('')}
        </div>

        <div class="salomao-widget-input-area">
          <input
            type="text"
            class="salomao-widget-input"
            id="salomao-input"
            placeholder="${CONFIG.placeholder}"
            autocomplete="off"
          >
          <button class="salomao-widget-send" id="salomao-send" aria-label="Enviar mensagem">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
            </svg>
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(container);
    return container;
  }

  // Formatar mensagem (markdown básico)
  function formatMessage(content) {
    const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
    const links = [];
    let text = escape(content).replace(/TRANSFERIR_SUPORTE|&lt;REQUIRES_ESCALATION&gt;/g, '');
    text = text.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, href) => {
      const token = '\uE000' + links.length + '\uE001';
      links.push('<a href="' + href + '" target="_blank" rel="noopener noreferrer" style="color:#1d4ed8;text-decoration:underline;overflow-wrap:anywhere">' + label + '</a>');
      return token;
    });
    text = text
      .replace(/^#{1,6}[ \t]+(.+)$/gm, '<strong>$1</strong>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s.,!?:;)])/g, '$1<strong>$2</strong>')
      .replace(/^&gt;[ \t]?(.*)$/gm, '<span style="display:block;border-left:3px solid #cbd5e1;padding-left:12px;color:#475569">$1</span>')
      .replace(/\n/g, '<br>');
    links.forEach((link, index) => { text = text.replace('\uE000' + index + '\uE001', link); });
    return text;
  }

  // Adicionar mensagem ao chat
  function addMessage(role, content, messageId = null) {
    const messagesContainer = document.getElementById('salomao-messages');
    const welcome = messagesContainer.querySelector('.salomao-widget-welcome');
    if (welcome) welcome.remove();

    const messageDiv = document.createElement('div');
    messageDiv.className = `salomao-widget-message ${role}`;
    messageDiv.innerHTML = formatMessage(content);

    if (role === 'assistant' && messageId) {
      messageDiv.dataset.messageId = messageId;
      const ratingDiv = document.createElement('div');
      ratingDiv.className = 'salomao-widget-rating';
      ratingDiv.innerHTML = `
        <button data-rating="like" title="Útil">👍</button>
        <button data-rating="dislike" title="Não útil">👎</button>
      `;
      messageDiv.appendChild(ratingDiv);

      ratingDiv.querySelectorAll('button').forEach(btn => {
        btn.addEventListener('click', () => rateMessage(messageId, btn.dataset.rating, ratingDiv));
      });
    }

    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    state.messages.push({ role, content, messageId });
  }

  // Mostrar indicador de digitação
  function showTyping() {
    const messagesContainer = document.getElementById('salomao-messages');
    const typingDiv = document.createElement('div');
    typingDiv.className = 'salomao-widget-typing';
    typingDiv.id = 'salomao-typing';
    typingDiv.innerHTML = '<span></span><span></span><span></span>';
    messagesContainer.appendChild(typingDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  // Esconder indicador de digitação
  function hideTyping() {
    const typing = document.getElementById('salomao-typing');
    if (typing) typing.remove();
  }

  // Enviar mensagem para API
  async function sendMessage(message) {
    if (!message.trim() || state.isLoading) return;

    addMessage('user', message);
    state.isLoading = true;
    showTyping();

    const input = document.getElementById('salomao-input');
    const sendBtn = document.getElementById('salomao-send');
    input.value = '';
    sendBtn.disabled = true;

    try {
      const response = await fetch(`${CONFIG.apiUrl}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: message,
          session_id: state.sessionId
        })
      });

      const data = await response.json();
      hideTyping();

      if (data.success) {
        addMessage('assistant', data.response, data.message_id);

        if (data.transfer_requested) {
          setTimeout(() => showFeedbackModal(), 1000);
        }
      } else {
        addMessage('assistant', 'Desculpe, ocorreu um erro. Tente novamente.');
      }
    } catch (error) {
      console.error('Salomão Widget Error:', error);
      hideTyping();
      addMessage('assistant', 'Desculpe, não foi possível conectar. Verifique sua conexão.');
    } finally {
      state.isLoading = false;
      sendBtn.disabled = false;
    }
  }

  // Avaliar mensagem
  async function rateMessage(messageId, rating, ratingDiv) {
    try {
      await fetch(`${CONFIG.apiUrl}/rating/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message_id: messageId,
          session_id: state.sessionId,
          rating: rating
        })
      });

      ratingDiv.querySelectorAll('button').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.rating === rating) {
          btn.classList.add('active');
        }
      });
    } catch (error) {
      console.error('Erro ao avaliar:', error);
    }
  }

  // Modal de feedback (simplificado)
  function showFeedbackModal() {
    const modal = document.createElement('div');
    modal.className = 'salomao-widget-message assistant';
    modal.innerHTML = `
      <p><strong>Como foi o atendimento?</strong></p>
      <p style="font-size: 12px; color: #888; margin-top: 4px;">Sua opinião nos ajuda a melhorar!</p>
      <div style="display: flex; gap: 4px; margin-top: 8px;">
        ${[1,2,3,4,5].map(n => `<button class="salomao-widget-suggestion" data-star="${n}" style="padding: 8px 12px;">⭐ ${n}</button>`).join('')}
      </div>
    `;

    const messagesContainer = document.getElementById('salomao-messages');
    messagesContainer.appendChild(modal);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    modal.querySelectorAll('[data-star]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const rating = parseInt(btn.dataset.star);
        try {
          await fetch(`${CONFIG.apiUrl}/rating/session`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              session_id: state.sessionId,
              rating: rating,
              transfer_requested: true
            })
          });
          modal.innerHTML = '<p>✅ Obrigado pela avaliação!</p>';
        } catch (e) {
          console.error('Erro ao enviar feedback:', e);
        }
      });
    });
  }

  // Toggle widget
  function toggleWidget() {
    state.isOpen = !state.isOpen;
    const container = document.querySelector('.salomao-widget-container');
    container.classList.toggle('open', state.isOpen);
  }

  // Inicializar widget
  function init() {
    state.sessionId = generateSessionId();
    injectStyles();
    const widget = createWidget();

    // Event listeners
    widget.querySelector('.salomao-widget-button').addEventListener('click', toggleWidget);
    widget.querySelector('.salomao-widget-close').addEventListener('click', toggleWidget);

    const input = document.getElementById('salomao-input');
    const sendBtn = document.getElementById('salomao-send');

    sendBtn.addEventListener('click', () => sendMessage(input.value));
    input.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') sendMessage(input.value);
    });

    // Sugestões
    document.querySelectorAll('.salomao-widget-suggestion').forEach(btn => {
      btn.addEventListener('click', () => {
        sendMessage(btn.textContent);
      });
    });

    console.log('🤖 Salomão Widget inicializado');
  }

  // Iniciar quando DOM estiver pronto
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
