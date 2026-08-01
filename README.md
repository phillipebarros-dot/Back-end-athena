# Athena Backend - API de Inteligencia de Midia e Planejamento

**Autor**: Phillipe Barros ([@phillipebarros-dot](https://github.com/phillipebarros-dot))  
**Organizacao**: Opus Multipla / Grupo OM  
**Versao**: 3.2.0 | **Licenca**: Proprietaria

Backend do assistente Athena. Um agente LLM especializado em midia, planejamento de comunicacao e gestao de investimentos publicitarios. Substitui os 7 webhooks do n8n original por uma API FastAPI unificada com agente LangGraph, integracao MCP e persistencia BigQuery/PostgreSQL.

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

---

## Tecnologias

| Camada | Tecnologia | Versao | Proposito |
|--------|-----------|--------|-----------|
| Runtime | Python | 3.11+ | Linguagem principal |
| Framework | FastAPI | 0.115+ | API REST assincrona |
| Servidor | Uvicorn | 0.32+ | ASGI server com hot reload |
| LLM Principal | Claude Sonnet 4 | via Anthropic API | Raciocinio e geracao de queries |
| LLM Sumarizador | Claude Haiku 4.5 | via Anthropic API | Compactacao de contexto |
| Orquestrador | LangGraph | 1.0+ | Grafo de agente ReAct |
| MCP | langchain-mcp-adapters | 0.3+ | Conexao com 4 MCP servers |
| Data Warehouse | Google BigQuery | 3.25+ | Consultas SQL e persistencia |
| Checkpointer | PostgreSQL (Cloud SQL) | via psycopg3 | Estado do agente entre turnos |
| TTS Principal | Gemini 2.5 Flash TTS | via google-genai | Voz Charon ultra-realista (masculina grave) |
| TTS Fallback 1 | Google Cloud Neural2 | pt-BR-Neural2-B | Fallback Neural2 (masculina) |
| TTS Fallback 2 | OpenAI TTS | tts-1-hd, voz onyx | Ultimo recurso (masculina grave) |
| Export | gspread | 6.0+ | Export nativo Google Sheets |
| Export | openpyxl | 3.1+ | Export XLSX (Excel) |
| PDF | pdfplumber | 0.11+ | Upload e extracao de PDFs |
| Validacao | Pydantic | 2.0+ | Schemas e validacao de dados |
| Templates | Jinja2 | 3.1+ | Renderizacao de prompts |
| HTTP | httpx | 0.27+ | Chamadas HTTP assincronas |
| SSE | sse-starlette | 2.0+ | Server-Sent Events (streaming) |
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
    +-- Rate Limiter (100 req/min por IP)
    |
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
    +-- BigQuery (conversas, mensagens, feedback, audit, learnings)
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

### Export de Dados
- Google Sheets: Cria planilha nativa no Drive do usuario via OAuth
- XLSX: Excel com headers estilizados e auto-width
- CSV: Fallback universal
- Export por SQL: Para datasets grandes (centenas/milhares de linhas)

### Voz (TTS)
- Text-to-Speech: Converte respostas em audio via OpenAI (modelo tts-1-hd, voz nova)
- Speech-to-Text: Web Speech API no frontend (Chrome)

### Gestao
- Conversas: CRUD completo com historico persistente
- Feedback: Like/dislike com comentario por mensagem
- Auditoria: Log completo de queries, tokens, timestamps
- Admin: Gestao de dominios permitidos, sinonimos, usuarios

---

## Endpoints da API

| Metodo | Endpoint | Descricao |
|--------|----------|-----------|
| POST | /chat | Envia mensagem e recebe resposta do agente |
| POST | /conversations | Cria nova conversa |
| POST | /history | Retorna historico de uma conversa |
| POST | /save-message | Persiste mensagem no BigQuery |
| POST | /feedback | Registra like/dislike com comentario |
| POST | /compact | Compacta contexto de uma conversa |
| POST | /tts | Text-to-Speech (OpenAI tts-1-hd) |
| POST | /export | Exporta dados (Sheets/XLSX/CSV) |
| POST | /upload | Upload de PDFs/Excel/CSV |
| POST | /resume | Retorna ultima mensagem de uma conversa |
| POST | /users | CRUD de usuarios (check/upsert/list/update_role) |
| POST | /audit | Busca logs de auditoria |
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
OPENAI_API_KEY=sk-proj-...            # TTS (tts-1-hd, voz nova)
MCP_AUTH_TOKEN=token-dos-mcps         # Autenticacao dos MCP servers
POSTGRES_URI=postgresql://...         # Cloud SQL (checkpointer)

# Google Cloud
GOOGLE_CLOUD_PROJECT=athenaai-opus
BQ_DATASET=ath_boticario

# CORS
CORS_ALLOWED_ORIGINS=https://athena-frontend-xxx.run.app

# MCP Servers
MCP_PUBLI_URL=https://mcp-publi-xxx.run.app
MCP_PESQUISAS_URL=https://mcp-pesquisas-xxx.run.app
MCP_MIDIA_URL=https://mcp-midia-xxx.run.app
MCP_EXPORT_URL=https://mcp-export-xxx.run.app

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
    main.py              # FastAPI app + todos os endpoints
    config.py            # Settings (Pydantic BaseSettings)
    models.py            # Schemas Pydantic (request/response)
    agent/
      graph.py           # LangGraph (create_react_agent + MCP)
      tools.py           # 15 tools (BigQuery + utilitarias)
      prompts.py         # System prompt (regras de negocio)
    services/
      bq_service.py      # Servico BigQuery (CRUD tabelas)
  Dockerfile             # Container para Cloud Run
  pyproject.toml         # Dependencias e metadata
  requirements.txt       # Lock de dependencias
  README.md              # Este arquivo
```

---

## Seguranca

- Auth: Bearer Token via HMAC (comparacao em tempo constante)
- Rate Limiting: 100 req/min por IP (middleware FastAPI)
- CORS: Restrito ao dominio do frontend
- Secrets: Todas as chaves via Google Secret Manager
- ADC: Application Default Credentials no Cloud Run
- Audit: Log completo de queries e interacoes no BigQuery
