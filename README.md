# Athena Backend - API de Inteligencia de Midia e Planejamento (v3.3.0)

**Autor**: Phillipe Barros ([@phillipebarros-dot](https://github.com/phillipebarros-dot))  
**Organizacao**: Opus Multipla / Grupo OM  
**Versao**: 3.4.0 | **Licenca**: Proprietaria

Backend do assistente Athena. Um agente LLM especializado em midia, planejamento de comunicacao e gestao de investimentos publicitarios. Substitui os 7 webhooks do n8n original por uma API FastAPI unificada com agente LangGraph, integracao MCP, streaming SSE e persistencia BigQuery/PostgreSQL.

---

## Indice

- [Tecnologias](#tecnologias)
- [Arquitetura](#arquitetura)
- [Funcionalidades](#funcionalidades)
- [Endpoints da API](#endpoints-da-api)
- [Configuracao](#configuracao)
- [Deploy](#deploy)
- [Estrutura de Arquivos](#estrutura-de-arquivos)
- [Seguranca](#seguranca)
- [Changelog v3.4.0](#changelog-v340)
- [Changelog v3.3.0](#changelog-v330)
- [Roadmap](#roadmap)

---

## Tecnologias

| Camada | Tecnologia | Versao | Proposito |
|--------|-----------|--------|-----------|
| Runtime | Python | 3.11+ | Linguagem principal |
| Framework | FastAPI | 0.115+ | API REST assincrona |
| Servidor | Uvicorn | 0.32+ | ASGI server com hot reload |
| LLM Principal | Claude Sonnet 4 | via Anthropic API | Raciocinio, queries e visao (multimodal) |
| LLM Sumarizador | Claude Haiku 4.5 | via Anthropic API | Compactacao de contexto |
| Orquestrador | LangGraph | 1.0+ | Grafo de agente ReAct |
| MCP | langchain-mcp-adapters | 0.3+ | Conexao com 4 MCP servers |
| Data Warehouse | Google BigQuery | 3.25+ | Consultas SQL e persistencia |
| Checkpointer | PostgreSQL (Cloud SQL) | via psycopg3 | Estado do agente entre turnos |
| TTS Saori | Gemini 2.5 Flash TTS | via google-genai | Voz Aoede feminina ultra-realista (apenas Saori) |
| TTS Saori Fallback | Google Cloud Neural2 | pt-BR-Neural2-C | Fallback Neural2 feminina (apenas Saori) |
| TTS Chat | OpenAI TTS | tts-1-hd, voz onyx | Audio no chat quando usuario solicitar |
| Export | gspread | 6.0+ | Export nativo Google Sheets |
| Export | openpyxl | 3.1+ | Export XLSX (Excel) |
| PDF | pdfplumber | 0.11+ | Upload e extracao de PDFs |
| Validacao | Pydantic | 2.0+ | Schemas e validacao de dados |
| Templates | Jinja2 | 3.1+ | Renderizacao de prompts |
| HTTP | httpx | 0.27+ | Chamadas HTTP assincronas |
| SSE | StreamingResponse | FastAPI | Server-Sent Events (POST /chat/stream) |
| Cache | cachetools | 5.4+ | TTLCache in-memory |
| Infra | Google Cloud Run | - | Serverless containers |
| CI/CD | Docker | - | Containerizacao |

---

## Arquitetura

```
Frontend (Next.js / Cloud Run)
    |
     v
FastAPI (Cloud Run)
     |
     +-- Auth Middleware (Bearer Token + HMAC)
     +-- Rate Limiter (100 req/min por IP, TTLCache)
     |
     +-- POST /chat         (bloqueante: resposta completa + TTS inline)
     +-- POST /chat/stream  (SSE: tokens incrementais em tempo real)
     v
LangGraph Agent (create_react_agent)
    |
    +-- Claude Sonnet 4 (LLM principal: raciocinio + SQL)
    +-- Claude Haiku 4.5 (sumarizador de contexto)
    |
    +-- Tools MCP (4 servers remotos no Cloud Run)
    |     +-- publi-mysql    > ERP Publi ao vivo (MySQL)
    |     +-- pesquisas      > BigQuery (IBOPE, Radio, OOH, TGI)
    |     +-- midia-online   > BigQuery (Meta, Google, TikTok)
    |     +-- export         > Google Sheets / CSV
    |
    +-- Tools BigQuery locais (8 tools fallback)
    |     +-- financeiro (PIs, investimentos, comissoes)
    |     +-- orcamento (orcamentos, pedidos de producao)
    |     +-- operacional (tarefas, pautas, prazos, equipes)
    |     +-- tabela_tv (precos TV, LIMIT 100)
    |     +-- briefing (briefings de campanha)
    |     +-- fornecedores (veiculos e fornecedores)
    |     +-- ooh (inventario out-of-home)
    |     +-- tgi_choices (pesquisa TGI/Choices)
    |
    +-- Tools utilitarias (7 tools)
    |     +-- cod_clientes (resolucao de marca para codigo)
    |     +-- converter_ciclo (ciclo C01-C06 para datas)
    |     +-- ciclo_de_data (data para ciclo)
    |     +-- enriquecer_grupo_mkt (mercado para grupo)
    |     +-- buscar_web (Google Custom Search)
    |     +-- exportar_sheets (gspread nativo)
    |     +-- exportar_sheet_sql (export SQL direto)
    |
    v
Persistencia
    +-- BigQuery (conversas, mensagens, feedback, audit, learnings, users)
    +-- PostgreSQL / Cloud SQL (LangGraph checkpointer)
```

---

## Funcionalidades

### Consulta de Dados Corporativos
- Financeiro: PIs, investimentos por veiculo/meio/periodo, comissoes, valores liquidos e brutos
- Orcamento: Orcamentos e pedidos de producao
- Operacional: Tarefas abertas, prazos, briefings, timesheet, equipes
- TV: Tabela de precos completa (100+ programas por mercado)
- Audiencia: IBOPE (TV), EasyMedia4 (Radio), inventario OOH
- TGI: Perfil de consumo, afinidade, penetracao
- Digital: Meta Ads, Google Ads, TikTok Ads (impressoes, cliques, CPM)

### Inteligencia do Agente
- Busca fuzzy: Nomes de programas com LIKE + variantes automaticas
- Desambiguacao geografica: Diferencia mercados Kantar (metro) vs estados
- Contexto de equipe: Lembra a equipe do usuario na conversa
- Compactacao automatica: Apos 20 mensagens, resume contexto
- Retry automatico: Corrige queries SQL que falham e retenta
- Fallback MCP -> Legacy: Se MCP falhar, ativa 14 tools BigQuery locais

### Export de Dados
- Google Sheets: Cria planilha nativa no Drive do usuario via OAuth (header azul marca)
- XLSX: Excel com headers estilizados azul marca e auto-width
- CSV: Fallback universal
- Export por SQL: Para datasets grandes (centenas/milhares de linhas)

### Voz (TTS) — Cadeia de 3 provedores
- Gemini 2.5 Flash TTS (voz Charon): Ultra-realista, masculina grave, emocoes naturais
- Google Cloud Neural2 (pt-BR-Neural2-B): Fallback de alta qualidade
- OpenAI TTS (tts-1-hd, voz onyx): Ultimo recurso

### Gestao
- Conversas: CRUD completo com historico persistente
- Feedback: Like/dislike com comentario por mensagem
- Auditoria: Log completo de queries, tokens, timestamps, KPIs, system stats, MCP health
- Admin: Gestao de dominios permitidos, sinonimos, usuarios (RBAC)
- Upload: PDF, XLSX, XLS, CSV (ate 10MB)

---

## Endpoints da API

| Metodo | Endpoint | Descricao |
|--------|----------|-----------|
| POST | /chat | Envia mensagem e recebe resposta completa (bloqueante, inclui TTS) |
| POST | /chat/stream | SSE streaming: tokens em tempo real via astream_events v2 |
| POST | /conversations | CRUD de conversas (list/create/update_title/delete) |
| POST | /history | Retorna historico de uma conversa |
| POST | /save-message | Persiste mensagem no BigQuery |
| POST | /feedback | Registra like/dislike com comentario |
| POST | /compact | Compacta contexto de uma conversa |
| POST | /tts | Text-to-Speech (Gemini > Google > OpenAI) |
| POST | /export | Exporta dados (Sheets/XLSX/CSV) |
| POST | /upload | Upload de PDFs/Excel/CSV |
| POST | /users | CRUD de usuarios (check/upsert/list/update_role) |
| POST | /audit | Metricas admin (kpis/recent_activity/feedback/top_users/system_stats/mcp_health) |
| POST | /search-entities | Autocomplete de entidades (veiculo, programa, praca) |
| POST | /list-clients | Lista clientes disponiveis |
| GET  | /health | Health check para Cloud Run |
| GET  | /settings/domains | Lista dominios permitidos |
| POST | /settings/domains/add | Adiciona dominio |
| POST | /settings/domains/remove | Remove dominio |
| GET  | /settings/synonyms | Lista sinonimos |
| POST | /settings/synonyms/add | Adiciona sinonimo |
| POST | /settings/synonyms/remove | Remove sinonimo |

---

## Configuracao

### Variaveis de Ambiente

```env
# Obrigatorias
ANTHROPIC_API_KEY=sk-ant-...          # Claude API
OPENAI_API_KEY=sk-proj-...            # TTS fallback
MCP_AUTH_TOKEN=token-dos-mcps         # Autenticacao dos MCP servers

# Google Cloud
GOOGLE_CLOUD_PROJECT=athenaai-opus
BQ_DATASET=ath_boticario

# Cloud SQL (Checkpointer)
CLOUDSQL_HOST=x.x.x.x                # IP do Cloud SQL
CLOUDSQL_USER=postgres
CLOUDSQL_PASSWORD=...
CLOUDSQL_DB=athena
# OU
POSTGRES_URI=postgresql://...         # Alternativa: URI completa

# CORS
CORS_ALLOWED_ORIGINS=https://athena-frontend-xxx.run.app

# MCP Servers
MCP_PUBLI_URL=https://mcp-publi-xxx.run.app/mcp
MCP_PESQUISAS_URL=https://mcp-pesquisas-xxx.run.app/mcp
MCP_MIDIA_URL=https://mcp-midia-xxx.run.app/mcp
MCP_EXPORT_URL=https://mcp-export-xxx.run.app/mcp

# Opcionais
DEBUG=false
LANGSMITH_API_KEY=...                 # Observabilidade LangSmith
```

---

## Deploy

```bash
# Build e deploy no Cloud Run
gcloud run deploy athena-backend \
  --source=. \
  --region=us-central1 \
  --allow-unauthenticated \
  --timeout=300
```

---

## Estrutura de Arquivos

```
Back-end-athena/
  app/
    main.py              # FastAPI app + todos os endpoints (~1370 linhas)
    config.py            # Settings (Pydantic BaseSettings)
    models.py            # Schemas Pydantic (request/response + sanitizacao)
    agent/
      graph.py           # LangGraph (create_react_agent + MCP + fallback + prompt caching)
      tools.py           # 15 tools (BigQuery + utilitarias)
      prompts.py         # System prompt (regras de negocio, Jinja2)
    services/
      bq_service.py      # Servico BigQuery (CRUD + cache TTL + client singleton)
      tts_service.py     # TTS cadeia Gemini > Google Cloud > OpenAI
      response_validator.py  # Pos-processamento (emojis, monetario, leak detection)
  docs/
    STREAMING_ARCHITECTURE.md  # Documentacao tecnica SSE (Anthropic, GCP, LangGraph)
  Dockerfile             # Container para Cloud Run
  pyproject.toml         # Dependencias e metadata
  requirements.txt       # Lock de dependencias
  README.md              # Este arquivo
```

---

## Seguranca

- Auth: Bearer Token via HMAC (comparacao em tempo constante)
- Rate Limiting: 100 req/min por IP (TTLCache, sem memory leak)
- CORS: Restrito ao dominio do frontend
- Secrets: Todas as chaves via Google Secret Manager
- ADC: Application Default Credentials no Cloud Run
- SQL Injection: Queries parametrizadas (@params) em todos os endpoints
- Prompt Injection: Sanitizacao de homoglyphs, caracteres invisíveis, patterns maliciosos
- Response Leak: Deteccao e redacao de system prompt vazado nas respostas
- Error Handling: Traceback completo so em DEBUG=true (producao retorna mensagem generica)
- Audit: Log completo de queries e interacoes no BigQuery

---

## Changelog v3.4.0

### SSE Streaming (Implementado)
- **POST /chat/stream**: Endpoint SSE usando LangGraph `astream_events(version="v2")` com `StreamingResponse`
- **Protocolo SSE**: Eventos JSON (`tok`, `tool`, `done`, `err`) via `data: {...}\n\n`
- **Cloud Run otimizado**: Header `X-Accel-Buffering: no` desabilita buffering do proxy
- **Prompt Caching**: Header `anthropic-beta: prompt-caching-2024-07-31` no ChatAnthropic (~90% economia no system prompt)
- **Endpoint /chat preservado**: Fallback para TTS inline (retorna audio base64)
- **Documentacao tecnica**: `docs/STREAMING_ARCHITECTURE.md` com refs oficiais (Anthropic, GCP, LangGraph)

---

## Changelog v3.3.0

### Correcoes de Seguranca
- **Traceback oculto em producao**: Global exception handler agora retorna mensagem generica (antes: expunha caminhos, versoes, URIs)
- **IP hardcoded removido**: Cloud SQL IP (34.59.118.159) nao e mais default; CLOUDSQL_HOST obrigatoria
- **Rate limit memory leak**: Trocado defaultdict (crescia infinitamente) por TTLCache com maxsize=10000

### Correcoes de Consistencia Visual
- **Sheets header**: Cor de fundo do header mudou de vermelho (#C41E1E) para azul marca (#4A90D9)
- **XLSX header**: Idem — PatternFill de C41E1E para 4A90D9

### Correcoes de Bug
- **_client_persistence NoneType**: Propriedade BigQuery para persistencia nunca era inicializada; 5 endpoints (system_stats, domains CRUD) crashavam com AttributeError. Adicionada propriedade lazy `client_persistence`

### Refatoracoes
- **TTS Service**: Extraida logica duplicada (~164 linhas) de /chat e /tts para services/tts_service.py. Cadeia de fallback em modulo reutilizavel
- **BigQuery Client Consolidation**: Eliminados 5 `bigquery.Client()` avulsos em search-entities, synonyms CRUD e list_clients. Todos agora usam o singleton via bq_service (client e client_persistence)

---

## Roadmap

### OAuth Refresh Token (Prioridade Media)
Implementar refresh_token flow para renovar google_access_token automaticamente. Atualmente o token expira em 1 hora e exports para Sheets falham.

### Admin Endpoints Pendentes (Prioridade Baixa)
- POST /audit?query=cost_metrics: Metricas de custo por modelo LLM
- Endpoints para "Criar regra" e "Ignorar" feedback (botoes desabilitados no frontend)
