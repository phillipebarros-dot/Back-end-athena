# 📚 Documentação Técnica — Athena Streaming Architecture

> Baseado na documentação OFICIAL de: Anthropic API, GCP Cloud Run, LangGraph, Next.js App Router.
> Última atualização: 2026-08-01

---

## 1. Fontes Oficiais Consultadas

| Tecnologia | Documentação | Seção |
|-----------|-------------|-------|
| **Anthropic** | platform.claude.com/docs/en/build-with-claude/streaming | Streaming Messages via SSE |
| **Anthropic** | platform.claude.com/docs/en/build-with-claude/prompt-caching | Prompt Caching |
| **Anthropic** | platform.claude.com/docs/en/build-with-claude/context-windows | Context Windows e Compaction |
| **GCP Cloud Run** | cloud.google.com/run/docs/triggering/https-request#streaming | HTTP Streaming Responses |
| **GCP Cloud Run** | cloud.google.com/run/docs/configuring/min-instances | Min Instances (anti cold-start) |
| **GCP Cloud Run** | cloud.google.com/run/docs/configuring/cpu-allocation | CPU Always Allocated |
| **LangGraph** | langchain-ai.github.io/langgraph/how-tos/streaming | astream_events v2 |
| **Next.js** | nextjs.org/docs/app/building-your-application/routing/route-handlers | Streaming Route Handlers |

---

## 2. Anthropic Streaming API

### Eventos SSE (sequência oficial)

```
event: message_start       → Metadados (model, usage)
event: content_block_start → Início de bloco (text, tool_use)
event: content_block_delta → Chunks de texto (type: "text_delta")
event: content_block_stop  → Fim do bloco
event: message_delta       → Stop reason + usage final
event: message_stop        → Fim da mensagem
```

### Prompt Caching

- System prompt ≥1024 tokens = cacheável
- Cache dura 5 min (renova a cada uso)
- Custo write: 1.25x | read: 0.1x do normal
- Header: `anthropic-beta: prompt-caching-2024-07-31`

### Context Management

- Claude Sonnet 4: 200K tokens context window
- Compaction API beta: `context-management-2025-06-27`
- `clear_tool_uses`: Remove tool results antigos automaticamente

---

## 3. GCP Cloud Run — Streaming

### Configuração para SSE

1. Status 200 obrigatório (não-200 são bufferizados)
2. Header `X-Accel-Buffering: no` desabilita buffering do proxy
3. Timeout default: 300s, máximo: 3600s

### Anti Cold-Start

```bash
# min-instances=1: Mantém 1 container quente
gcloud run services update athena-backend \
  --region us-central1 \
  --min-instances 1

# startup-cpu-boost: Boost de CPU durante startup
gcloud run services update athena-backend \
  --region us-central1 \
  --cpu-boost

# CPU always allocated: Necessário para background tasks
gcloud run services update athena-backend \
  --region us-central1 \
  --no-cpu-throttling
```

### Custos

| Config | Custo mensal |
|--------|-------------|
| min-instances=0 (atual) | $0 idle |
| min-instances=1, 1vCPU, 1Gi | ~$15-25/mês |
| + no-cpu-throttling | ~$35-50/mês |

---

## 4. LangGraph — astream_events v2

```python
async for event in agent.astream_events(
    {"messages": [("user", message)]},
    config={"configurable": {"thread_id": conversation_id}},
    version="v2",  # v1 deprecated
):
    kind = event["event"]
    
    if kind == "on_chat_model_stream":
        content = event["data"]["chunk"].content
        # Token sendo gerado
    
    elif kind == "on_tool_start":
        tool_name = event["name"]
        # Tool call iniciado
    
    elif kind == "on_tool_end":
        tool_name = event["name"]
        # Tool call finalizado
```

### Checkpointer (Postgres)

O estado da conversa é persistido no Postgres via `AsyncPostgresSaver`.
O `thread_id` = `conversation_id` identifica a conversa.
Se o user sair e voltar, o agente retoma de onde parou.

---

## 5. Next.js — SSE Proxy

```typescript
// Route handler que faz proxy do SSE do backend
export async function POST(req: NextRequest) {
    const body = await req.json();
    const res = await fetch(`${BACKEND_URL}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
        body: JSON.stringify(body),
        // NÃO passa req.signal se quer que backend continue
    });
    
    return new Response(res.body, {
        headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
        },
    });
}
```

### Abort Handling

- `req.signal` = AbortSignal padrão
- Client desconecta → signal dispara `abort`
- Para preservar geração: NÃO propagar signal ao backend
- Para economizar: propagar signal (backend para de processar)

---

## 6. Protocolo SSE da Athena

### Eventos

```
data: {"t":"tok","c":"texto"}              → Token do LLM
data: {"t":"tool","n":"nome","s":"start"}  → Tool call iniciado
data: {"t":"tool","n":"nome","s":"end"}    → Tool call finalizado
data: {"t":"done","c":"texto_completo"}    → Fim da geração
data: {"t":"err","c":"mensagem"}           → Erro
```

### Sequência de uma request

```
1. Frontend POST /api/athena/chat/stream
2. Next.js proxy → Backend POST /chat/stream
3. Backend chama agent.astream_events()
4. Loop:
   a. Claude gera token → SSE {"t":"tok"}
   b. Claude chama tool → SSE {"t":"tool","s":"start"}
   c. Tool retorna → SSE {"t":"tool","s":"end"}
   d. Claude continua gerando → SSE {"t":"tok"}
5. Fim → SSE {"t":"done"}
6. Frontend salva mensagem no histórico
```
