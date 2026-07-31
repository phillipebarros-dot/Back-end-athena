"""
Configuração centralizada do backend Athena.

Carrega de variáveis de ambiente (.env) e expõe como dataclass tipada.
NUNCA hardcode credenciais — tudo via env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class BigQueryConfig:
    """Configuração do BigQuery para dados de mídia e persistência.

    ATENÇÃO: a persistência (conversations, messages, feedback, logs, learnings)
    roda no projeto 'sheetsintegration-451500', NÃO no 'athenaai-opus'.
    As tools do agente (mídia, financeiro) usam 'athenaai-opus'.
    """
    project_id: str = os.getenv("BQ_PROJECT_ID", "athenaai-opus")
    project_persistence: str = os.getenv("BQ_PROJECT_PERSISTENCE", "sheetsintegration-451500")
    dataset_media: str = os.getenv("BQ_DATASET_MEDIA", "ath_boticario")
    dataset_persistence: str = os.getenv("BQ_DATASET_PERSISTENCE", "pj_boti")


@dataclass(frozen=True)
class LLMConfig:
    """Configuração dos modelos LLM."""
    # Agente principal
    model_main: str = os.getenv("LLM_MODEL_MAIN", "claude-sonnet-4-6")
    temperature_main: float = float(os.getenv("LLM_TEMPERATURE_MAIN", "0.15"))
    max_tokens_main: int = int(os.getenv("LLM_MAX_TOKENS_MAIN", "4096"))

    # Sumarizador (compactação de memória)
    model_summarizer: str = os.getenv("LLM_MODEL_SUMMARIZER", "claude-haiku-4-5")
    temperature_summarizer: float = float(os.getenv("LLM_TEMPERATURE_SUMMARIZER", "0.1"))
    max_tokens_summarizer: int = int(os.getenv("LLM_MAX_TOKENS_SUMMARIZER", "1024"))

    # Agent loop
    max_iterations: int = int(os.getenv("LLM_MAX_ITERATIONS", "20"))


@dataclass(frozen=True)
class MCPConfig:
    """Configuração dos 4 MCP servers do Camilo (Cloud Run).

    Cada MCP roda como serviço independente no Cloud Run.
    Auth: Bearer Token compartilhado (MCP_AUTH_TOKEN).
    """
    # publi-mysql: ERP ao vivo (fonte principal)
    publi_url: str = os.getenv(
        "MCP_PUBLI_URL",
        "https://mcp-publi-mysql-642859299503.us-central1.run.app/mcp",
    )
    # pesquisas: audiência IBOPE/Rádio + OOH + TGI (complemento BigQuery)
    pesquisas_url: str = os.getenv(
        "MCP_PESQUISAS_URL",
        "https://mcp-pesquisas-642859299503.us-central1.run.app/mcp",
    )
    export_url: str = os.getenv(
        "MCP_EXPORT_URL",
        "https://mcp-export-642859299503.us-central1.run.app/mcp",
    )
    midia_online_url: str = os.getenv(
        "MCP_MIDIA_ONLINE_URL",
        "https://mcp-midia-online-642859299503.us-central1.run.app/mcp",
    )
    auth_token: str = os.getenv("MCP_AUTH_TOKEN", "").strip()


@dataclass(frozen=True)
class PersistenceConfig:
    """Configuração do PostgreSQL (LangGraph checkpointer) e Cloud SQL.

    Cloud SQL: instância pg-grom no projeto db-sql-om (PostgreSQL 18).
    Dois modos de conexão:
      - POSTGRES_URI: conexão direta (dev, requer IP liberado)
      - CLOUDSQL_*: via Cloud SQL Connector (prod/Cloud Run)
    """
    postgres_uri: str = os.getenv("POSTGRES_URI", "")
    # Cloud SQL Connector (alternativa ao URI direto — recomendado pra Cloud Run)
    cloudsql_instance: str = os.getenv("CLOUDSQL_INSTANCE", "db-sql-om:us-central1:pg-grom")
    cloudsql_user: str = os.getenv("CLOUDSQL_USER", "")
    cloudsql_password: str = os.getenv("CLOUDSQL_PASSWORD", "")
    cloudsql_db: str = os.getenv("CLOUDSQL_DB", "postgres")
    cloudsql_sa_key: str = os.getenv("CLOUDSQL_SA_KEY", "")
    # Tabelas BigQuery de persistência (migradas do n8n)
    table_conversations: str = "athena_conversations"
    table_messages: str = "athena_messages"
    table_feedback: str = "athena_feedback"
    table_learnings: str = "athena_learnings"
    table_logs: str = "athena_logs"


@dataclass(frozen=True)
class TTSConfig:
    """Configuração do Text-to-Speech (OpenAI)."""
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    model: str = os.getenv("TTS_MODEL", "tts-1-hd")
    voice: str = os.getenv("TTS_VOICE", "nova")


@dataclass(frozen=True)
class SecurityConfig:
    """Configuração de segurança."""
    admin_emails: list[str] = field(default_factory=lambda: [
        e.strip()
        for e in os.getenv(
            "ADMIN_EMAILS",
            "andrei@grupoom.com.br,phillipe.barros@grupoom.com.br,"
            "camilo.ferreira@grupoom.com.br",
        ).split(",")
        if e.strip()
    ])
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
    max_input_chars: int = int(os.getenv("MAX_INPUT_CHARS", "4000"))


@dataclass(frozen=True)
class AppConfig:
    """Configuração raiz — agrega todas as sub-configs."""
    bq: BigQueryConfig = field(default_factory=BigQueryConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8080"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


# Singleton — importar de qualquer lugar
settings = AppConfig()
