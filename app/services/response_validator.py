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
    """Pós-processa resposta do agente.

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

    # 3. Remove travessões duplos
    output = output.replace(" -- ", ". ")
    output = output.replace("--", " ")

    # 4. Corrige formato monetário
    output = _MONEY_WRONG_FORMAT.sub(r"R$ \1.\2,\3", output)

    result: dict[str, Any] = {"output": output.strip()}
    if attachment:
        result["attachment"] = attachment

    return result
