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

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.agent.prompts import get_prompt_for_client
from app.config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# LLM Builders
# ============================================================================

def _build_llm() -> ChatAnthropic:
    """Cria a instância do LLM principal (Claude Sonnet).

    Equivale ao node "Claude Sonnet LLM" do n8n
    (tipo: @n8n/n8n-nodes-langchain.lmChatAnthropic).
    """
    return ChatAnthropic(
        model=settings.llm.model_main,
        temperature=settings.llm.temperature_main,
        max_tokens=settings.llm.max_tokens_main,
    )


def _build_summarizer_llm() -> ChatAnthropic:
    """Cria a instância do LLM sumarizador (Claude Haiku).

    Equivale ao node "Claude Haiku Sumarizador" do n8n.
    Usado para compactação de memória.
    """
    return ChatAnthropic(
        model=settings.llm.model_summarizer,
        temperature=settings.llm.temperature_summarizer,
        max_tokens=settings.llm.max_tokens_summarizer,
    )


# ============================================================================
# Checkpointer
# ============================================================================

def _build_checkpointer():
    """Cria o checkpointer para persistência de state.

    Em PRODUÇÃO: usa AsyncPostgresSaver com Cloud SQL.
    Em DEV: usa MemorySaver (in-memory, não persiste entre restarts).

    Equivale ao node "Memoria da Conversa" do n8n
    (tipo: @n8n/n8n-nodes-langchain.memoryBufferWindow, buffer de 10 msgs).
    DIFERENÇA: LangGraph persiste state real, não buffer fixo.
    """
    if settings.persistence.postgres_uri:
        try:
            from psycopg_pool import AsyncConnectionPool
            from psycopg.rows import dict_row
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            pool = AsyncConnectionPool(
                conninfo=settings.persistence.postgres_uri,
                kwargs={"autocommit": True, "row_factory": dict_row},
                open=True,
            )
            checkpointer = AsyncPostgresSaver(pool)
            logger.info("Usando AsyncPostgresSaver (PostgreSQL). State PERSISTE entre restarts.")
            return checkpointer
        except ImportError:
            logger.warning(
                "langgraph-checkpoint-postgres não instalado. "
                "Instale com: pip install langgraph-checkpoint-postgres"
            )
        except Exception as e:
            logger.error("Erro ao conectar PostgreSQL: %s. Caindo para MemorySaver.", e)

    logger.info("Usando MemorySaver (dev/teste). State NÃO persiste entre restarts.")
    return MemorySaver()


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
        return []
    except Exception as e:
        logger.error("Erro ao carregar MCP tools: %s", e, exc_info=True)
        return []


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
        local_tools: Tools locais (BigQuery + validadores). Se None, usa lista vazia.
        cliente: Cliente ativo para renderizar o system prompt dinâmico.

    Returns:
        CompiledGraph — o agente compilado, pronto para .ainvoke() ou .astream().
    """
    llm = _build_llm()
    checkpointer = _build_checkpointer()
    system_prompt = get_prompt_for_client(cliente)

    # Combina tools locais (BigQuery, validadores) + tools MCP (Camilo)
    all_tools = list(local_tools or [])

    mcp_tools = await _load_mcp_tools()
    all_tools.extend(mcp_tools)

    agent = create_react_agent(
        model=llm,
        tools=all_tools,
        checkpointer=checkpointer,
        prompt=SystemMessage(content=system_prompt),
    )

    logger.info(
        "Agente LangGraph criado: model=%s, tools_local=%d, tools_mcp=%d, total=%d, cliente=%s",
        settings.llm.model_main,
        len(local_tools or []),
        len(mcp_tools),
        len(all_tools),
        cliente or "multi-cliente",
    )

    return agent


# ============================================================================
# Instância global do agente (lazy init)
# ============================================================================

_agent_instance = None


async def get_agent(tools: list[Any] | None = None, cliente: str | None = None):
    """Retorna a instância do agente (singleton lazy).

    Em produção, o agente é criado uma vez e reutilizado.
    O cliente pode ser trocado por request via config do thread.
    """
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = await build_agent(local_tools=tools, cliente=cliente)
    return _agent_instance
