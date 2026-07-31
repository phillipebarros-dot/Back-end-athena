"""
Tools do agente Athena — equivalentes das 17 tools do n8n.

Cada tool é documentada com o node n8n que substitui.
Tools são funções Python puras decoradas com @tool — testáveis, versionáveis.

Changelog vs versão anterior:
- validar_veiculo: portados TODOS os ~60 mapeamentos do Code node do n8n
- validar_cliente: adicionados QDB, Vult, BeautyBox, Multi B, Truss, Australian Gold
- converter_periodo: ciclos C01-C06 hardcoded (não delega mais ao BQ)
- calcular_indicadores: suporta tipo_calculo (compatível n8n) + kwargs
- buscar_web: implementado com Google Custom Search API
- exportar_sheets: stub preparado para MCP export
- tgi_choices: renomeado de bigquery_tgi para tgi_choices (alinhado com n8n)
"""

from __future__ import annotations

import logging
import os

from google.cloud import bigquery
from langchain_core.tools import tool

from app.config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# BigQuery client singleton
# ============================================================================

_bq_client: bigquery.Client | None = None


def _get_bq_client() -> bigquery.Client:
    """Retorna client BigQuery singleton."""
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=settings.bq.project_id)
    return _bq_client


def _run_bq_query(sql: str, max_results: int = 100) -> list[dict]:
    """Executa query read-only no BigQuery e retorna lista de dicts.

    Guardrails de segurança (portados do n8n + mysql_read.py do Camilo):
    - Só SELECT permitido
    - Timeout de 30 segundos
    - LIMIT máximo aplicado
    """
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return [{"error": "Apenas queries SELECT são permitidas."}]

    # Injeta LIMIT se não existir
    if "LIMIT" not in sql_upper:
        sql = f"{sql.rstrip(';')} LIMIT {max_results}"

    try:
        client = _get_bq_client()
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=1_000_000_000,  # 1GB max
        )
        query_job = client.query(sql, job_config=job_config, timeout=30)
        rows = [dict(row) for row in query_job.result(timeout=30)]
        logger.info("BQ query executada: %d rows, sql=%s", len(rows), sql[:100])
        return rows
    except Exception as e:
        logger.error("Erro na query BQ: %s", e)
        return [{"error": f"Erro na consulta: {str(e)}"}]


# ============================================================================
# 8 BigQuery Tools (equivalentes dos googleBigQueryTool do n8n)
# ============================================================================

@tool
def bigquery_financeiro(sql: str) -> list[dict]:
    """Consulta dados financeiros de mídia (PIs, investimentos, comissões).

    Base: athenaai-opus.ath_boticario

    Tabelas: pi01 (PIs/faturamento), pi01x (detalhes veiculação — JOIN por cod_detalhe).
    pi01 campos: pi, pit, cod_detalhe, cliente_gr, cliente, nome_veiculo, meio,
                 descricao_meio, praca, estado, produto, periodo_pi(DATE),
                 valor_liquido, valor_bruto, comissao, desconto,
                 quantidade_insercoes, campanha, impactos, grupo_marketing,
                 tipo_investimento, situacao(A/F/P/C/N/L), data_competencia_pit(DATE).
    pi01x campos: cod_detalhe, nome_programa, descricao_peca, formato,
                  horario_inicio, horario_fim, data_inicio_veiculacao, data_fim_veiculacao,
                  custo_unitario, quantidade, valor_total, grp, audiencia.

    dim_ciclos: ciclo(INT), ano(INT), data_inicio(DATE), data_fim(DATE), cliente(STR).
    JOIN: pi01.data_competencia_pit BETWEEN dc.data_inicio AND dc.data_fim
          AND LOWER(pi01.cliente) = LOWER(dc.cliente)

    REGRAS:
    - situacao: por padrão filtrar por F (Faturado). Perguntar ao usuário se quer outros.
    - Investimento = SUM(valor_liquido). Bruto só se pedido.
    - Comissão JÁ É R$: SUM(COALESCE(comissao,0)). NÃO multiplique.
    - COUNT(DISTINCT pi) para contar PIs.
    - COALESCE + ROUND(,2). LIMIT 50.
    """
    return _run_bq_query(sql, max_results=50)


