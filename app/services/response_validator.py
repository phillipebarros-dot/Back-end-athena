"""
Validador de Resposta — pós-processamento da saída do agente.

Equivale ao node "Validador de Resposta" do n8n (tipo: code).
Ações:
1. Detecta e extrai attachment JSON embutido (PDF/Sheets)
2. Remove emojis
3. Remove travessões duplos (--)
4. Corrige formatação monetária R$
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Regex emojis (Unicode ranges — portado do Validador de Resposta do n8n)
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"   # emoticons
    "\U0001F300-\U0001F5FF"   # misc symbols
    "\U0001F680-\U0001F6FF"   # transport
    "\U0001F1E0-\U0001F1FF"   # flags
    "\U00002600-\U000026FF"   # misc
    "\U00002700-\U000027BF"   # dingbats
    "]+",
    flags=re.UNICODE,
)

# Fix formato monetário: R$ 1,234.56 → R$ 1.234,56
_MONEY_WRONG_FORMAT = re.compile(r"R\$\s*(\d{1,3}),(\d{3})\.(\d{2})")

# Attachment JSON embutido na resposta
_ATTACHMENT_PATTERN = re.compile(
    r'\{"status"\s*:\s*"success"\s*,\s*"file_type"\s*:\s*"(sheet|pdf)"[^}]+\}'
)


def validate_response(output: str) -> dict[str, Any]:
    """Pos-processa resposta do agente.

    Inclui validacao de seguranca contra leak de system prompt.

    Returns:
        Dict com 'output' limpo e opcional 'attachment'.
    """
    attachment = None

    # 1. Detectar artefato JSON embutido (PDF/Sheets export)
    json_match = _ATTACHMENT_PATTERN.search(output)
    if json_match:
        try:
            attachment = json.loads(json_match.group(0))
            output = output.replace(json_match.group(0), "").strip()
        except json.JSONDecodeError:
            pass

    # 2. Remove emojis
    output = _EMOJI_PATTERN.sub("", output)

    # 3. Remove travessoes estilisticos (isolados ou em-dash)
    #    Preserva -- dentro de ranges, SQL comments, tabelas ASCII
    output = re.sub(r'(?<=\s)--(?=\s)', '.', output)   # " -- " isolado -> "."
    output = output.replace('\u2014', ' \u2014 ')                  # em-dash: normaliza espacamento

    # 4. Corrige formato monetario
    output = _MONEY_WRONG_FORMAT.sub(r"R$ \1.\2,\3", output)

    # 5. SEGURANCA: detectar e redatar leak de system prompt/instrucoes internas
    output = _redact_prompt_leak(output)

    result: dict[str, Any] = {"output": output.strip()}
    if attachment:
        result["attachment"] = attachment

    return result


# Padroes que indicam vazamento de system prompt ou config interna
_LEAK_PATTERNS = re.compile(
    r"("
    r"REGRAS?\s+INVIOLA"
    r"|=== SEGURANCA ==="
    r"|=== COMUNICACAO ==="
    r"|=== FONTES DE DADOS ==="
    r"|=== SAUDACAO ==="
    r"|ATHENA v\d+\.\d+"
    r"|system_prompt|render_system_prompt"
    r"|publi-mysql|midia-online|pesquisas\s*\(BigQuery\)"
    r"|consultar_mysql|consultar_bigquery|exportar_sheet_sql"
    r"|abrir_catalogo|cod_clientes|enriquecer_grupo_mkt"
    r"|athenaai-opus\.ath_boticario"
    r"|sheetsintegration-\d+\.boti_on"
    r"|COD_CLIENT\s+IN"
    r"|ANTHROPIC_API_KEY|OPENAI_API_KEY|GOOGLE_.*_KEY"
    r"|Bearer\s+[A-Za-z0-9\-_.]+"
    r")",
    re.IGNORECASE,
)


def _redact_prompt_leak(output: str) -> str:
    """Remove trechos que parecem leak de instrucoes internas."""
    if _LEAK_PATTERNS.search(output):
        logger.warning("Detectado possivel leak de system prompt na resposta")
        # Redatar cada match
        output = _LEAK_PATTERNS.sub("[REDACTED]", output)
    return output

