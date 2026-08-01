"""
LangGraph Agent — cérebro da Athena.

Substitui o "Athena Agente Principal" do n8n (node tipo @n8n/n8n-nodes-langchain.agent)
por um grafo LangGraph com state management, checkpointing, e integração MCP.

MCP Integration:
    Usa langchain-mcp-adapters v0.3.0 para conectar com os 4 MCPs (v2.4 producao):
    - publi (MySQL ERP ao vivo — fonte principal)
    - pesquisas (BigQuery audiencia IBOPE/Radio/OOH/TGI)
    - midia_online (BigQuery performance digital)
    - export (Google Sheets / CSV export)

    tool_name_prefix=True evita colisão de nomes entre servers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.agent.prompts import get_prompt_for_client
from app.config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# LLM Builders
# ============================================================================

def _build_llm() -> ChatOpenAI:
    """Cria a instância do LLM principal (GPT-4o).

    Equivale ao node LLM do n8n, agora usando OpenAI.
    """
    return ChatOpenAI(
        model=settings.llm.model_main,
        temperature=settings.llm.temperature_main,
        max_tokens=settings.llm.max_tokens_main,
    )


def _build_summarizer_llm() -> ChatOpenAI:
    """Cria a instância do LLM sumarizador (GPT-4o-mini).

    Usado para compactação de memória.
    """
    return ChatOpenAI(
        model=settings.llm.model_summarizer,
        temperature=settings.llm.temperature_summarizer,
        max_tokens=settings.llm.max_tokens_summarizer,
    )


# ============================================================================
# Checkpointer
# ============================================================================

# Estado global do checkpointer — inicializado pelo lifespan (main.py)
_checkpointer = None
_checkpointer_pool = None


async def initialize_checkpointer():
    """Inicializa o checkpointer Postgres (pool + tabelas).

    Chamado UMA VEZ pelo lifespan do FastAPI no startup.
    Se Postgres não estiver disponível, cai para MemorySaver.

    Prioridade de conexão:
      1. CLOUDSQL_* vars (host/user/password/db separados — evita encoding)
      2. POSTGRES_URI (fallback — precisa de encoding correto)
      3. MemorySaver (dev/teste)

    FIX C2: AsyncPostgresSaver.setup() cria as tabelas checkpoints/
    checkpoint_blobs/checkpoint_writes. Sem isso, a primeira leitura
    estoura UndefinedTable.

    FIX M3: pool criado com open=False, depois await pool.open()
    (evita DeprecationWarning em psycopg3 async).
    """
    global _checkpointer, _checkpointer_pool

    # Determina conninfo: prioriza componentes individuais (sem encoding)
    conninfo = None
    p = settings.persistence
    cloudsql_instance = p.cloudsql_instance  # default: db-sql-om:us-central1:pg-grom

    if p.cloudsql_user and p.cloudsql_password:
        from psycopg.conninfo import make_conninfo

        if cloudsql_instance:
            # Cloud Run com instância Cloud SQL anexada → socket unix (não TCP)
            # O Cloud SQL Auth Proxy cria o socket em /cloudsql/<INSTANCE>
            socket_path = f"/cloudsql/{cloudsql_instance}"
            conninfo = make_conninfo(
                host=socket_path,
                user=p.cloudsql_user,
                password=p.cloudsql_password,
                dbname=p.cloudsql_db,
            )
            logger.info(
                "Postgres conninfo via Cloud SQL socket (instance=%s, user=%s, db=%s)",
                cloudsql_instance, p.cloudsql_user, p.cloudsql_db,
            )
        else:
            # Dev/local: IP direto com SSL
            conninfo = make_conninfo(
                host=os.getenv("CLOUDSQL_HOST", ""),
                port=int(os.getenv("CLOUDSQL_PORT", "5432")),
                user=p.cloudsql_user,
                password=p.cloudsql_password,
                dbname=p.cloudsql_db,
                sslmode=os.getenv("CLOUDSQL_SSLMODE", "require"),
            )
            logger.info(
                "Postgres conninfo via TCP (host=%s, user=%s, db=%s)",
                os.getenv("CLOUDSQL_HOST", ""), p.cloudsql_user, p.cloudsql_db,
            )
    elif p.postgres_uri:
        # POSTGRES_URI direto — deve usar formato socket se em Cloud Run:
        # postgresql://user:pass@/dbname?host=/cloudsql/INSTANCE
        conninfo = p.postgres_uri
        logger.info("Postgres conninfo via POSTGRES_URI")
    else:
        logger.info("Postgres não configurado. Usando MemorySaver (dev/teste). State NÃO persiste entre restarts.")
        _checkpointer = MemorySaver()
        return

    try:
        from psycopg_pool import AsyncConnectionPool
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = AsyncConnectionPool(
            conninfo=conninfo,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,  # FIX M3: não abrir no construtor
        )
        await pool.open()  # Abre dentro do event loop
        _checkpointer_pool = pool

        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()  # FIX C2: cria tabelas se não existirem

        _checkpointer = checkpointer
        logger.info("Checkpointer PostgreSQL inicializado com sucesso. State PERSISTE entre restarts.")

    except ImportError:
        logger.warning(
            "langgraph-checkpoint-postgres não instalado. "
            "Instale com: pip install langgraph-checkpoint-postgres"
        )
        _checkpointer = MemorySaver()
    except Exception as e:
        logger.error(
            "Erro ao inicializar checkpointer PostgreSQL: %s. Caindo para MemorySaver.", e,
            exc_info=True,
        )
        _checkpointer = MemorySaver()


def _get_checkpointer():
    """Retorna o checkpointer inicializado (Postgres ou MemorySaver fallback)."""
    if _checkpointer is None:
        logger.warning("Checkpointer não inicializado — lifespan não rodou? Usando MemorySaver.")
        return MemorySaver()
    return _checkpointer


# ============================================================================
# MCP Tools (Camilo — 4 servers no Cloud Run)
# ============================================================================

async def _load_mcp_tools() -> list:
    """Carrega tools dos 4 MCPs do Camilo via langchain-mcp-adapters.

    Cada MCP server expõe tools via protocolo MCP (Model Context Protocol).
    O adapter descobre e converte automaticamente para LangChain tools.

    tool_name_prefix=True → prefixa com nome do server para evitar colisão.
    Exemplo: publi_mysql__consultar_mysql, export__exportar_planilha

    Returns:
        Lista de tools MCP prontas para uso no agente.
        Se MCP_AUTH_TOKEN vazio ou conexão falhar, retorna lista vazia.
    """
    if not settings.mcp.auth_token:
        logger.warning("MCP_AUTH_TOKEN não configurado. Tools MCP desabilitadas.")
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        auth_header = {"Authorization": f"Bearer {settings.mcp.auth_token}"}

        # Monta config apenas para MCPs com URL configurada
        mcp_servers = {}
        server_urls = {
            "publi": settings.mcp.publi_url,
            "pesquisas": settings.mcp.pesquisas_url,
            "export": settings.mcp.export_url,
            "midia_online": settings.mcp.midia_online_url,
        }
        for name, url in server_urls.items():
            if url:
                mcp_servers[name] = {
                    "url": url,
                    "transport": "streamable_http",
                    "headers": auth_header,
                }

        if not mcp_servers:
            logger.warning("Nenhum MCP server configurado.")
            return []

        client = MultiServerMCPClient(mcp_servers)
        tools = await client.get_tools()

        logger.info(
            "MCP tools carregadas: %d tools de %d servers (%s)",
            len(tools),
            len(mcp_servers),
            ", ".join(mcp_servers.keys()),
        )

        # Log nome de cada tool para debug
        for t in tools:
            logger.debug("  MCP tool: %s", t.name)

        return tools

    except ImportError:
        logger.error(
            "langchain-mcp-adapters não instalado. "
            "Instale com: pip install langchain-mcp-adapters"
        )
        raise  # FIX C1: não engolir — deixar o chamador fazer fallback
    except Exception as e:
        logger.error("Erro ao carregar MCP tools: %s", e, exc_info=True)
        raise  # FIX C1: não engolir — deixar o chamador fazer fallback


# ============================================================================
# MCP Fallback (FIX C1)
# ============================================================================

async def _load_tools_with_fallback() -> list:
    """Carrega MCP tools; se falhar, cai para legacy tools locais.

    FIX C1: o antigo _load_mcp_tools() engolia exceções e retornava [].
    Resultado: agente sem nenhuma tool → alucinava dados.
    Agora: tenta MCP → se falhar, ativa as 14 tools locais BigQuery.
    """
    try:
        mcp_tools = await _load_mcp_tools()
        if mcp_tools:
            return mcp_tools
        logger.warning(
            "MCP retornou 0 tools. Ativando fallback para tools locais (BigQuery direto)."
        )
    except Exception as e:
        logger.warning(
            "MCP indisponível (%s). Ativando fallback para tools locais (BigQuery direto).", e
        )

    from app.agent.tools import get_legacy_tools
    legacy = get_legacy_tools()
    if legacy:
        logger.info("Fallback ativo: %d tools locais carregadas.", len(legacy))
    else:
        logger.critical("NENHUMA tool disponível (MCP + legacy). Agente vai operar sem dados.")
    return legacy


# ============================================================================
# Agent Builder
# ============================================================================

async def build_agent(
    local_tools: list[Any] | None = None,
    cliente: str | None = None,
):
    """Constrói o agente LangGraph com tools locais + MCP e system prompt.

    Este é o equivalente Python do workflow inteiro do n8n:
    - chatTrigger → Sanitizador → Agent → Tools → Validador → Resposta

    Em LangGraph, tudo isso é um GRAFO com state management.

    Args:
        local_tools: Tools locais extras. Se None, usa lista vazia.
        cliente: Cliente ativo para renderizar o system prompt dinâmico.

    Returns:
        CompiledGraph — o agente compilado, pronto para .ainvoke() ou .astream().
    """
    llm = _build_llm()
    checkpointer = _get_checkpointer()  # FIX C2: usa checkpointer já inicializado
    system_prompt = get_prompt_for_client(cliente)

    # Combina tools locais extras + tools MCP (com fallback para legacy)
    all_tools = list(local_tools or [])

    data_tools = await _load_tools_with_fallback()  # FIX C1: fallback
    all_tools.extend(data_tools)

    if not all_tools:
        logger.critical(
            "Agente criado com ZERO tools. Todas as consultas a dados vão falhar."
        )

    agent = create_react_agent(
        model=llm,
        tools=all_tools,
        checkpointer=checkpointer,
        prompt=SystemMessage(content=system_prompt),
    )

    logger.info(
        "Agente LangGraph criado: model=%s, tools=%d, cliente=%s, checkpointer=%s",
        settings.llm.model_main,
        len(all_tools),
        cliente or "multi-cliente",
        type(checkpointer).__name__,
    )

    return agent


# ============================================================================
# Instância do agente — per-client com Lock (thread-safe)
#
# FIX CRÍTICO: o singleton antigo compartilhava o MESMO agente (com o MESMO
# system prompt) entre todos os requests, independente do cliente.
# Resultado: respostas cruzadas entre usuários e travamento quando dois
# requests chegavam ao mesmo tempo.
#
# Agora: um agente por cliente, protegido por asyncio.Lock.
# ============================================================================

_agent_cache: dict[str, Any] = {}  # cliente → agente compilado
_agent_lock = asyncio.Lock()


async def get_agent(tools: list[Any] | None = None, cliente: str | None = None):
    """Retorna o agente para o cliente solicitado (cache por cliente).

    - Se o cliente mudou, cria um novo agente com o system prompt correto.
    - asyncio.Lock impede race conditions entre requests simultâneos.
    - Cada agente tem seu próprio checkpointer, evitando contaminação.
    """
    cache_key = (cliente or "__default__").lower()

    # Fast path — agente já existe para este cliente
    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    # Slow path — criar agente (protegido por Lock)
    async with _agent_lock:
        # Double-check: outra coroutine pode ter criado enquanto esperávamos
        if cache_key in _agent_cache:
            return _agent_cache[cache_key]

        logger.info("Criando agente para cliente=%s (total em cache: %d)", cache_key, len(_agent_cache))
        agent = await build_agent(local_tools=tools, cliente=cliente)
        _agent_cache[cache_key] = agent
        return agent
