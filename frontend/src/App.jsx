import React, { useState, useRef, useEffect } from 'react'
import { formatMessage } from './formatMessage.mjs'
import {
  Send,
  Image,
  Mic,
  MicOff,
  Trash2,
  Bot,
  User,
  Loader2,
  X,
  Volume2,
  ThumbsUp,
  ThumbsDown,
  Star
} from 'lucide-react'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function App() {
  const [messages, setMessages] = useState([])
  const [inputMessage, setInputMessage] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  const [selectedImage, setSelectedImage] = useState(null)
  const [imagePreview, setImagePreview] = useState(null)
  const [isRecording, setIsRecording] = useState(false)
  const [audioBlob, setAudioBlob] = useState(null)
  const [showFeedbackModal, setShowFeedbackModal] = useState(false)
  const [feedbackRating, setFeedbackRating] = useState(0)
  const [feedbackComment, setFeedbackComment] = useState('')
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false)

  const messagesEndRef = useRef(null)
  const fileInputRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const audioChunksRef = useRef([])

  useEffect(() => {
    createNewSession()
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const createNewSession = async () => {
    try {
      const response = await fetch(`${API_URL}/session/new`, {
        method: 'POST'
      })
      const data = await response.json()
      setSessionId(data.session_id)
      setMessages([])
    } catch (error) {
      console.error('Erro ao criar sessão:', error)
      setSessionId(crypto.randomUUID())
    }
  }

  const sendMessage = async () => {
    if (!inputMessage.trim() && !selectedImage && !audioBlob) return

    const userMessage = {
      role: 'user',
      content: inputMessage,
      image: imagePreview,
      hasAudio: !!audioBlob,
      timestamp: new Date().toISOString()
    }

    setMessages(prev => [...prev, userMessage])
    setIsLoading(true)

    try {
      let imageBase64 = null
      let audioBase64 = null

      if (selectedImage) {
        const reader = new FileReader()
        imageBase64 = await new Promise((resolve) => {
          reader.onload = (e) => {
            const base64 = e.target.result.split(',')[1]
            resolve(base64)
          }
          reader.readAsDataURL(selectedImage)
        })
      }

      if (audioBlob) {
        const reader = new FileReader()
        audioBase64 = await new Promise((resolve) => {
          reader.onload = (e) => {
            const base64 = e.target.result.split(',')[1]
            resolve(base64)
          }
          reader.readAsDataURL(audioBlob)
        })
      }

      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: inputMessage,
          session_id: sessionId,
          image_base64: imageBase64,
          audio_base64: audioBase64,
          audio_format: 'webm'
        })
      })

      const data = await response.json()

      const assistantMessage = {
        id: data.message_id,
        role: 'assistant',
        content: data.response,
        audioTranscription: data.audio_transcription,
        transferRequested: data.transfer_requested,
        timestamp: new Date().toISOString(),
        userRating: null
      }

      setMessages(prev => [...prev, assistantMessage])

      if (data.transfer_requested) {
        setTimeout(() => setShowFeedbackModal(true), 1000)
      }

    } catch (error) {
      console.error('Erro ao enviar mensagem:', error)
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Desculpe, ocorreu um erro ao processar sua mensagem. Por favor, tente novamente.',
        isError: true,
        timestamp: new Date().toISOString()
      }])
    } finally {
      setIsLoading(false)
      setInputMessage('')
      setSelectedImage(null)
      setImagePreview(null)
      setAudioBlob(null)
    }
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const handleImageSelect = (e) => {
    const file = e.target.files[0]
    if (file) {
      setSelectedImage(file)
      const reader = new FileReader()
      reader.onload = (e) => {
        setImagePreview(e.target.result)
      }
      reader.readAsDataURL(file)
    }
  }

  const removeImage = () => {
    setSelectedImage(null)
    setImagePreview(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      mediaRecorderRef.current = new MediaRecorder(stream)
      audioChunksRef.current = []

      mediaRecorderRef.current.ondataavailable = (e) => {
        audioChunksRef.current.push(e.data)
      }

      mediaRecorderRef.current.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        setAudioBlob(blob)
        stream.getTracks().forEach(track => track.stop())
      }

      mediaRecorderRef.current.start()
      setIsRecording(true)
    } catch (error) {
      console.error('Erro ao iniciar gravação:', error)
      alert('Não foi possível acessar o microfone. Verifique as permissões.')
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
    }
  }

  const removeAudio = () => {
    setAudioBlob(null)
  }

  const clearConversation = async () => {
    if (sessionId) {
      try {
        await fetch(`${API_URL}/conversation/${sessionId}`, {
          method: 'DELETE'
        })
      } catch (error) {
        console.error('Erro ao limpar conversa:', error)
      }
    }
    setFeedbackSubmitted(false)
    setShowFeedbackModal(false)
    createNewSession()
  }

  const rateMessage = async (messageId, rating) => {
    try {
      await fetch(`${API_URL}/rating/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message_id: messageId,
          session_id: sessionId,
          rating: rating
        })
      })

      setMessages(prev => prev.map(msg =>
        msg.id === messageId ? { ...msg, userRating: rating } : msg
      ))
    } catch (error) {
      console.error('Erro ao avaliar mensagem:', error)
    }
  }

  const submitFeedback = async () => {
    if (feedbackRating === 0) return

    try {
      await fetch(`${API_URL}/rating/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          rating: feedbackRating,
          comment: feedbackComment || null,
          transfer_requested: true
        })
      })

      setFeedbackSubmitted(true)
      setTimeout(() => {
        setShowFeedbackModal(false)
      }, 2000)
    } catch (error) {
      console.error('Erro ao enviar feedback:', error)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex flex-col">
      {/* Header */}
      <header className="bg-white shadow-sm border-b border-gray-200">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-full flex items-center justify-center">
              <Bot className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-800">Salomão</h1>
              <p className="text-sm text-gray-500">Assistente inChurch</p>
            </div>
          </div>
          <button
            onClick={clearConversation}
            className="flex items-center gap-2 px-4 py-2 text-sm text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
          >
            <Trash2 className="w-4 h-4" />
            Nova conversa
          </button>
        </div>
      </header>

      {/* Chat Container */}
      <main className="flex-1 max-w-4xl w-full mx-auto flex flex-col">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 chat-container">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center py-12">
              <div className="w-20 h-20 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-full flex items-center justify-center mb-4">
                <Bot className="w-10 h-10 text-white" />
              </div>
              <h2 className="text-2xl font-bold text-gray-800 mb-2">Olá! Eu sou o Salomão 👋</h2>
              <p className="text-gray-600 max-w-md">
                Sou seu assistente virtual da inChurch. Posso ajudar com dúvidas sobre a plataforma,
                analisar imagens e até entender mensagens de áudio!
              </p>
              <div className="mt-6 flex flex-wrap gap-2 justify-center">
                {['Como criar cupom de desconto?', 'Como cadastrar células?', 'Gestão de eventos'].map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => setInputMessage(suggestion)}
                    className="px-4 py-2 bg-white border border-gray-200 rounded-full text-sm text-gray-700 hover:bg-gray-50 hover:border-gray-300 transition-colors"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message, index) => (
            <div
              key={index}
              className={`flex gap-3 ${message.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                message.role === 'user'
                  ? 'bg-blue-500'
                  : 'bg-gradient-to-br from-blue-500 to-indigo-600'
              }`}>
                {message.role === 'user' ? (
                  <User className="w-5 h-5 text-white" />
                ) : (
                  <Bot className="w-5 h-5 text-white" />
                )}
              </div>
              <div className={`max-w-[80%] ${message.role === 'user' ? 'text-right' : ''}`}>
                {message.image && (
                  <img
                    src={message.image}
                    alt="Imagem enviada"
                    className="max-w-xs rounded-lg mb-2 border border-gray-200"
                  />
                )}
                {message.hasAudio && (
                  <div className="flex items-center gap-2 text-sm text-gray-500 mb-1">
                    <Volume2 className="w-4 h-4" />
                    <span>Áudio enviado</span>
                  </div>
                )}
                {message.audioTranscription && (
                  <div className="text-sm text-gray-500 italic mb-2 bg-gray-100 p-2 rounded">
                    Transcrição: "{message.audioTranscription}"
                  </div>
                )}
                <div className={`inline-block px-4 py-3 rounded-2xl ${
                  message.role === 'user'
                    ? 'bg-blue-500 text-white rounded-br-md'
                    : message.isError
                      ? 'bg-red-50 text-red-700 border border-red-200 rounded-bl-md'
                      : 'bg-white text-gray-800 shadow-sm border border-gray-100 rounded-bl-md'
                }`}>
                  <div
                    className="message-content text-sm"
                    dangerouslySetInnerHTML={{ __html: formatMessage(message.content) }}
                  />
                </div>
                {message.role === 'assistant' && !message.isError && (
                  <div className="flex items-center gap-1 mt-2">
                    <button
                      onClick={() => rateMessage(message.id, 'like')}
                      className={`p-1.5 rounded-full transition-colors ${
                        message.userRating === 'like'
                          ? 'bg-green-100 text-green-600'
                          : 'text-gray-400 hover:text-green-500 hover:bg-green-50'
                      }`}
                      title="Útil"
                    >
                      <ThumbsUp className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => rateMessage(message.id, 'dislike')}
                      className={`p-1.5 rounded-full transition-colors ${
                        message.userRating === 'dislike'
                          ? 'bg-red-100 text-red-600'
                          : 'text-gray-400 hover:text-red-500 hover:bg-red-50'
                      }`}
                      title="Não útil"
                    >
                      <ThumbsDown className="w-4 h-4" />
                    </button>
                  </div>
                )}
                {message.transferRequested && (
                  <div className="mt-2 px-3 py-1 bg-yellow-100 text-yellow-800 text-xs rounded-full inline-block">
                    ⚠️ Transferência para suporte humano solicitada
                  </div>
                )}
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="flex gap-3">
              <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-full flex items-center justify-center">
                <Bot className="w-5 h-5 text-white" />
              </div>
              <div className="bg-white px-4 py-3 rounded-2xl rounded-bl-md shadow-sm border border-gray-100">
                <div className="typing-indicator flex gap-1">
                  <span className="w-2 h-2 bg-gray-400 rounded-full"></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full"></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full"></span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="border-t border-gray-200 bg-white p-4">
          {/* Preview Area */}
          {(imagePreview || audioBlob) && (
            <div className="mb-3 flex gap-2 flex-wrap">
              {imagePreview && (
                <div className="relative inline-block">
                  <img
                    src={imagePreview}
                    alt="Preview"
                    className="h-20 rounded-lg border border-gray-200"
                  />
                  <button
                    onClick={removeImage}
                    className="absolute -top-2 -right-2 w-6 h-6 bg-red-500 text-white rounded-full flex items-center justify-center hover:bg-red-600"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              )}
              {audioBlob && (
                <div className="relative inline-flex items-center gap-2 px-3 py-2 bg-blue-50 rounded-lg border border-blue-200">
                  <Volume2 className="w-5 h-5 text-blue-500" />
                  <span className="text-sm text-blue-700">Áudio gravado</span>
                  <button
                    onClick={removeAudio}
                    className="w-5 h-5 bg-red-500 text-white rounded-full flex items-center justify-center hover:bg-red-600"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              )}
            </div>
          )}

          <div className="flex items-end gap-2">
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleImageSelect}
              accept="image/*"
              className="hidden"
            />

            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-3 text-gray-500 hover:text-blue-500 hover:bg-blue-50 rounded-full transition-colors"
              title="Enviar imagem"
            >
              <Image className="w-5 h-5" />
            </button>

            <button
              onClick={isRecording ? stopRecording : startRecording}
              className={`p-3 rounded-full transition-colors ${
                isRecording
                  ? 'text-red-500 bg-red-50 animate-pulse'
                  : 'text-gray-500 hover:text-blue-500 hover:bg-blue-50'
              }`}
              title={isRecording ? 'Parar gravação' : 'Gravar áudio'}
            >
              {isRecording ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
            </button>

            <div className="flex-1 relative">
              <textarea
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Digite sua mensagem..."
                rows={1}
                className="w-full px-4 py-3 pr-12 border border-gray-200 rounded-2xl resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                style={{ minHeight: '48px', maxHeight: '120px' }}
              />
            </div>

            <button
              onClick={sendMessage}
              disabled={isLoading || (!inputMessage.trim() && !selectedImage && !audioBlob)}
              className={`p-3 rounded-full transition-colors ${
                isLoading || (!inputMessage.trim() && !selectedImage && !audioBlob)
                  ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  : 'bg-blue-500 text-white hover:bg-blue-600'
              }`}
            >
              {isLoading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                <Send className="w-5 h-5" />
              )}
            </button>
          </div>

          <p className="text-xs text-gray-400 text-center mt-3">
            Salomão pode cometer erros. Verifique informações importantes.
          </p>
        </div>
      </main>

      {/* Modal de Feedback */}
      {showFeedbackModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 animate-fade-in">
            {!feedbackSubmitted ? (
              <>
                <div className="text-center mb-6">
                  <div className="w-16 h-16 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-full flex items-center justify-center mx-auto mb-4">
                    <Star className="w-8 h-8 text-white" />
                  </div>
                  <h3 className="text-xl font-bold text-gray-800">Como foi o atendimento?</h3>
                  <p className="text-gray-500 text-sm mt-1">
                    Sua opinião nos ajuda a melhorar! (opcional)
                  </p>
                </div>

                <div className="flex justify-center gap-2 mb-6">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button
                      key={star}
                      onClick={() => setFeedbackRating(star)}
                      className={`p-2 rounded-full transition-all ${
                        feedbackRating >= star
                          ? 'text-yellow-400 scale-110'
                          : 'text-gray-300 hover:text-yellow-300'
                      }`}
                    >
                      <Star className="w-8 h-8 fill-current" />
                    </button>
                  ))}
                </div>

                {feedbackRating > 0 && (
                  <div className="mb-6">
                    <textarea
                      value={feedbackComment}
                      onChange={(e) => setFeedbackComment(e.target.value)}
                      placeholder="Conte-nos mais sobre sua experiência... (opcional)"
                      className="w-full px-4 py-3 border border-gray-200 rounded-xl resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
                      rows={3}
                    />
                  </div>
                )}

                <div className="flex gap-3">
                  <button
                    onClick={() => setShowFeedbackModal(false)}
                    className="flex-1 px-4 py-3 text-gray-600 hover:bg-gray-100 rounded-xl transition-colors"
                  >
                    Pular
                  </button>
                  <button
                    onClick={submitFeedback}
                    disabled={feedbackRating === 0}
                    className={`flex-1 px-4 py-3 rounded-xl transition-colors ${
                      feedbackRating > 0
                        ? 'bg-blue-500 text-white hover:bg-blue-600'
                        : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    }`}
                  >
                    Enviar avaliação
                  </button>
                </div>
              </>
            ) : (
              <div className="text-center py-8">
                <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <ThumbsUp className="w-8 h-8 text-green-600" />
                </div>
                <h3 className="text-xl font-bold text-gray-800">Obrigado!</h3>
                <p className="text-gray-500 mt-1">Sua avaliação foi enviada com sucesso.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default App
