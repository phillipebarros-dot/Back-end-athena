# Athena Backend

Backend do assistente Athena da OpusMultipla. Substitui os 7 webhooks do n8n por uma API FastAPI unificada com agente LangGraph, integracao MCP e persistencia BigQuery/PostgreSQL.


## Arquitetura

O backend e uma API FastAPI que recebe requests do frontend Next.js e orquestra um agente LLM (Claude Anthropic) via LangGraph. O agente decide quais tools chamar (BigQuery direto ou MCPs remotos), processa os dados e devolve respostas formatadas em markdown com tabelas GFM.

```
Frontend (Next.js)
    |
    v
FastAPI (Cloud Run)
    |
    +-- Auth Middleware (Bearer Token)
    +-- Rate Limiter (30 req/min)
    |
    v
LangGraph Agent (create_react_agent)
    |
    +-- Claude Sonnet 4 (LLM principal)
    +-- Claude Haiku 4.5 (sumarizador)
    |
    +-- Tools MCP (4 servers Cloud Run)
    |     +-- publi (MySQL ERP ao vivo)
    |     +-- pesquisas (BigQuery audiencia IBOPE/Radio/OOH/TGI)
    |     +-- export (Google Sheets/CSV)
    |     +-- midia_online (BigQuery digital)
    |
    +-- Tools BigQuery locais (8 tools fallback)
    |     +-- financeiro, orcamento, operacional
    |     +-- tabela_tv, briefing, fornecedores
    |     +-- ooh, tgi_choices
    |
    +-- Tools utilitarias (6 tools)
          +-- validar_veiculo (~60 mapeamentos)
          +-- validar_cliente (Boticario, Eudora, QDB, etc)
          +-- converter_periodo (ciclos C01 a C06)
          +-- calcular_indicadores (CPM, GRP, TRP, etc)
          +-- buscar_web (Google Custom Search)
          +-- exportar_sheets (MCP export)
    |
    v
Persistencia
    +-- BigQuery (conversas, mensagens, feedback, logs, learnings)
    +-- PostgreSQL (LangGraph checkpointer via Cloud SQL)
```


## Estrutura de arquivos

```
Back-end-athena/
  app/
    __init__.py
    main.py              # FastAPI app, endpoints, middlewares
    config.py            # Configuracao centralizada (dataclasses tipadas)
    models.py            # Pydantic models (request/response)
    agent/
      __init__.py
      graph.py           # LangGraph agent builder, MCP client, checkpointer
      prompts.py         # System prompts dinamicos por cliente
      tools.py           # 14 tools LangChain (8 BQ + 6 utilitarias)
    services/
      __init__.py
      bq_service.py      # Servico BigQuery (CRUD conversas, mensagens, audit)
      response_validator.py  # Validacao de respostas do agente
  Dockerfile             # Container Python 3.11
  pyproject.toml         # Dependencias e configuracao do projeto
  .env.example           # Template de variaveis de ambiente
  test_chat.py           # Teste basico do endpoint /chat
```


## Endpoints da API

| Metodo | Rota | Descricao |
|--------|------|-----------|
| POST | /chat | Envia mensagem ao agente, recebe resposta com tabelas, sources e SQL |
| POST | /conversations | CRUD de conversas (list, create, updateTitle, delete) |
| POST | /history | Recupera historico de mensagens de uma conversa |
| POST | /save-message | Persiste mensagem no BigQuery |
| POST | /compact | Compacta conversas com 20+ mensagens via Haiku |
| POST | /feedback | Registra feedback positivo/negativo com comentario |
| POST | /audit | Metricas para dashboard admin (KPIs, top users, feedback, MCP health) |
| POST | /users | Gestao de usuarios e RBAC (list, check, update_role) |
| POST | /search-entities | Autocomplete de entidades (veiculo, programa, praca) |
| GET  | /list-clients | Lista dinamica de clientes/anunciantes |
| POST | /tts | Text to Speech via OpenAI API |
| POST | /export | Exporta dados para CSV/XLSX |
| GET  | /settings/domains | Lista dominios permitidos |
| POST | /settings/domains/add | Adiciona dominio permitido |
| POST | /settings/domains/remove | Remove dominio permitido |
| GET  | /settings/synonyms | Lista sinonimos do dicionario |
| POST | /settings/synonyms/add | Adiciona sinonimo |
| POST | /settings/synonyms/remove | Remove sinonimo |
| GET  | /health | Health check do servico |


## Conexao com os MCPs

O backend conecta nos 4 MCPs do Camilo usando a biblioteca `langchain-mcp-adapters`. No startup do agente, a funcao `_load_mcp_tools()` em `graph.py` cria um `MultiServerMCPClient` passando as URLs dos 4 MCPs com transporte `streamable_http` e autenticacao Bearer Token. O adapter descobre automaticamente todas as tools que cada MCP expoe via protocolo MCP e as converte em tools LangChain com prefixo do server (ex: `publi__consultar_mysql`). Se os MCPs estiverem indisponiveis, o sistema ativa um fallback automatico (`_load_tools_with_fallback`) que carrega as 14 tools BigQuery locais definidas em `tools.py`.

URLs dos MCPs (defaults em config.py, podem ser sobrescritas por env vars):

| MCP | URL | Funcao |
|-----|-----|--------|
| publi | https://mcp-publi-mysql-642859299503.us-central1.run.app/mcp | ERP ao vivo (MySQL) |
| pesquisas | https://mcp-pesquisas-642859299503.us-central1.run.app/mcp | Audiencia IBOPE, Radio, OOH, TGI |
| export | https://mcp-export-642859299503.us-central1.run.app/mcp | Exportacao Google Sheets |
| midia_online | https://mcp-midia-online-642859299503.us-central1.run.app/mcp | Performance digital |