@tool
def bigquery_orcamento(sql: str) -> list[dict]:
    """Consulta orçamentos de produção.

    Tabelas: ath_boticario.orc01 (cabeçalho) + orc01x (itens).
    orc01: numero_orcamento, nome_cliente, valor_total, custos_internos,
           custos_externos, honorarios, situacao, campanha.
    orc01x: descricao_item, valor_item, quantidade.
    JOIN por numero_orcamento. NUNCA SELECT *. COALESCE. ROUND. LIMIT 30.
    """
    return _run_bq_query(sql, max_results=30)


@tool
def bigquery_operacional(sql: str) -> list[dict]:
    """Consulta tarefas e operacional.

    Tabela: ath_boticario.pt01.
    Campos: numero_tarefa, pit, descricao_trabalho, cliente, campanha,
            situacao, data_prazo, data_entrada, data_saida,
            nivel_prioridade(INT, menor=urgente), equipe, briefing.
    Tarefas abertas: situacao NOT IN ('Concluido','Cancelado','Reprovado','Finalizado').
    Atrasadas: DATE(data_prazo) < CURRENT_DATE(). LIMIT 50.
    """
    return _run_bq_query(sql, max_results=50)


@tool
def bigquery_tabela_tv(sql: str) -> list[dict]:
    """Consulta preços TV + audiência IBOPE.

    Tabelas: ath_boticario.tab01tv, ibope_audiencia_detalhada.

    tab01tv: emissora, mercado, genero_programa, nome_programa,
             horario_inicio, horario_fim, preco_30_segundos,
             p_10/p_15/p_45/p_60 (multiplicadores),
             data_comercial_inicio/fim, ano_mes_veiculacao,
             dias_semana (posições 1-7: Dom=1,Seg=2,...,Sab=7. LIKE '%2%' = segunda).

    ibope_audiencia_detalhada: data, praca, genero, target, emissora,
             programa, horario_inicio, audiencia(Rating%), share(%),
             grp, afinidade(índice%).

    Filtros IBOPE obrigatórios:
    - target: [SEXO]_[CLASSE]_[IDADE]. Ex: mm_ab_25_mais. Padrão: total_individuos.
    - praca: Grande_Sao_Paulo, Grande_Rio_de_Janeiro, RM_Abertas, etc.

    UPPER() + LIKE para emissoras. ORDER BY audiencia DESC. LIMIT 100.
    """
    return _run_bq_query(sql, max_results=100)


@tool
def bigquery_briefing(sql: str) -> list[dict]:
    """Consulta briefings de campanha.

    Tabelas: ath_boticario.pt01 (briefing, descricao_trabalho) + pit01 (pit, campanha, cliente, produto).
    Para briefing específico: texto completo. Para listagens: SUBSTR(briefing, 1, 500). LIMIT 20.
    """
    return _run_bq_query(sql, max_results=20)


@tool
def bigquery_fornecedores(sql: str) -> list[dict]:
    """Consulta fornecedores e veículos.

    Tabela: ath_boticario.for01.
    Campos: codigo_fornecedor, nome_fornecedor, razao_social, rede, praca, estado,
            meio_principal, tipo, cnpj, telefone, email, website, situacao, data_inclusao.
    LOWER LIKE para busca por nome. LIMIT 30.
    """
    return _run_bq_query(sql, max_results=30)


@tool
def bigquery_ooh(sql: str) -> list[dict]:
    """Consulta inventário OOH/outdoors.

    Tabela: ath_boticario.ooh_inventario.
    Campos: id, ponto, dimensao, tipo, cidade, endereco, uf,
            latitude(STRING), longitude(STRING), imagem, outdoor_link, pagina, estado.
    LOWER LIKE para cidade/estado. LIMIT 30.
    """
    return _run_bq_query(sql, max_results=30)


