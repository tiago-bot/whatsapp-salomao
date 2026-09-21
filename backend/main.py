import base64
import uuid
from logging_config import configure_logging

configure_logging()
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from salomao_agent import salomao
from database import db
from media_processing import MAX_MEDIA_BYTES, MAX_BASE64_CHARS, AUDIO_FORMATS, normalize_audio_format
import json
import asyncio


app = FastAPI(
    title="Salomão - Assistente inChurch",
    description="API do agente de IA Salomão para suporte ao cliente inChurch",
    version="1.0.0"
)

import os
cors_origins = os.getenv("CORS_ORIGINS", "*")
if cors_origins == "*":
    origins = ["*"]
else:
    origins = [origin.strip() for origin in cors_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = ""
    session_id: Optional[str] = None
    image_base64: Optional[str] = Field(default=None, max_length=MAX_BASE64_CHARS)
    image_mime_type: Optional[str] = None
    audio_base64: Optional[str] = Field(default=None, max_length=MAX_BASE64_CHARS)
    audio_format: Optional[str] = "wav"


class TokenUsage(BaseModel):
    prompt: int = 0
    completion: int = 0
    total: int = 0


class ChatResponse(BaseModel):
    success: bool
    response: str
    session_id: str
    transfer_requested: bool = False
    audio_transcription: Optional[str] = None
    model_used: Optional[str] = None
    message_count: Optional[int] = None
    tokens: Optional[TokenUsage] = None
    message_id: Optional[str] = None
    error: Optional[str] = None
    answer_status: str = "answered"
    sources: list[dict] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)


@app.get("/")
async def root():
    return {
        "message": "Salomão - Assistente inChurch",
        "status": "online",
        "version": "1.0.0"
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Endpoint principal para conversar com o Salomão.
    Aceita texto, imagem em base64 e áudio em base64.
    """
    session_id = request.session_id or str(uuid.uuid4())

    result = await asyncio.to_thread(salomao.process_message,
        message=request.message,
        session_id=session_id,
        image_base64=request.image_base64,
        image_mime_type=request.image_mime_type,
        audio_base64=request.audio_base64,
        audio_format=request.audio_format or "wav"
    )

    return ChatResponse(**result)


@app.post("/chat/upload", response_model=ChatResponse)
async def chat_with_upload(
    message: str = Form(""),
    session_id: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    audio: Optional[UploadFile] = File(None)
):
    """
    Endpoint para conversar com upload de arquivos.
    Aceita imagem e áudio como arquivos.
    """
    session_id = session_id or str(uuid.uuid4())

    image_base64 = None
    audio_base64 = None
    audio_format = "wav"

    async def read_attachment(upload: UploadFile) -> bytes:
        content = await upload.read(MAX_MEDIA_BYTES + 1)
        if len(content) > MAX_MEDIA_BYTES:
            raise HTTPException(status_code=413, detail="Cada anexo deve ter até 20 MB.")
        if not content:
            raise HTTPException(status_code=422, detail="O anexo está vazio. Envie o arquivo novamente.")
        return content

    if image:
        image_content = await read_attachment(image)
        image_base64 = base64.b64encode(image_content).decode("utf-8")

    if audio:
        audio_content = await read_attachment(audio)
        audio_base64 = base64.b64encode(audio_content).decode("utf-8")
        if audio.filename:
            audio_format = normalize_audio_format(audio.filename.rsplit(".", 1)[-1])
        if audio_format not in AUDIO_FORMATS or not audio.filename or "." not in audio.filename:
            audio_format = normalize_audio_format(audio.content_type)
            if audio_format not in AUDIO_FORMATS:
                audio_format = "wav"  # The decoder identifies the real container from its bytes.

    result = await asyncio.to_thread(salomao.process_message,
        message=message,
        session_id=session_id,
        image_base64=image_base64,
        image_mime_type=image.content_type if image else None,
        audio_base64=audio_base64,
        audio_format=audio_format
    )

    return result


@app.get("/conversation/{session_id}")
async def get_conversation(session_id: str):
    """Retorna o histórico de conversa de uma sessão."""
    history = salomao.get_conversation_history(session_id)
    return {
        "session_id": session_id,
        "messages": history,
        "message_count": len(history)
    }


@app.delete("/conversation/{session_id}")
async def clear_conversation(session_id: str):
    """Limpa o histórico de conversa de uma sessão."""
    salomao.clear_conversation(session_id)
    return {
        "success": True,
        "message": f"Conversa {session_id} limpa com sucesso"
    }


@app.post("/session/new")
async def create_session():
    """Cria uma nova sessão de conversa."""
    session_id = str(uuid.uuid4())
    return {
        "session_id": session_id,
        "message": "Nova sessão criada com sucesso"
    }


class MessageRatingRequest(BaseModel):
    message_id: str
    session_id: str
    rating: str  # 'like' ou 'dislike'


class SessionFeedbackRequest(BaseModel):
    session_id: str
    rating: int  # 0-5
    comment: Optional[str] = None
    transfer_requested: bool = True


@app.post("/rating/message")
async def rate_message(request: MessageRatingRequest):
    """Avalia uma mensagem individual (like/dislike)."""
    if request.rating not in ["like", "dislike"]:
        raise HTTPException(status_code=400, detail="Rating deve ser 'like' ou 'dislike'")

    result = db.rate_message(
        message_id=request.message_id,
        session_id=request.session_id,
        rating=request.rating
    )

    return {
        "success": True,
        "message_id": request.message_id,
        "rating": request.rating
    }


@app.post("/rating/session")
async def submit_session_feedback(request: SessionFeedbackRequest):
    """Envia avaliação final do atendimento (0-5 + comentário opcional)."""
    if not 0 <= request.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating deve ser entre 0 e 5")

    result = db.submit_session_feedback(
        session_id=request.session_id,
        rating=request.rating,
        comment=request.comment,
        transfer_requested=request.transfer_requested
    )

    return {
        "success": True,
        "session_id": request.session_id,
        "rating": request.rating,
        "comment": request.comment
    }


@app.get("/rating/session/{session_id}")
async def get_session_feedback(session_id: str):
    """Obtém a avaliação de uma sessão."""
    feedback = db.get_session_feedback(session_id)
    return {
        "session_id": session_id,
        "feedback": feedback
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
