"""
BigQueryService — CRUD de persistência para a Athena.

Substitui os 18 nodes BigQuery CRUD do n8n:
  - BQ Listar/Criar Conversas, BQ Atualizar Titulo
  - BQ Carregar Historico, BQ Inserir Mensagem, BQ Atualizar Contagem
  - BQ Inserir Feedback
  - BQ Salvar Resumo, BQ Marcar Compactadas, BQ Contar Ativas
  - Registrador de Requisicao
  - BQ Audit Query (overview, recent_activity, feedback_summary, top_users)
  - BQ Load Week Feedback, BQ Save Learnings

Tabelas BigQuery (dataset: pj_boti):
  - athena_conversations: conversas do usuário
  - athena_messages: mensagens individuais
  - athena_feedback: thumbs up/down
  - athena_learnings: padrões aprendidos do feedback
  - athena_logs: log de cada request/response
"""

from __future__ import annotations

import logging
import time
import random
from datetime import datetime, timezone

from google.cloud import bigquery

from app.config import settings

logger = logging.getLogger(__name__)


class BigQueryService:
    """Serviço de persistência BigQuery — CRUD para todas as tabelas da Athena."""

    def __init__(self) -> None:
        self._client: bigquery.Client | None = None

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            # Persistência usa projeto diferente (sheetsintegration-451500)
            self._client = bigquery.Client(project=settings.bq.project_persistence)
        return self._client

    def _table(self, name: str) -> str:
        """Retorna o fully-qualified table ID para persistência."""
        return f"`{settings.bq.project_persistence}.{settings.bq.dataset_persistence}.{name}`"

    @staticmethod
    def _gen_id(prefix: str) -> str:
        """Gera ID no formato do n8n: prefix + timestamp + random.

        Ex: msg_1721523600123_a3f, sum_1721523600456_b7c, fb_1721523600789_d1e
        """
        ts = int(time.time() * 1000)
        rnd = f"{random.randint(0, 4095):03x}"
        return f"{prefix}{ts}_{rnd}"

    def _query(self, sql: str, params: list[bigquery.QueryParameter] | None = None) -> list[dict]:
        """Executa query e retorna lista de dicts."""
        job_config = bigquery.QueryJobConfig()
        if params:
            job_config.query_parameters = params
        try:
            result = self.client.query(sql, job_config=job_config, timeout=30)
            return [dict(row) for row in result.result(timeout=30)]
        except Exception as e:
            logger.error("BigQueryService query error: %s | sql: %s", e, sql[:200])
            raise

    def _execute(self, sql: str, params: list[bigquery.QueryParameter] | None = None) -> None:
        """Executa DML (INSERT/UPDATE) sem retorno."""
        job_config = bigquery.QueryJobConfig()
        if params:
            job_config.query_parameters = params
        try:
            job = self.client.query(sql, job_config=job_config, timeout=30)
            job.result(timeout=30)
        except Exception as e:
            logger.error("BigQueryService execute error: %s | sql: %s", e, sql[:200])
            raise

    # =========================================================================
    # Conversas
    # =========================================================================

    def list_conversations(self, user_id: str) -> list[dict]:
        """Lista conversas de um usuário.

        Equivale ao node: BQ Listar Conversas
        """
        sql = f"""
            SELECT conversation_id, user_id, title, status, message_count,
                   created_at, updated_at
            FROM {self._table(settings.persistence.table_conversations)}
            WHERE user_id = @user_id AND status = 'active'
            ORDER BY updated_at DESC
            LIMIT 50
        """
        params = [bigquery.ScalarQueryParameter("user_id", "STRING", user_id)]
        return self._query(sql, params)

    def create_conversation(
        self, user_id: str, conversation_id: str, title: str = "Nova conversa",
    ) -> dict:
        """Cria uma nova conversa.

        Equivale ao node: BQ Criar Conversa
        NOTA: conversation_id vem do FRONTEND (gerado pelo App.js),
        não do backend. O n8n recebe o ID pelo request body.
        """
        now = datetime.now(timezone.utc).isoformat()
        sql = f"""
            INSERT INTO {self._table(settings.persistence.table_conversations)}
            (conversation_id, user_id, title, status, message_count, created_at, updated_at)
            VALUES (@conv_id, @user_id, @title, 'active', 0, @now, @now)
        """
        params = [
            bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id),
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("title", "STRING", title),
            bigquery.ScalarQueryParameter("now", "STRING", now),
        ]
        self._execute(sql, params)
        return {"conversation_id": conversation_id, "title": title, "created_at": now}

    def update_title(self, conversation_id: str, title: str) -> bool:
        """Atualiza título de uma conversa.

        Equivale ao node: BQ Atualizar Titulo
        """
        now = datetime.now(timezone.utc).isoformat()
        sql = f"""
            UPDATE {self._table(settings.persistence.table_conversations)}
            SET title = @title, updated_at = @now
            WHERE conversation_id = @conv_id
        """
        params = [
            bigquery.ScalarQueryParameter("title", "STRING", title),
            bigquery.ScalarQueryParameter("now", "STRING", now),
            bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id),
        ]
        self._execute(sql, params)
        return True

    # =========================================================================
    # Mensagens / Histórico
    # =========================================================================

    def load_history(self, conversation_id: str, limit: int = 200) -> list[dict]:
        """Carrega histórico de mensagens de uma conversa.

        Equivale ao node: BQ Carregar Historico
        """
        sql = f"""
            SELECT message_id, conversation_id, user_id, role, content,
                   timestamp, is_compacted
            FROM {self._table(settings.persistence.table_messages)}
            WHERE conversation_id = @conv_id AND is_compacted = FALSE
            ORDER BY timestamp ASC
            LIMIT @limit
        """
        params = [
            bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
        return self._query(sql, params)

    def save_message(self, conversation_id: str, user_id: str, role: str, content: str) -> dict:
        """Salva uma mensagem e atualiza contagem na conversa.

        Equivale aos nodes: BQ Inserir Mensagem + BQ Atualizar Contagem
        NOTA: messageId usa formato n8n: msg_ + timestamp + random (não UUID).
        """
        message_id = self._gen_id("msg_")
        now = datetime.now(timezone.utc).isoformat()

        # INSERT mensagem
        sql_insert = f"""
            INSERT INTO {self._table(settings.persistence.table_messages)}
            (message_id, conversation_id, user_id, role, content, timestamp, is_compacted)
            VALUES (@msg_id, @conv_id, @user_id, @role, @content, @now, FALSE)
        """
        params_insert = [
            bigquery.ScalarQueryParameter("msg_id", "STRING", message_id),
            bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id),
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("role", "STRING", role),
            bigquery.ScalarQueryParameter("content", "STRING", content),
            bigquery.ScalarQueryParameter("now", "STRING", now),
        ]
        self._execute(sql_insert, params_insert)

        # UPDATE contagem na conversa
        sql_update = f"""
            UPDATE {self._table(settings.persistence.table_conversations)}
            SET message_count = message_count + 1, updated_at = @now
            WHERE conversation_id = @conv_id
        """
        params_update = [
            bigquery.ScalarQueryParameter("now", "STRING", now),
            bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id),
        ]
        self._execute(sql_update, params_update)

        return {"message_id": message_id, "timestamp": now}

    # =========================================================================
    # Feedback
    # =========================================================================

    def save_feedback(
        self,
        user_id: str,
        message_id: str,
        rating: str,
        conversation_id: str = "",
        user_query: str | None = None,
        assistant_response: str | None = None,
        comment: str | None = None,
    ) -> bool:
        """Salva feedback positivo/negativo.

        Equivale ao node: BQ Inserir Feedback
        NOTA: inclui conversation_id (o n8n salva esse campo no INSERT).
        """
        feedback_id = self._gen_id("fb_")
        now = datetime.now(timezone.utc).isoformat()
        sql = f"""
            INSERT INTO {self._table(settings.persistence.table_feedback)}
            (feedback_id, user_id, message_id, conversation_id, rating, user_query,
             assistant_response, comment, timestamp)
            VALUES (@fb_id, @user_id, @msg_id, @conv_id, @rating, @user_query,
                    @assistant_response, @comment, @now)
        """
        params = [
            bigquery.ScalarQueryParameter("fb_id", "STRING", feedback_id),
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("msg_id", "STRING", message_id),
            bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id),
            bigquery.ScalarQueryParameter("rating", "STRING", rating),
            bigquery.ScalarQueryParameter("user_query", "STRING", user_query or ""),
            bigquery.ScalarQueryParameter("assistant_response", "STRING", assistant_response or ""),
            bigquery.ScalarQueryParameter("comment", "STRING", comment or ""),
            bigquery.ScalarQueryParameter("now", "STRING", now),
        ]
        self._execute(sql, params)
        return True

    # =========================================================================
    # Logs
    # =========================================================================

    def insert_log(
        self,
        session_id: str,
        user_input: str,
        agent_response: str,
        latency_ms: int,
    ) -> None:
        """Registra log de request/response.

        Equivale ao node: Registrador de Requisicao
        """
        sql = f"""
            INSERT INTO {self._table(settings.persistence.table_logs)}
            (timestamp, session_id, user_input, agent_response, latency_ms)
            VALUES (CURRENT_TIMESTAMP(), @session_id, @user_input, @agent_response, @latency_ms)
        """
        params = [
            bigquery.ScalarQueryParameter("session_id", "STRING", session_id),
            bigquery.ScalarQueryParameter("user_input", "STRING", user_input[:2000]),
            bigquery.ScalarQueryParameter("agent_response", "STRING", agent_response[:2000]),
            bigquery.ScalarQueryParameter("latency_ms", "INT64", latency_ms),
        ]
        try:
            self._execute(sql, params)
        except Exception as e:
            # Log falha NÃO deve quebrar a resposta do usuário
            logger.warning("Falha ao registrar log: %s", e)

    # =========================================================================
    # Compactação
    # =========================================================================

    def count_active_messages(self, conversation_id: str) -> int:
        """Conta mensagens ativas (não compactadas) de uma conversa.

        Equivale ao node: BQ Contar Ativas
        NOTA: filtra só role IN ('user','assistant') — ignora system_summary.
        """
        sql = f"""
            SELECT COUNT(*) as total
            FROM {self._table(settings.persistence.table_messages)}
            WHERE conversation_id = @conv_id
              AND is_compacted = FALSE
              AND role IN ('user', 'assistant')
        """
        params = [bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id)]
        rows = self._query(sql, params)
        return rows[0]["total"] if rows else 0

    def load_old_messages(self, conversation_id: str, keep_recent: int = 10) -> list[dict]:
        """Carrega mensagens antigas para compactação.

        Equivale ao node: BQ Carregar Antigas
        NOTA: filtra só role IN ('user','assistant') — ignora system_summary.
        """
        sql = f"""
            SELECT message_id, role, content, timestamp
            FROM {self._table(settings.persistence.table_messages)}
            WHERE conversation_id = @conv_id
              AND is_compacted = FALSE
              AND role IN ('user', 'assistant')
            ORDER BY timestamp ASC
        """
        params = [bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id)]
        all_msgs = self._query(sql, params)
        if len(all_msgs) <= keep_recent:
            return []
        return all_msgs[:-keep_recent]

    def save_summary(self, conversation_id: str, user_id: str, summary: str) -> str:
        """Salva resumo da compactação como system_summary.

        Equivale ao node: BQ Salvar Resumo
        Usa summaryId no formato sum_ + timestamp + random.
        """
        summary_id = self._gen_id("sum_")
        now = datetime.now(timezone.utc).isoformat()
        sql = f"""
            INSERT INTO {self._table(settings.persistence.table_messages)}
            (message_id, conversation_id, user_id, role, content, timestamp, is_compacted)
            VALUES (@msg_id, @conv_id, @user_id, 'system_summary', @content, @now, FALSE)
        """
        params = [
            bigquery.ScalarQueryParameter("msg_id", "STRING", summary_id),
            bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id),
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("content", "STRING", summary[:3000]),
            bigquery.ScalarQueryParameter("now", "STRING", now),
        ]
        self._execute(sql, params)
        return summary_id

    def mark_compacted(self, message_ids: list[str]) -> None:
        """Marca mensagens como compactadas.

        Equivale ao node: BQ Marcar Compactadas
        """
        if not message_ids:
            return
        sql = f"""
            UPDATE {self._table(settings.persistence.table_messages)}
            SET is_compacted = TRUE
            WHERE message_id IN UNNEST(@ids)
        """
        params = [bigquery.ArrayQueryParameter("ids", "STRING", message_ids)]
        self._execute(sql, params)

    # =========================================================================
    # Auditoria
    # =========================================================================

    def audit_kpis(self) -> dict:
        """KPIs gerais — totalMessages, activeConversations, uniqueUsers, positiveCount, negativeCount.

        Equivale ao Roteador de Auditoria query='kpis'.
        """
        t_msg = self._table(settings.persistence.table_messages)
        t_conv = self._table(settings.persistence.table_conversations)
        t_fb = self._table(settings.persistence.table_feedback)
        sql = f"""
            SELECT
                (SELECT COUNT(*) FROM {t_msg} WHERE role IN ('user','assistant')) AS total_messages,
                (SELECT COUNT(*) FROM {t_conv} WHERE status = 'active') AS active_conversations,
                (SELECT COUNT(DISTINCT user_id) FROM {t_msg} WHERE role = 'user') AS unique_users,
                (SELECT COUNT(*) FROM {t_fb} WHERE rating = 'positive') AS positive_count,
                (SELECT COUNT(*) FROM {t_fb} WHERE rating = 'negative') AS negative_count
        """
        rows = self._query(sql)
        return rows[0] if rows else {}

    def audit_recent_activity(self) -> list[dict]:
        """Últimas 24h de atividade — mensagens do user.

        Equivale ao Roteador de Auditoria query='recent_activity'.
        """
        sql = f"""
            SELECT user_id, content, timestamp
            FROM {self._table(settings.persistence.table_messages)}
            WHERE role = 'user'
              AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
            ORDER BY timestamp DESC
            LIMIT 50
        """
        return self._query(sql)

    def audit_recent_feedback(self) -> list[dict]:
        """Últimos 10 feedbacks.

        Equivale ao Roteador de Auditoria query='recent_feedback'.
        """
        sql = f"""
            SELECT feedback_id, user_id, rating, user_query, comment, timestamp
            FROM {self._table(settings.persistence.table_feedback)}
            ORDER BY timestamp DESC
            LIMIT 10
        """
        return self._query(sql)

    def audit_top_users(self) -> list[dict]:
        """Top 10 users por msgs, com contagem de feedback positivo/negativo.

        Equivale ao Roteador de Auditoria query='top_users'.
        """
        t_msg = self._table(settings.persistence.table_messages)
        t_fb = self._table(settings.persistence.table_feedback)
        sql = f"""
            SELECT
                m.user_id,
                COUNT(*) AS message_count,
                COALESCE(f.positive_count, 0) AS positive_count,
                COALESCE(f.negative_count, 0) AS negative_count
            FROM {t_msg} m
            LEFT JOIN (
                SELECT user_id,
                       COUNTIF(rating = 'positive') AS positive_count,
                       COUNTIF(rating = 'negative') AS negative_count
                FROM {t_fb}
                GROUP BY user_id
            ) f ON m.user_id = f.user_id
            WHERE m.role = 'user'
            GROUP BY m.user_id, f.positive_count, f.negative_count
            ORDER BY message_count DESC
            LIMIT 10
        """
        return self._query(sql)

    def audit_all_conversations(
        self, date_from: str | None = None, date_to: str | None = None,
    ) -> list[dict]:
        """Todas as conversas, com filtro de data opcional.

        Equivale ao Roteador de Auditoria query='all_conversations'.
        """
        sql = f"""
            SELECT conversation_id, user_id, title, status, message_count,
                   created_at, updated_at
            FROM {self._table(settings.persistence.table_conversations)}
            WHERE 1=1
        """
        params = []
        if date_from:
            sql += " AND created_at >= @date_from"
            params.append(bigquery.ScalarQueryParameter("date_from", "STRING", date_from))
        if date_to:
            sql += " AND created_at <= @date_to"
            params.append(bigquery.ScalarQueryParameter("date_to", "STRING", date_to))
        sql += " ORDER BY updated_at DESC LIMIT 100"
        return self._query(sql, params or None)

    def audit_conversation_messages(self, conversation_id: str) -> list[dict]:
        """Todas as mensagens de uma conversa específica.

        Equivale ao Roteador de Auditoria query='conversation_messages'.
        """
        sql = f"""
            SELECT message_id, role, content, timestamp, is_compacted
            FROM {self._table(settings.persistence.table_messages)}
            WHERE conversation_id = @conv_id
            ORDER BY timestamp ASC
            LIMIT 500
        """
        params = [bigquery.ScalarQueryParameter("conv_id", "STRING", conversation_id)]
        return self._query(sql, params)


    # =========================================================================
    # Learnings (Feedback Analysis)
    # =========================================================================

    def load_week_feedback(self, days: int = 7) -> list[dict]:
        """Carrega feedback para análise."""
        sql = f"""
            SELECT feedback_id, user_id, rating, user_query,
                   assistant_response, comment, timestamp
            FROM {self._table(settings.persistence.table_feedback)}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
            ORDER BY timestamp DESC
            LIMIT 200
        """
        params = [bigquery.ScalarQueryParameter("days", "INT64", days)]
        return self._query(sql, params)

    def save_learnings(self, learnings: list[dict]) -> None:
        """Salva aprendizados da análise de feedback.

        Schema real da tabela athena_learnings:
        learning_id, category, pattern, recommendation, examples, priority(INT),
        status(STR='active'), created_at
        """
        now = datetime.now(timezone.utc).isoformat()
        for learning in learnings:
            sql = f"""
                INSERT INTO {self._table(settings.persistence.table_learnings)}
                (learning_id, category, pattern, recommendation, examples,
                 priority, status, created_at)
                VALUES (@lid, @category, @pattern, @recommendation, @examples,
                        @priority, @status, @now)
            """
            params = [
                bigquery.ScalarQueryParameter("lid", "STRING", self._gen_id("lrn_")),
                bigquery.ScalarQueryParameter("category", "STRING", learning.get("category", "general")),
                bigquery.ScalarQueryParameter("pattern", "STRING", learning.get("pattern", "")),
                bigquery.ScalarQueryParameter("recommendation", "STRING", learning.get("recommendation", "")),
                bigquery.ScalarQueryParameter("examples", "STRING", learning.get("examples", "")),
                bigquery.ScalarQueryParameter("priority", "INT64", int(learning.get("priority", 5))),
                bigquery.ScalarQueryParameter("status", "STRING", learning.get("status", "active")),
                bigquery.ScalarQueryParameter("now", "STRING", now),
            ]
            self._execute(sql, params)

    def load_learnings(self) -> list[dict]:
        """Carrega aprendizados recentes."""
        sql = f"""
            SELECT category, pattern, recommendation, examples, priority, status, created_at
            FROM {self._table(settings.persistence.table_learnings)}
            WHERE status = 'active'
            ORDER BY created_at DESC
            LIMIT 20
        """
        return self._query(sql)

    # =========================================================================
    # Users (RBAC via athena_users)
    # =========================================================================

    def get_user_by_email(self, email: str) -> dict | None:
        """Busca usuário pelo email. Retorna None se não existe."""
        sql = f"""
            SELECT google_sub, email, nome, avatar_url, departamento, role,
                   created_at, last_login
            FROM {self._table('athena_users')}
            WHERE email = @email
            LIMIT 1
        """
        params = [bigquery.ScalarQueryParameter("email", "STRING", email.lower())]
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def is_admin(self, email: str) -> bool:
        """Verifica se o usuário tem role 'admin' na athena_users.

        Fallback: se o usuário não existe na tabela, retorna False.
        """
        user = self.get_user_by_email(email)
        if user is None:
            return False
        return user.get("role") == "admin"

    def upsert_user(
        self,
        google_sub: str,
        email: str,
        nome: str | None = None,
        avatar_url: str | None = None,
    ) -> dict:
        """Cria ou atualiza usuário no login.

        Se o email já existe, atualiza google_sub, nome, avatar e last_login.
        Se não existe, insere com role='user' (default da tabela).
        """
        email_lower = email.lower()
        existing = self.get_user_by_email(email_lower)

        if existing:
            # UPDATE — atualiza dados do Google e last_login
            sql = f"""
                UPDATE {self._table('athena_users')}
                SET google_sub = @sub, nome = @nome, avatar_url = @avatar,
                    last_login = CURRENT_TIMESTAMP()
                WHERE email = @email
            """
            params = [
                bigquery.ScalarQueryParameter("sub", "STRING", google_sub),
                bigquery.ScalarQueryParameter("nome", "STRING", nome or existing.get("nome", "")),
                bigquery.ScalarQueryParameter("avatar", "STRING", avatar_url or existing.get("avatar_url", "")),
                bigquery.ScalarQueryParameter("email", "STRING", email_lower),
            ]
            self._execute(sql, params)
            return {"action": "updated", "email": email_lower, "role": existing.get("role", "user")}
        else:
            # INSERT — novo usuário com role default 'user'
            sql = f"""
                INSERT INTO {self._table('athena_users')}
                (google_sub, email, nome, avatar_url, role, last_login)
                VALUES (@sub, @email, @nome, @avatar, 'user', CURRENT_TIMESTAMP())
            """
            params = [
                bigquery.ScalarQueryParameter("sub", "STRING", google_sub),
                bigquery.ScalarQueryParameter("email", "STRING", email_lower),
                bigquery.ScalarQueryParameter("nome", "STRING", nome or ""),
                bigquery.ScalarQueryParameter("avatar", "STRING", avatar_url or ""),
            ]
            self._execute(sql, params)
            return {"action": "created", "email": email_lower, "role": "user"}

    def update_user_role(self, email: str, role: str) -> bool:
        """Atualiza o role de um usuário. Só admin pode chamar (validado no endpoint)."""
        if role not in ("admin", "user"):
            raise ValueError(f"Role '{role}' inválido. Use 'admin' ou 'user'.")
        sql = f"""
            UPDATE {self._table('athena_users')}
            SET role = @role
            WHERE email = @email
        """
        params = [
            bigquery.ScalarQueryParameter("role", "STRING", role),
            bigquery.ScalarQueryParameter("email", "STRING", email.lower()),
        ]
        self._execute(sql, params)
        return True

    def list_users(self) -> list[dict]:
        """Lista todos os usuários cadastrados."""
        sql = f"""
            SELECT email, nome, departamento, role, created_at, last_login
            FROM {self._table('athena_users')}
            ORDER BY created_at DESC
            LIMIT 200
        """
        return self._query(sql)


# Singleton
_bq_service: BigQueryService | None = None


def get_bq_service() -> BigQueryService:
    global _bq_service
    if _bq_service is None:
        _bq_service = BigQueryService()
    return _bq_service