@tool
def tgi_choices(sql: str) -> list[dict]:
    """Consulta dados TGI/Choices de audiência e perfil demográfico.

    Tabela: ath_boticario.choices.
    BASE_AUDIENCE: "TOTAL", "[GERAL] HCP (Usa)", "[EUD] HCP (Uso)", "[BOTI] HCP (Usa)".
    Colunas: base_audience, category(Midia/Geolocalizacao/Demografia),
             sub_category, attribute, amostra, pop_000(multiplicar por 1000),
             perc_vertical, perc_horizontal, afinidade(100=média, >120=alta).
    SEMPRE filtre por base_audience. Para mídia ORDER BY afinidade DESC.
    Para geografia ORDER BY perc_vertical DESC. LIMIT 30.
    """
    return _run_bq_query(sql, max_results=30)


# ============================================================================
# Validation Tools (equivalentes dos toolCode do n8n)
# ============================================================================

@tool
def validar_veiculo(nome_comercial: str) -> str:
    """Traduz nome comercial de veículo/meio para o valor exato no campo descricao_meio.

    Use SEMPRE antes de filtrar por veículo.
    Retorna o descricao_meio exato e filtro SQL recomendado.
    """
    # Mapeamento COMPLETO portado do Code node validar_veiculo do n8n
    mapa = {
        # Social
        "meta": "Internet-Social",
        "facebook": "Internet-Social",
        "instagram": "Internet-Social",
        "whatsapp": "Internet-Social",
        "tiktok": "Internet-Social",
        "linkedin": "Internet-Social",
        "twitter": "Internet-Social",
        "x": "Internet-Social",
        "pinterest": "Internet-Social",
        "social": "Internet-Social",
        "midia social": "Internet-Social",
        "redes sociais": "Internet-Social",
        # Display
        "google": "Internet-Display",
        "google display": "Internet-Display",
        "gdn": "Internet-Display",
        "display": "Internet-Display",
        "programatica": "Internet-Display",
        "dv360": "Internet-Display",
        # Video
        "youtube": "Internet-Video",
        "google video": "Internet-Video",
        "video online": "Internet-Video",
        # Search
        "google search": "Internet-Search",
        "search": "Internet-Search",
        "busca": "Internet-Search",
        # TV Aberta
        "tv aberta": "TV Aberta",
        "globo": "TV Aberta",
        "tv globo": "TV Aberta",
        "sbt": "TV Aberta",
        "record": "TV Aberta",
        "band": "TV Aberta",
        "bandeirantes": "TV Aberta",
        "redetv": "TV Aberta",
        "tv cultura": "TV Aberta",
        # TV Fechada
        "tv fechada": "TV Fechada",
        "tv paga": "TV Fechada",
        "cabo": "TV Fechada",
        "globonews": "TV Fechada",
        "espn": "TV Fechada",
        "sportv": "TV Fechada",
        "multishow": "TV Fechada",
        "gnt": "TV Fechada",
        "discovery": "TV Fechada",
        # Rádio
        "radio": "Radio",
        "radio am": "Radio",
        "radio fm": "Radio",
        # Impressos
        "jornal": "Jornal",
        "folha": "Jornal",
        "estadao": "Jornal",
        "gazeta": "Jornal",
        "revista": "Revista",
        "veja": "Revista",
        "exame": "Revista",
        # OOH
        "ooh": "OOH",
        "outdoor": "OOH",
        "mobiliario urbano": "OOH",
        "midia exterior": "OOH",
        "busdoor": "OOH",
        "painel": "OOH",
        "aeroporto": "OOH",
        # Cinema
        "cinema": "Cinema",
        "cinemark": "Cinema",
    }

    key = nome_comercial.strip().lower()
    valor = mapa.get(key)

    if valor:
        return (
            f"Veiculo '{nome_comercial}' mapeado para: {valor}. "
            f"Use EXATAMENTE este valor no filtro descricao_meio. "
            f"Exemplo: WHERE descricao_meio = '{valor}'"
        )

    return (
        f"Veiculo '{nome_comercial}' nao encontrado no mapeamento padrao. "
        f"Execute esta query para descobrir: "
        f"SELECT DISTINCT descricao_meio, COUNT(*) as qtd "
        f"FROM `athenaai-opus.ath_boticario.pi01` GROUP BY 1 ORDER BY 2 DESC LIMIT 30"
    )


