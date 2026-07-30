"""
Modelos Pydantic — validação de entrada/saída para todos os endpoints.

Substitui os 5 sanitizadores do n8n (Sanitizador de Entrada, Sanitizar Entrada Conversa,
Sanitizar Entrada Historico, Sanitizar Entrada Mensagem, Sanitizar Feedback) por schemas
declarativos e type-safe.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# Segurança — Padrões de injection
# ============================================================================

# SQL injection patterns (do Sanitizador de Entrada do n8n)
_SQL_INJECTION = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|CREATE|EXEC|UNION\s+SELECT)\b",
    re.IGNORECASE,
)

# Prompt injection patterns (do Sanitizador de Entrada do n8n)
_PROMPT_INJECTION = re.compile(
    r"(ignore previous|system prompt|act as|you are now|pretend|jailbreak|DAN mode)",
    re.IGNORECASE,
)

# Caracteres de controle (exceto newline e tab)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_text(value: str, max_chars: int = 4000) -> str:
    """Sanitiza texto de entrada — equivalente ao Sanitizador de Entrada do n8n."""
    value = _CONTROL_CHARS.sub("", value)
    value = value[:max_chars]
    return value.strip()


def _check_injection(value: str) -> str:
    """Verifica SQL injection e prompt injection."""
    if _SQL_INJECTION.search(value):
        raise ValueError("Entrada contém padrões SQL não permitidos.")
    if _PROMPT_INJECTION.search(value):
        raise ValueError("Entrada contém padrões de prompt injection não permitidos.")
    return value


# ============================================================================
# Chat
# ============================================================================

class ChatRequest(BaseModel):
    """Request para POST /chat — substitui o chatTrigger + Sanitizador do n8n."""
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = Field(default="anonymous")
    user_email: str | None = None
    is_audio: bool = False
    client: str | None = None  # Multi-tenant: "O Boticário" | "Eudora" | ... | "Todos"

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        v = _sanitize_text(v)
        return _check_injection(v)


class Attachment(BaseModel):
    """Artefato anexo (PDF, Sheets) — equivalente ao contrato de saída do n8n."""
    status: str = "success"
    file_type: str  # "pdf" | "sheet"
    url: str
    view_url: str | None = None


class ProvenanceSource(BaseModel):
    """Fonte de proveniência — identifica de onde a informação foi extraída."""
    label: str            # ex.: "Publi, BigQuery"
    detail: str | None = None  # ex.: "pj_boti.pi_insercoes"


class ChatResponse(BaseModel):
    """Response do POST /chat — equivalente ao Validador de Resposta do n8n."""
    output: str
    conversation_id: str
    latency_ms: int | None = None
    attachment: Attachment | None = None
    audio: str | None = None  # n8n usa campo 'audio', não 'audio_base64'
    sources: list[ProvenanceSource] | None = None   # Fontes consultadas (front: "Como cheguei nesse resultado")
    query: str | None = None                          # SQL executada
    tables: list[str] | None = None                   # Tabelas acessadas


# ============================================================================
# Conversas
# ============================================================================

class ConversationAction(str, Enum):
    LIST = "list"
    CREATE = "create"
    UPDATE_TITLE = "updateTitle"


class ConversationRequest(BaseModel):
    """Request para POST /conversations — substitui o Webhook Conversas do n8n."""
    action: ConversationAction
    user_id: str
    conversation_id: str | None = None
    title: str | None = None

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: str | None) -> str | None:
        if v is not None:
            v = _sanitize_text(v, max_chars=200)
        return v


class ConversationResponse(BaseModel):
    """Uma conversa individual."""
    conversation_id: str
    user_id: str
    title: str
    status: str = "active"
    message_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ============================================================================
# Histórico
# ============================================================================

class HistoryRequest(BaseModel):
    """Request para GET /history."""
    conversation_id: str
    limit: int = Field(default=200, ge=1, le=500)


class MessageRecord(BaseModel):
    """Uma mensagem individual do histórico."""
    message_id: str
    conversation_id: str
    user_id: str
    role: str  # "user" | "assistant" | "system" | "system_summary"
    content: str
    timestamp: datetime | None = None
    is_compacted: bool = False

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"user", "assistant", "system", "system_summary"}
        if v not in allowed:
            raise ValueError(f"Role '{v}' não permitido. Use: {allowed}")
        return v


# ============================================================================
# Salvar Mensagem
# ============================================================================

class SaveMessageRequest(BaseModel):
    """Request para POST /save-message."""
    conversation_id: str
    user_id: str
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"user", "assistant", "system", "system_summary"}
        if v not in allowed:
            raise ValueError(f"Role '{v}' não permitido. Use: {allowed}")
        return v

    @field_validator("content")
    @classmethod
    def sanitize_content(cls, v: str) -> str:
        return _sanitize_text(v, max_chars=50000)  # Respostas podem ser longas


# ============================================================================
# Feedback
# ============================================================================

class FeedbackRating(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class FeedbackRequest(BaseModel):
    """Request para POST /feedback — substitui o Sanitizar Feedback do n8n."""
    user_id: str
    message_id: str
    rating: FeedbackRating
    conversation_id: str | None = None  # n8n salva conversation_id no feedback
    user_query: str | None = None
    assistant_response: str | None = None
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def sanitize_comment(cls, v: str | None) -> str | None:
        if v is not None:
            v = _sanitize_text(v, max_chars=1000)
        return v


# ============================================================================
# Compactação
# ============================================================================

class CompactRequest(BaseModel):
    """Request para POST /compact."""
    conversation_id: str
    threshold: int = Field(default=30, ge=5, le=100)
    keep_recent: int = Field(default=10, ge=3, le=50)


# ============================================================================
# Auditoria
# ============================================================================

class AuditRequest(BaseModel):
    """Request para POST /audit.

    6 queries suportadas (do Roteador de Auditoria do n8n):
    kpis, recent_activity, recent_feedback, top_users,
    all_conversations, conversation_messages
    """
    query: str  # kpis | recent_activity | recent_feedback | top_users | all_conversations | conversation_messages
    user_email: str  # Para validação admin server-side
    conversation_id: str | None = None  # Para conversation_messages
    date_from: str | None = None  # Para all_conversations (YYYY-MM-DD)
    date_to: str | None = None  # Para all_conversations (YYYY-MM-DD)


# ============================================================================
# TTS
# ============================================================================

class TTSRequest(BaseModel):
    """Request para POST /tts."""
    text: str = Field(..., min_length=1, max_length=5000)

    @field_validator("text")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        return _sanitize_text(v, max_chars=5000)


class TTSResponse(BaseModel):
    """Response do POST /tts — n8n retorna campo 'audio' (não 'audio_base64')."""
    audio: str


# ============================================================================
# Export
# ============================================================================

class ExportRequest(BaseModel):
    """Request para POST /export."""
    data: list[dict]
    title: str = "Athena Export"
    format: str = "sheets"  # "sheets" | "csv"
