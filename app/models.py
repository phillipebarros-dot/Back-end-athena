"""
Modelos Pydantic — validação de entrada/saída para todos os endpoints.

Substitui os 5 sanitizadores do n8n (Sanitizador de Entrada, Sanitizar Entrada Conversa,
Sanitizar Entrada Historico, Sanitizar Entrada Mensagem, Sanitizar Feedback) por schemas
declarativos e type-safe.
"""

from __future__ import annotations

import logging
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
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|CREATE|EXEC|UNION\s+SELECT|"
    r"MERGE|GRANT|REVOKE|CALL|EXECUTE|xp_|sp_|INFORMATION_SCHEMA|"
    r"INTO\s+OUTFILE|INTO\s+DUMPFILE|LOAD_FILE|BENCHMARK|SLEEP)\b",
    re.IGNORECASE,
)

# Prompt injection patterns — defesa em profundidade
# Cobre: EN + PT-BR + variantes com typo + unicode tricks + encoded headers
_PROMPT_INJECTION = re.compile(
    r"("
    # === EN: Classic jailbreak patterns ===
    r"ignore\s*(all\s*)?(previous|prior|above|earlier|preceding)\s*(instructions?|rules?|prompts?|context)"
    r"|disregard\s*(all\s*)?(previous|prior|above)\s*(instructions?|rules?)"
    r"|forget\s*(all\s*)?(previous|prior|your)\s*(instructions?|rules?|training|programming)"
    r"|override\s*(your|all|the|system)?\s*(instructions?|rules?|safety|restrictions?)"
    r"|bypass\s*(your|all|the|system)?\s*(filters?|restrictions?|rules?|safety|guardrails?)"
    r"|new\s+instructions?\s*:"
    r"|system\s*prompt"
    r"|act\s+as\s+(if\s+you\s+are\s+|a\s+|an\s+|my\s+)?"
    r"|you\s+are\s+now\s+(a\s+|an\s+|my\s+)?"
    r"|pretend\s+(to\s+be|you\s+are|that)"
    r"|roleplay\s+as"
    r"|jailbreak"
    r"|DAN\s*mode"
    r"|developer\s*mode"
    r"|do\s+anything\s+now"
    r"|no\s+restrictions?\s+mode"
    r"|god\s*mode"
    r"|sudo\s+mode"
    r"|admin\s+override"
    r"|master\s+prompt"
    r"|reveal\s+(your|the|system)\s*(prompt|instructions?|rules?|config)"
    r"|show\s+(me\s+)?(your|the)\s*(prompt|instructions?|system)"
    r"|what\s+(are|is)\s+your\s*(system\s*)?(prompt|instructions?|rules?)"
    r"|print\s+(your|the|system)\s*(prompt|instructions?)"
    r"|output\s+(your|the|system)\s*(prompt|instructions?)"
    r"|repeat\s+(your|the|everything|all)\s*(above|instructions?|prompt)"
    r"|tell\s+me\s+(your|the)\s*(system\s*)?(prompt|instructions?|rules?)"
    # === PT-BR: Padroes em portugues ===
    r"|ignore\s*(todas?\s*)?(as\s*)?(instruc|regras?|prompt|anterior)"
    r"|esqueca\s*(todas?\s*)?(as\s*)?(instruc|regras?|anteriores?)"
    r"|desconsidere\s*(todas?\s*)?(instruc|regras?)"
    r"|finja\s+(ser|que\s+(voce|vc)\s+(e|eh|sera))"
    r"|voce\s+(agora\s+)?(e|eh|sera)\s+(um|uma|o|a)\s+"
    r"|aja\s+como\s+(se\s+)?(voce\s+)?(fosse|e|eh)"
    r"|assuma\s+(o\s+)?(papel|persona|identidade|role)"
    r"|revele\s*(o|seu|as?)\s*(prompt|instruc|regras?|config)"
    r"|mostre\s*(o|seu|as?)\s*(prompt|instruc|regras?)"
    r"|qual\s+(e|eh|sao)\s+(o\s+)?(seu|suas?)\s*(prompt|instruc|regras?)"
    r"|modo\s+(sem\s+)?restri"
    r"|modo\s+dev"
    r"|modo\s+admin"
    # === Encoded/obfuscation attempts ===
    r"|base64\s*:"
    r"|eval\s*\("
    r"|<\s*script"
    r"|javascript\s*:"
    r"|data\s*:\s*text"
    r")",
    re.IGNORECASE,
)

# Caracteres de controle (exceto newline e tab)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Unicode homoglyphs comuns usados para bypassar regex
_HOMOGLYPH_MAP = str.maketrans({
    '\u0430': 'a',  # Cyrillic а -> Latin a
    '\u0435': 'e',  # Cyrillic е -> Latin e
    '\u043e': 'o',  # Cyrillic о -> Latin o
    '\u0440': 'p',  # Cyrillic р -> Latin p
    '\u0441': 'c',  # Cyrillic с -> Latin c
    '\u0443': 'y',  # Cyrillic у -> Latin y
    '\u0456': 'i',  # Ukrainian і -> Latin i
    '\u04bb': 'h',  # Cyrillic һ -> Latin h
    '\uff49': 'i',  # Fullwidth ｉ -> i
    '\uff4e': 'n',  # Fullwidth ｎ -> n
    '\uff47': 'g',  # Fullwidth ｇ -> g
    '\u200b': '',   # Zero-width space -> remove
    '\u200c': '',   # Zero-width non-joiner -> remove
    '\u200d': '',   # Zero-width joiner -> remove
    '\ufeff': '',   # BOM -> remove
})

_injection_logger = logging.getLogger("athena.security.injection")


def _sanitize_text(value: str, max_chars: int = 4000) -> str:
    """Sanitiza texto de entrada com defesa em profundidade."""
    # 1. Remove caracteres de controle
    value = _CONTROL_CHARS.sub("", value)
    # 2. Normaliza unicode homoglyphs para ASCII
    value = value.translate(_HOMOGLYPH_MAP)
    # 3. Remove zero-width characters residuais
    value = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060\ufeff]', '', value)
    # 4. Trunca
    value = value[:max_chars]
    return value.strip()


def _check_injection(value: str) -> str:
    """Verifica SQL injection e prompt injection com logging."""
    if _SQL_INJECTION.search(value):
        _injection_logger.warning(
            "SQL injection bloqueado: %.100s...", value[:100]
        )
        raise ValueError("Entrada contem padroes SQL nao permitidos.")
    if _PROMPT_INJECTION.search(value):
        _injection_logger.warning(
            "Prompt injection bloqueado: %.100s...", value[:100]
        )
        raise ValueError("Entrada contem padroes de prompt injection nao permitidos.")
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
    DELETE = "delete"


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
    format: str = "sheets"  # "sheets" | "csv" | "xlsx"
    user_email: str | None = None  # Email para compartilhar a planilha
    google_access_token: str | None = None  # Token OAuth do usuario para Sheets