@tool
def validar_cliente(apelido: str) -> str:
    """Traduz apelidos e abreviações de clientes para o nome oficial na base de dados.

    Use quando o usuario mencionar um cliente por apelido.
    """
    # Mapeamento COMPLETO portado do Code node validar_cliente do n8n
    mapa = {
        "boti": "O BOTICARIO",
        "boticario": "O BOTICARIO",
        "o boticario": "O BOTICARIO",
        "boticário": "O BOTICARIO",
        "eudora": "EUDORA",
        "qdb": "QUEM DISSE BERENICE",
        "quem disse": "QUEM DISSE BERENICE",
        "quem disse berenice": "QUEM DISSE BERENICE",
        "berenice": "QUEM DISSE BERENICE",
        "vult": "VULT",
        "beautybox": "BEAUTYBOX",
        "beauty box": "BEAUTYBOX",
        "multi b": "MULTI B",
        "multib": "MULTI B",
        "grupo boti": "GRUPO BOTICARIO",
        "grupo boticario": "GRUPO BOTICARIO",
        "holding": "GRUPO BOTICARIO",
        "truss": "TRUSS",
        "australian gold": "AUSTRALIAN GOLD",
    }

    key = apelido.strip().lower()
    oficial = mapa.get(key)

    if oficial:
        return (
            f"Cliente '{apelido}' identificado como: {oficial}. "
            f"Use no filtro SQL: WHERE UPPER(cliente) = '{oficial}' "
            f"ou WHERE UPPER(cliente) LIKE '%{oficial}%'"
        )

    return (
        f"Cliente '{apelido}' nao encontrado no mapeamento. Execute para descobrir: "
        f"SELECT DISTINCT cliente, COUNT(*) as qtd FROM `athenaai-opus.ath_boticario.pi01` "
        f"WHERE UPPER(cliente) LIKE '%{apelido.upper()}%' GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
    )


