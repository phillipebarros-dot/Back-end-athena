# Build stage — instalar dependencias
FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
# Cria stub __init__.py para satisfazer setuptools (app/ vem só no runtime)
RUN mkdir -p app && touch app/__init__.py
RUN pip install --no-cache-dir .

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Cria usuario nao-root para seguranca
RUN groupadd -r athena && useradd --no-log-init -r -g athena athena

# Copia dependencias instaladas
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copia codigo da aplicacao
COPY app/ app/

# Cloud Run espera PORT 8080
ENV PORT=8080
ENV HOST=0.0.0.0
# Seguranca: DEBUG off por padrao na imagem
ENV DEBUG=false

EXPOSE 8080

USER athena

# Uvicorn com workers para producao
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