## Agente LangGraph

O agente usa `create_react_agent` do LangGraph com os seguintes componentes:

1. **LLM principal**: Claude Sonnet 4 (temperature 0.15, 4096 tokens)
2. **Sumarizador**: Claude Haiku 4.5 (temperature 0.1, 1024 tokens) para compactacao de memoria
3. **Checkpointer**: PostgreSQL via Cloud SQL (fallback para MemorySaver em memoria)
4. **Cache de agentes**: Um agente por cliente em cache (`_agent_cache`), protegido por `asyncio.Lock`
5. **System prompt**: Dinamico por cliente, renderizado por Jinja2 a partir de `prompts.py`
6. **Max iteracoes**: 20 iteracoes de tool calls por request

O agente suporta multi-tenant. Quando o frontend manda `client: "O Boticario"`, o backend cria (ou reutiliza do cache) um agente com system prompt especifico para aquele cliente. O MCP resolve internamente o codigo do cliente para filtrar dados.


## Tools do Agente

### BigQuery (8 tools, fallback quando MCP indisponivel)

| Tool | Tabela/Funcao |
|------|---------------|
| bigquery_financeiro | PIs, investimentos, comissoes |
| bigquery_orcamento | Orcamentos de campanha |
| bigquery_operacional | Dados operacionais de veiculacao |
| bigquery_tabela_tv | Tabelas de preco de TV (com formatacao R$) |
| bigquery_briefing | Briefings de campanha |
| bigquery_fornecedores | Cadastro de fornecedores/veiculos |
| bigquery_ooh | Dados Out of Home |
| tgi_choices | Pesquisas TGI (segmentacao/consumo) |

### Utilitarias (6 tools)

| Tool | Funcao |
|------|--------|
| validar_veiculo | Normaliza ~60 nomes comerciais para nomes canonicos BigQuery |
| validar_cliente | Mapeia apelidos para codigos de cliente (Boticario, Eudora, QDB, etc) |
| converter_periodo | Converte datas, meses e ciclos (C01 a C06) para filtros SQL |
| calcular_indicadores | Calcula CPM, GRP, TRP, cobertura, frequencia |
| buscar_web | Pesquisa web via Google Custom Search API |
| exportar_sheets | Exporta dados para Google Sheets via MCP export |


## Configuracao

Todas as configuracoes sao gerenciadas por `config.py` via dataclasses tipadas. Valores vem de variaveis de ambiente (`.env` ou Cloud Run secrets).

### Secrets obrigatorios (Google Secret Manager)

| Secret | Descricao |
|--------|-----------|
| ANTHROPIC_API_KEY | Chave da API Anthropic (Claude) |
| OPENAI_API_KEY | Chave da API OpenAI (TTS) |
| MCP_AUTH_TOKEN | Token Bearer para autenticacao nos MCPs |
| CLOUDSQL_PASSWORD | Senha do PostgreSQL (Cloud SQL) |
| POSTGRES_URI | URI de conexao PostgreSQL (alternativa ao Cloud SQL Connector) |

### Variaveis de ambiente opcionais

| Variavel | Default | Descricao |
|----------|---------|-----------|
| BQ_PROJECT_ID | athenaai-opus | Projeto BigQuery dos dados de midia |
| BQ_PROJECT_PERSISTENCE | sheetsintegration-451500 | Projeto BigQuery da persistencia |
| BQ_DATASET_MEDIA | ath_boticario | Dataset de midia |
| BQ_DATASET_PERSISTENCE | pj_boti | Dataset de persistencia |
| LLM_MODEL_MAIN | claude-sonnet-4-6 | Modelo LLM principal |
| LLM_MODEL_SUMMARIZER | claude-haiku-4-5 | Modelo sumarizador |
| RATE_LIMIT_PER_MINUTE | 30 | Limite de requests por minuto |
| CORS_ALLOWED_ORIGINS | (frontend URLs) | Origens permitidas para CORS |
| PORT | 8080 | Porta do servidor |


## Deploy

O backend roda no Google Cloud Run. O deploy e feito via source deploy:

```bash
gcloud run deploy athena-backend-teste \
  --source=. \
  --region=us-central1 \
  --allow-unauthenticated \
  --timeout=300
```

Os secrets sao mapeados automaticamente do Google Secret Manager para env vars no Cloud Run:

```bash
gcloud run services update athena-backend-teste \
  --region=us-central1 \
  --update-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest
```


## Seguranca

1. Auth middleware valida Bearer Token em todos os requests (exceto /health)
2. Rate limiter: 30 requests por minuto por IP
3. Queries BigQuery: apenas SELECT permitido, LIMIT forcado, 1GB max billing
4. CORS restrito aos dominios do frontend
5. Admin verificado via BigQuery (tabela athena_users.role) com fallback para ADMIN_EMAILS
6. Input limitado a 4000 caracteres


## Desenvolvimento local

```bash
# Instalar dependencias
pip install -e ".[dev]"

# Configurar variaveis
cp .env.example .env
# Editar .env com as credenciais reais

# Rodar
uvicorn app.main:app --reload --port 8080
```


## Testes

```bash
pytest test_chat.py -v
```


## Autores

Phillipe Barros, Camilo Ferreira, Wesley Macena, Andrei Nogueira
Grupo OpusMultipla