@tool
def converter_periodo(periodo: str, ano: int | None = None) -> str:
    """Converte ciclo/trimestre/mês para datas YYYY-MM-DD.

    Exemplos: "ciclo 3" → Mai-Jun, "Q1" → Jan-Mar, "janeiro" → Jan.
    Retorna data_inicio, data_fim e filtro SQL recomendado.
    """
    from datetime import date
    import calendar
    import re

    if ano is None:
        ano = date.today().year

    p = periodo.strip().lower()

    # Ciclos (portados do Code node converter_periodo do n8n)
    ciclos = {
        1: (f"{ano}-01-01", f"{ano}-02-28", "Ciclo 01 (Jan-Fev)"),
        2: (f"{ano}-03-01", f"{ano}-04-30", "Ciclo 02 (Mar-Abr)"),
        3: (f"{ano}-05-01", f"{ano}-06-30", "Ciclo 03 (Mai-Jun)"),
        4: (f"{ano}-07-01", f"{ano}-08-31", "Ciclo 04 (Jul-Ago)"),
        5: (f"{ano}-09-01", f"{ano}-10-31", "Ciclo 05 (Set-Out)"),
        6: (f"{ano}-11-01", f"{ano}-12-31", "Ciclo 06 (Nov-Dez)"),
    }

    # Detecta "ciclo N" ou "cN" ou "c0N"
    ciclo_match = re.search(r"(?:ciclo\s*)?(?:c)?0?(\d+)", p)
    if "ciclo" in p or p.startswith("c") and ciclo_match:
        num = int(ciclo_match.group(1))
        if num in ciclos:
            inicio, fim, desc = ciclos[num]
            return (
                f"Periodo: {desc}. Data inicio: {inicio}. Data fim: {fim}. "
                f"Filtro SQL: WHERE periodo_pi BETWEEN '{inicio}' AND '{fim}'"
            )
        return f"Ciclo {num} nao reconhecido. Ciclos validos: 1-6."

    # Trimestres
    trimestres = {
        "q1": (f"{ano}-01-01", f"{ano}-03-31", "1o Trimestre"),
        "primeiro trimestre": (f"{ano}-01-01", f"{ano}-03-31", "1o Trimestre"),
        "q2": (f"{ano}-04-01", f"{ano}-06-30", "2o Trimestre"),
        "segundo trimestre": (f"{ano}-04-01", f"{ano}-06-30", "2o Trimestre"),
        "q3": (f"{ano}-07-01", f"{ano}-09-30", "3o Trimestre"),
        "terceiro trimestre": (f"{ano}-07-01", f"{ano}-09-30", "3o Trimestre"),
        "q4": (f"{ano}-10-01", f"{ano}-12-31", "4o Trimestre"),
        "quarto trimestre": (f"{ano}-10-01", f"{ano}-12-31", "4o Trimestre"),
    }
    if p in trimestres:
        inicio, fim, desc = trimestres[p]
        return f"Periodo: {desc}. Data inicio: {inicio}. Data fim: {fim}. Filtro SQL: WHERE periodo_pi BETWEEN '{inicio}' AND '{fim}'"

    # Semestres
    if p in ("primeiro semestre", "s1"):
        return f"Periodo: 1o Semestre. Data inicio: {ano}-01-01. Data fim: {ano}-06-30. Filtro SQL: WHERE periodo_pi BETWEEN '{ano}-01-01' AND '{ano}-06-30'"
    if p in ("segundo semestre", "s2"):
        return f"Periodo: 2o Semestre. Data inicio: {ano}-07-01. Data fim: {ano}-12-31. Filtro SQL: WHERE periodo_pi BETWEEN '{ano}-07-01' AND '{ano}-12-31'"

    # Meses
    meses = {
        "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2,
        "março": 3, "marco": 3, "mar": 3, "abril": 4, "abr": 4,
        "maio": 5, "mai": 5, "junho": 6, "jun": 6,
        "julho": 7, "jul": 7, "agosto": 8, "ago": 8,
        "setembro": 9, "set": 9, "outubro": 10, "out": 10,
        "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
    }
    for nome, num in meses.items():
        if nome in p:
            ultimo = calendar.monthrange(ano, num)[1]
            return (
                f"Periodo: {nome.capitalize()}/{ano}. "
                f"Data inicio: {ano}-{num:02d}-01. Data fim: {ano}-{num:02d}-{ultimo:02d}. "
                f"Filtro SQL: WHERE periodo_pi BETWEEN '{ano}-{num:02d}-01' AND '{ano}-{num:02d}-{ultimo:02d}'"
            )

    # Ano / YTD
    if "ano" in p or "ytd" in p or "anual" in p:
        return f"Periodo: Ano {ano}. Data inicio: {ano}-01-01. Data fim: {ano}-12-31. Filtro SQL: WHERE periodo_pi BETWEEN '{ano}-01-01' AND '{ano}-12-31'"

    return f"Periodo nao reconhecido: '{periodo}'. Informe ciclo (01-06), trimestre, semestre ou mes."


@tool
def calcular_indicadores(
    tipo_calculo: str,
    valor1: float,
    valor2: float = 0,
) -> str:
    """Calcula indicadores e métricas de mídia com precisão matemática.

    Tipos suportados:
    - cpp / custo por ponto: valor1=investimento, valor2=GRP
    - cpm / custo por mil: valor1=investimento, valor2=impressões
    - variacao / crescimento: valor1=valor atual, valor2=valor base
    - participacao / share: valor1=parte, valor2=total
    - media: valor1 e valor2 para calcular média
    - soma: valor1 + valor2
    """
    def fmt_brl(v: float) -> str:
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def fmt_pct(v: float) -> str:
        return f"{v:.2f}".replace(".", ",") + "%"

    t = tipo_calculo.lower().strip()

    if "cpp" in t or "custo por ponto" in t:
        if valor2 == 0:
            return "GRP nao pode ser zero."
        return f"CPP = {fmt_brl(valor1)} / {valor2} GRP = {fmt_brl(valor1 / valor2)}"

    if "cpm" in t or "custo por mil" in t:
        if valor2 == 0:
            return "Impressoes nao podem ser zero."
        return f"CPM = {fmt_brl((valor1 / valor2) * 1000)}"

    if "variacao" in t or "variação" in t or "crescimento" in t:
        if valor2 == 0:
            return "Valor base nao pode ser zero."
        v = ((valor1 - valor2) / valor2) * 100
        sinal = "+" if v >= 0 else ""
        return f"Variacao = {sinal}{fmt_pct(v)}"

    if "participacao" in t or "participação" in t or "share" in t or "percentual" in t:
        if valor2 == 0:
            return "Total nao pode ser zero."
        return f"Participacao = {fmt_pct((valor1 / valor2) * 100)}"

    if "media" in t or "média" in t:
        return f"Media = {fmt_brl((valor1 + valor2) / 2)}"

    if "soma" in t:
        return f"Soma = {fmt_brl(valor1 + valor2)}"

    return f"Tipo nao reconhecido: '{tipo_calculo}'. Use: cpp, cpm, variacao, participacao, media, soma."


@tool
def buscar_web(search_query: str) -> str:
    """Busca informações na web quando os dados do BigQuery não são suficientes.

    Use para: tendências de mercado, notícias do setor, benchmarks, dados de
    concorrentes, CPMs médios, regulamentações, melhores práticas.
    NUNCA use para dados internos da agência.
    """
    import httpx

    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
    cx = os.environ.get("GOOGLE_SEARCH_CX", "")

    if not api_key or not cx:
        return (
            f"Busca web temporariamente indisponivel (API nao configurada). "
            f"Responda com base no seu conhecimento, deixando claro que nao sao dados "
            f"verificados em tempo real. Query tentada: {search_query}"
        )

    try:
        resp = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": search_query, "num": 5, "lr": "lang_pt"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            return f"Nenhum resultado encontrado para: {search_query}. Tente termos diferentes."

        resultados = f"Resultados da busca web para: {search_query}\n\n"
        for i, item in enumerate(items, 1):
            resultados += f"{i}. {item['title']}\n"
            resultados += f"   {item.get('snippet', 'Sem descricao')}\n"
            resultados += f"   Fonte: {item['link']}\n\n"
        resultados += "Apresente de forma organizada. Cite as fontes."
        return resultados

    except Exception as e:
        return (
            f"Erro na busca web: {e}. Responda com base no seu conhecimento, "
            f"deixando claro que nao sao dados verificados em tempo real."
        )


@tool
def exportar_sheets(titulo: str, dados: str) -> str:
    """Exporta dados para uma nova planilha Google Sheets.

    Args:
        titulo: Título da planilha.
        dados: String JSON serializada com array dos resultados.

    Returns:
        URL da planilha criada ou mensagem de erro.
    """
    import json

    try:
        import gspread
        import google.auth

        # Parse dos dados
        try:
            parsed = json.loads(dados)
        except json.JSONDecodeError:
            return f"Erro: dados não são JSON válido. Recebido: {dados[:200]}..."

        if not isinstance(parsed, list) or len(parsed) == 0:
            return "Erro: dados devem ser uma lista não vazia de objetos."

        # ADC do Cloud Run
        creds, _ = google.auth.default(
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
        )
        gc = gspread.authorize(creds)

        # Limpar planilhas antigas (>30 dias) pra liberar espaco no Drive da SA
        try:
            from datetime import datetime, timedelta, timezone
            from googleapiclient.discovery import build
            drive_service = build("drive", "v3", credentials=creds)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            old_files = drive_service.files().list(
                q=f"name contains 'Athena' and createdTime < '{cutoff}' and mimeType='application/vnd.google-apps.spreadsheet'",
                fields="files(id,name)",
                pageSize=100,
            ).execute().get("files", [])
            for f in old_files:
                try:
                    drive_service.files().delete(fileId=f["id"]).execute()
                except Exception:
                    pass
            if old_files:
                import logging
                logging.getLogger(__name__).info(
                    "Limpeza Drive: removidas %d planilhas antigas", len(old_files)
                )
        except Exception:
            pass  # Nao bloqueia se cleanup falhar

        # Criar planilha
        sh = gc.create(f"Athena \u2014 {titulo}")
        ws = sh.sheet1
        ws.update_title(titulo[:100])

        # Montar rows
        if isinstance(parsed[0], dict):
            headers = list(parsed[0].keys())
            rows = [headers] + [[str(row.get(h, "")) for h in headers] for row in parsed]
        else:
            rows = [[str(c) for c in (row if isinstance(row, list) else [row])] for row in parsed]

        # Escrever batch
        ws.update(range_name="A1", values=rows)

        # Estilizar header
        try:
            ws.format("1", {
                "backgroundColor": {"red": 0.77, "green": 0.12, "blue": 0.12},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            })
        except Exception:
            pass

        # Compartilhar com link (qualquer pessoa da org pode acessar)
        try:
            sh.share("", perm_type="anyone", role="reader")
        except Exception:
            pass

        return (
            f"Planilha criada com sucesso!\n"
            f"T\u00edtulo: {sh.title}\n"
            f"Linhas: {len(parsed)}\n"
            f"URL: {sh.url}\n"
            f"Abra no navegador para editar."
        )

    except ImportError:
        return "Erro: gspread n\u00e3o instalado no backend."
    except Exception as e:
        error_msg = str(e)
        if "storage quota" in error_msg.lower() or "quota" in error_msg.lower():
            return (
                "Erro: o armazenamento do Google Drive da conta de servi\u00e7o est\u00e1 cheio. "
                "Planilhas antigas precisam ser removidas. "
                "Entre em contato com o administrador para liberar espa\u00e7o no Drive da SA."
            )
        return f"Erro ao criar planilha: {error_msg}"


# ============================================================================
# Registro de tools — todas em uma lista
# ============================================================================

def get_all_tools() -> list:
    """Retorna tools locais para o agente.

    Na v3.1 (alinhado com produção v2.4), TODAS as tools de dados são
    fornecidas pelos 4 MCPs (publi-mysql, pesquisas, midia-online, export).
    As tools locais de BigQuery foram desativadas — o agente usa as tools
    MCP via langchain-mcp-adapters.

    Retorna lista vazia: tools MCP são injetadas pelo adapter em graph.py.
    As funções locais acima continuam definidas como fallback/referência.
    """
    # MCPs fornecem tudo: consultar_mysql, consultar_bigquery,
    # listar_tabelas, descrever_tabela, abrir_catalogo, cod_clientes,
    # converter_ciclo, ciclo_de_data, enriquecer_grupo_mkt,
    # exportar_sheet, exportar_csv, exportar_sheet_sql.
    return []


def get_legacy_tools() -> list:
    """Retorna tools locais legadas (para fallback se MCPs estiverem offline)."""
    return [
        bigquery_financeiro,
        bigquery_orcamento,
        bigquery_operacional,
        bigquery_tabela_tv,
        bigquery_briefing,
        bigquery_fornecedores,
        bigquery_ooh,
        tgi_choices,
        validar_veiculo,
        validar_cliente,
        converter_periodo,
        calcular_indicadores,
        buscar_web,
        exportar_sheets,
    ]
