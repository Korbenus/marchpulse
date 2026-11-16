"""Dual-backend persistence layer (SQLite local or PostgreSQL/Neon via psycopg)."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from marchepulse.models import (
    AlertSubscription,
    AlertSubscriptionCreate,
    ApiKeyPublicInfo,
    BuyerEnrichment,
    NormalizedTender,
)

logger = logging.getLogger(__name__)


def _schema_statements(auto_pk: str) -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS tenders (
            idweb TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            buyer_name TEXT NOT NULL,
            departments_json TEXT NOT NULL,
            publication_date TEXT,
            deadline TEXT,
            days_remaining INTEGER,
            is_open INTEGER NOT NULL,
            market_types_json TEXT NOT NULL,
            nature TEXT,
            procedure TEXT,
            descriptor_codes_json TEXT NOT NULL,
            descriptors_json TEXT NOT NULL,
            family TEXT,
            source_url TEXT NOT NULL,
            quality_score INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tenders_pub ON tenders(publication_date DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tenders_buyer ON tenders(buyer_name)",
        """
        CREATE TABLE IF NOT EXISTS buyers_cache (
            query_name TEXT PRIMARY KEY,
            siren TEXT,
            nom_complet TEXT,
            etat_administratif TEXT,
            nature_juridique TEXT,
            activite_principale TEXT,
            categorie_entreprise TEXT,
            tranche_effectif_salarie TEXT,
            commune TEXT,
            code_postal TEXT,
            departement TEXT,
            cached_at TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS api_keys (
            id {auto_pk},
            key_hash TEXT NOT NULL UNIQUE,
            customer_email TEXT NOT NULL,
            plan TEXT NOT NULL,
            provider TEXT NOT NULL,
            external_customer_id TEXT,
            external_subscription_id TEXT,
            monthly_quota INTEGER NOT NULL,
            requests_used INTEGER NOT NULL DEFAULT 0,
            usage_month TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_api_keys_sub ON api_keys(external_subscription_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_keys_email ON api_keys(customer_email)",
        f"""
        CREATE TABLE IF NOT EXISTS alert_subscriptions (
            id {auto_pk},
            owner_key_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            departments_json TEXT NOT NULL,
            market_types_json TEXT NOT NULL,
            only_open INTEGER NOT NULL DEFAULT 1,
            channel TEXT NOT NULL,
            target TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS delivery_logs (
            id {auto_pk},
            subscription_id INTEGER NOT NULL,
            tender_idweb TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            UNIQUE(subscription_id, tender_idweb),
            FOREIGN KEY (subscription_id) REFERENCES alert_subscriptions(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS billing_events (
            event_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            event_type TEXT NOT NULL,
            processed_at TEXT NOT NULL
        )
        """,
    ]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def adapt_sql(sql: str, backend: str) -> str:
    """Convert `?` parameter placeholders to `%s` when targeting PostgreSQL."""
    if backend == "postgres":
        return sql.replace("?", "%s")
    return sql


class _CompatConnection:
    """Thin adapter normalizing `execute` and `executemany` across SQLite and psycopg."""

    def __init__(self, raw_conn: Any, backend: str) -> None:
        self._conn = raw_conn
        self.backend = backend

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        return self._conn.execute(adapt_sql(sql, self.backend), params)

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> None:
        cur = self._conn.cursor()
        try:
            cur.executemany(adapt_sql(sql, self.backend), seq_of_params)
        finally:
            cur.close()


class Database:
    """Thread-safe SQLite or PostgreSQL repository for MarchePulse."""

    def __init__(
        self,
        db_path: Path | str = "data/marchepulse.db",
        database_url: str = "",
    ) -> None:
        normalized_url = database_url.strip()
        if normalized_url.startswith("postgres://"):
            normalized_url = "postgresql://" + normalized_url[len("postgres://") :]

        self.database_url = normalized_url
        self.db_path = str(db_path)
        self.backend = "postgres" if self.database_url.startswith("postgresql://") else "sqlite"

        if self.backend == "sqlite" and self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[_CompatConnection]:
        """Yield a configured DB connection and commit/rollback cleanly."""
        if self.backend == "postgres":
            import psycopg
            from psycopg.rows import dict_row

            conn = psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=15)
            try:
                yield _CompatConnection(conn, "postgres")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON;")
                if self.db_path != ":memory:":
                    conn.execute("PRAGMA journal_mode = WAL;")
                yield _CompatConnection(conn, "sqlite")
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        auto_pk = (
            "SERIAL PRIMARY KEY"
            if self.backend == "postgres"
            else "INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        statements = _schema_statements(auto_pk)
        with self.connect() as conn:
            for stmt in statements:
                conn.execute(stmt)

    # ------------------------------------------------------------------
    # Tenders & Buyers cache
    # ------------------------------------------------------------------
    def upsert_tenders(self, tenders: list[NormalizedTender]) -> int:
        """Insert or update normalized tenders and return count written."""
        if not tenders:
            return 0
        now = _utc_now_iso()
        rows = [
            (
                t.idweb,
                t.title,
                t.buyer_name,
                json.dumps(t.departments, ensure_ascii=False),
                t.publication_date,
                t.deadline,
                t.days_remaining,
                int(t.is_open),
                json.dumps(t.market_types, ensure_ascii=False),
                t.nature,
                t.procedure,
                json.dumps(t.descriptor_codes, ensure_ascii=False),
                json.dumps(t.descriptors, ensure_ascii=False),
                t.family,
                t.source_url,
                t.quality_score,
                now,
            )
            for t in tenders
        ]
        sql = """
        INSERT INTO tenders (
            idweb, title, buyer_name, departments_json, publication_date, deadline,
            days_remaining, is_open, market_types_json, nature, procedure,
            descriptor_codes_json, descriptors_json, family, source_url,
            quality_score, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idweb) DO UPDATE SET
            title = excluded.title,
            buyer_name = excluded.buyer_name,
            departments_json = excluded.departments_json,
            publication_date = excluded.publication_date,
            deadline = excluded.deadline,
            days_remaining = excluded.days_remaining,
            is_open = excluded.is_open,
            market_types_json = excluded.market_types_json,
            nature = excluded.nature,
            procedure = excluded.procedure,
            descriptor_codes_json = excluded.descriptor_codes_json,
            descriptors_json = excluded.descriptors_json,
            family = excluded.family,
            source_url = excluded.source_url,
            quality_score = excluded.quality_score,
            updated_at = excluded.updated_at
        """
        with self.connect() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    def _row_to_tender(
        self, row: Any, buyer: BuyerEnrichment | None = None
    ) -> NormalizedTender:
        return NormalizedTender(
            idweb=row["idweb"],
            title=row["title"],
            buyer_name=row["buyer_name"],
            departments=json.loads(row["departments_json"]),
            publication_date=row["publication_date"],
            deadline=row["deadline"],
            days_remaining=row["days_remaining"],
            is_open=bool(row["is_open"]),
            market_types=json.loads(row["market_types_json"]),
            nature=row["nature"],
            procedure=row["procedure"],
            descriptor_codes=json.loads(row["descriptor_codes_json"]),
            descriptors=json.loads(row["descriptors_json"]),
            family=row["family"],
            source_url=row["source_url"],
            quality_score=row["quality_score"],
            buyer=buyer,
        )

    def query_tenders(
        self,
        *,
        q: str | None = None,
        department: str | None = None,
        market_type: str | None = None,
        only_open: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[NormalizedTender], int]:
        """Query cached tenders with filters and attach cached buyer enrichment."""
        clauses: list[str] = []
        params: list[object] = []

        if q:
            clauses.append(
                "(LOWER(t.title) LIKE LOWER(?) OR LOWER(t.buyer_name) LIKE LOWER(?) "
                "OR LOWER(t.descriptors_json) LIKE LOWER(?))"
            )
            like = f"%{q.strip()}%"
            params.extend([like, like, like])
        if department:
            clauses.append("t.departments_json LIKE ?")
            params.append(f'%"{department.strip()}"%')
        if market_type:
            clauses.append("UPPER(t.market_types_json) LIKE ?")
            params.append(f'%"{market_type.strip().upper()}"%')
        if only_open:
            clauses.append("t.is_open = 1")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self.connect() as conn:
            count_row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM tenders t {where_sql}", params
            ).fetchone()
            total = int(count_row["cnt"]) if count_row else 0

            rows = conn.execute(
                f"""
                SELECT t.*,
                       b.query_name AS b_query_name,
                       b.siren AS b_siren,
                       b.nom_complet AS b_nom_complet,
                       b.etat_administratif AS b_etat_administratif,
                       b.nature_juridique AS b_nature_juridique,
                       b.activite_principale AS b_activite_principale,
                       b.categorie_entreprise AS b_categorie_entreprise,
                       b.tranche_effectif_salarie AS b_tranche_effectif_salarie,
                       b.commune AS b_commune,
                       b.code_postal AS b_code_postal,
                       b.departement AS b_departement
                FROM tenders t
                LEFT JOIN buyers_cache b ON b.query_name = t.buyer_name
                {where_sql}
                ORDER BY t.publication_date DESC, t.quality_score DESC, t.idweb DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        items: list[NormalizedTender] = []
        for r in rows:
            buyer = None
            if r["b_query_name"] is not None:
                buyer = BuyerEnrichment(
                    query_name=r["b_query_name"],
                    siren=r["b_siren"],
                    nom_complet=r["b_nom_complet"],
                    etat_administratif=r["b_etat_administratif"],
                    nature_juridique=r["b_nature_juridique"],
                    activite_principale=r["b_activite_principale"],
                    categorie_entreprise=r["b_categorie_entreprise"],
                    tranche_effectif_salarie=r["b_tranche_effectif_salarie"],
                    commune=r["b_commune"],
                    code_postal=r["b_code_postal"],
                    departement=r["b_departement"],
                )
            items.append(self._row_to_tender(r, buyer=buyer))
        return items, total

    def get_tender(self, idweb: str) -> NormalizedTender | None:
        """Fetch one tender by idweb along with its cached buyer enrichment."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenders WHERE idweb = ?", (idweb.strip(),)
            ).fetchone()
            if not row:
                return None
            buyer = self.get_cached_buyer(row["buyer_name"])
            return self._row_to_tender(row, buyer=buyer)

    def count_tenders(self) -> int:
        """Return total cached tenders count."""
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM tenders").fetchone()
            return int(row["cnt"]) if row else 0

    def list_active_departments(self, limit: int = 30) -> list[str]:
        """Return distinct department codes present in recent open tenders."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT departments_json FROM tenders
                WHERE is_open = 1
                ORDER BY publication_date DESC
                LIMIT 200
                """
            ).fetchall()
        seen: list[str] = []
        for row in rows:
            for dep in json.loads(row["departments_json"]):
                if dep and dep not in seen:
                    seen.append(dep)
                    if len(seen) >= limit:
                        return seen
        return seen

    def get_cached_buyer(self, query_name: str) -> BuyerEnrichment | None:
        """Look up a previously enriched buyer by normalized query name."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM buyers_cache WHERE query_name = ?", (query_name,)
            ).fetchone()
        if not row:
            return None
        return BuyerEnrichment(
            query_name=row["query_name"],
            siren=row["siren"],
            nom_complet=row["nom_complet"],
            etat_administratif=row["etat_administratif"],
            nature_juridique=row["nature_juridique"],
            activite_principale=row["activite_principale"],
            categorie_entreprise=row["categorie_entreprise"],
            tranche_effectif_salarie=row["tranche_effectif_salarie"],
            commune=row["commune"],
            code_postal=row["code_postal"],
            departement=row["departement"],
        )

    def upsert_buyer(self, buyer: BuyerEnrichment) -> None:
        """Persist buyer enrichment in cache."""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO buyers_cache (
                    query_name, siren, nom_complet, etat_administratif,
                    nature_juridique, activite_principale, categorie_entreprise,
                    tranche_effectif_salarie, commune, code_postal, departement,
                    cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_name) DO UPDATE SET
                    siren = excluded.siren,
                    nom_complet = excluded.nom_complet,
                    etat_administratif = excluded.etat_administratif,
                    nature_juridique = excluded.nature_juridique,
                    activite_principale = excluded.activite_principale,
                    categorie_entreprise = excluded.categorie_entreprise,
                    tranche_effectif_salarie = excluded.tranche_effectif_salarie,
                    commune = excluded.commune,
                    code_postal = excluded.code_postal,
                    departement = excluded.departement,
                    cached_at = excluded.cached_at
                """,
                (
                    buyer.query_name,
                    buyer.siren,
                    buyer.nom_complet,
                    buyer.etat_administratif,
                    buyer.nature_juridique,
                    buyer.activite_principale,
                    buyer.categorie_entreprise,
                    buyer.tranche_effectif_salarie,
                    buyer.commune,
                    buyer.code_postal,
                    buyer.departement,
                    _utc_now_iso(),
                ),
            )

    def list_unenriched_buyers(self, limit: int = 15) -> list[str]:
        """Return distinct buyer names from tenders not yet present in buyers_cache."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT t.buyer_name
                FROM tenders t
                LEFT JOIN buyers_cache b ON b.query_name = t.buyer_name
                WHERE b.query_name IS NULL AND t.buyer_name != ''
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(r["buyer_name"]) for r in rows]

    # ------------------------------------------------------------------
    # API Keys & Quotas
    # ------------------------------------------------------------------
    def create_api_key(
        self,
        *,
        key_hash: str,
        customer_email: str,
        plan: str,
        provider: str,
        monthly_quota: int,
        external_customer_id: str | None = None,
        external_subscription_id: str | None = None,
    ) -> ApiKeyPublicInfo:
        """Create a new API key record."""
        now = _utc_now_iso()
        month = _current_month()
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO api_keys (
                    key_hash, customer_email, plan, provider,
                    external_customer_id, external_subscription_id,
                    monthly_quota, requests_used, usage_month, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?)
                RETURNING id
                """,
                (
                    key_hash,
                    customer_email,
                    plan,
                    provider,
                    external_customer_id,
                    external_subscription_id,
                    monthly_quota,
                    month,
                    now,
                    now,
                ),
            ).fetchone()
            key_id = int(row["id"])
        return ApiKeyPublicInfo(
            id=key_id,
            customer_email=customer_email,
            plan=plan,
            provider=provider,
            monthly_quota=monthly_quota,
            requests_used=0,
            active=True,
        )

    def has_key_for_email(self, email: str, provider: str | None = None) -> bool:
        """Check whether an email already owns an API key (optionally for a provider)."""
        clean_email = email.strip().lower()
        with self.connect() as conn:
            if provider is not None:
                row = conn.execute(
                    "SELECT 1 FROM api_keys WHERE customer_email = ? AND provider = ?",
                    (clean_email, provider),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM api_keys WHERE customer_email = ?",
                    (clean_email,),
                ).fetchone()
        return row is not None

    def consume_api_key_quota(self, key_hash: str) -> tuple[ApiKeyPublicInfo | None, str | None]:
        """Validate an API key and atomically increment its monthly usage.

        Returns `(key_info, error_code)` where `error_code` is None on success,
        `'invalid_key'`, `'inactive_key'`, or `'quota_exceeded'`.
        """
        month = _current_month()
        now = _utc_now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            if not row:
                return None, "invalid_key"
            if not row["active"]:
                return None, "inactive_key"

            if row["usage_month"] != month:
                conn.execute(
                    "UPDATE api_keys SET requests_used = 0, usage_month = ?, updated_at = ? WHERE id = ?",
                    (month, now, row["id"]),
                )
                used = 0
            else:
                used = int(row["requests_used"])

            quota = int(row["monthly_quota"])
            if used >= quota:
                return None, "quota_exceeded"

            cur = conn.execute(
                """
                UPDATE api_keys
                SET requests_used = requests_used + 1, updated_at = ?
                WHERE id = ? AND active = 1 AND requests_used < monthly_quota
                """,
                (now, row["id"]),
            )
            if cur.rowcount == 0:
                return None, "quota_exceeded"

            updated = conn.execute(
                "SELECT * FROM api_keys WHERE id = ?", (row["id"],)
            ).fetchone()

        return (
            ApiKeyPublicInfo(
                id=int(updated["id"]),
                customer_email=updated["customer_email"],
                plan=updated["plan"],
                provider=updated["provider"],
                monthly_quota=int(updated["monthly_quota"]),
                requests_used=int(updated["requests_used"]),
                active=bool(updated["active"]),
            ),
            None,
        )

    def update_key_status_by_subscription(
        self,
        *,
        external_subscription_id: str,
        active: bool,
        plan: str | None = None,
        monthly_quota: int | None = None,
    ) -> int:
        """Activate, deactivate, or change plan for keys tied to a billing subscription."""
        now = _utc_now_iso()
        with self.connect() as conn:
            if plan is not None and monthly_quota is not None:
                cur = conn.execute(
                    """
                    UPDATE api_keys
                    SET active = ?, plan = ?, monthly_quota = ?, updated_at = ?
                    WHERE external_subscription_id = ?
                    """,
                    (int(active), plan, monthly_quota, now, external_subscription_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE api_keys
                    SET active = ?, updated_at = ?
                    WHERE external_subscription_id = ?
                    """,
                    (int(active), now, external_subscription_id),
                )
            return int(cur.rowcount)

    def find_key_by_subscription(self, external_subscription_id: str) -> ApiKeyPublicInfo | None:
        """Find an existing key by external billing subscription ID."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE external_subscription_id = ?",
                (external_subscription_id,),
            ).fetchone()
        if not row:
            return None
        return ApiKeyPublicInfo(
            id=int(row["id"]),
            customer_email=row["customer_email"],
            plan=row["plan"],
            provider=row["provider"],
            monthly_quota=int(row["monthly_quota"]),
            requests_used=int(row["requests_used"]),
            active=bool(row["active"]),
        )

    # ------------------------------------------------------------------
    # Alert Subscriptions & Delivery Logs
    # ------------------------------------------------------------------
    def create_alert_subscription(
        self, owner_key_id: int, payload: AlertSubscriptionCreate
    ) -> AlertSubscription:
        """Create a new alert subscription owned by an API key."""
        now = _utc_now_iso()
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO alert_subscriptions (
                    owner_key_id, name, keywords_json, departments_json,
                    market_types_json, only_open, channel, target, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                RETURNING id
                """,
                (
                    owner_key_id,
                    payload.name,
                    json.dumps([k.strip() for k in payload.keywords if k.strip()], ensure_ascii=False),
                    json.dumps([d.strip() for d in payload.departments if d.strip()], ensure_ascii=False),
                    json.dumps([m.strip().upper() for m in payload.market_types if m.strip()], ensure_ascii=False),
                    int(payload.only_open),
                    payload.channel,
                    payload.target.strip(),
                    now,
                ),
            ).fetchone()
            sub_id = int(row["id"])
        return AlertSubscription(
            id=sub_id,
            owner_key_id=owner_key_id,
            name=payload.name,
            keywords=[k.strip() for k in payload.keywords if k.strip()],
            departments=[d.strip() for d in payload.departments if d.strip()],
            market_types=[m.strip().upper() for m in payload.market_types if m.strip()],
            only_open=payload.only_open,
            channel=payload.channel,
            target=payload.target.strip(),
            active=True,
            created_at=datetime.fromisoformat(now),
        )

    def _row_to_alert(self, row: Any) -> AlertSubscription:
        return AlertSubscription(
            id=int(row["id"]),
            owner_key_id=int(row["owner_key_id"]),
            name=row["name"],
            keywords=json.loads(row["keywords_json"]),
            departments=json.loads(row["departments_json"]),
            market_types=json.loads(row["market_types_json"]),
            only_open=bool(row["only_open"]),
            channel=row["channel"],
            target=row["target"],
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_alert_subscriptions(self, owner_key_id: int | None = None) -> list[AlertSubscription]:
        """List active alert subscriptions, optionally filtered by owner API key."""
        with self.connect() as conn:
            if owner_key_id is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM alert_subscriptions
                    WHERE owner_key_id = ? AND active = 1
                    ORDER BY id DESC
                    """,
                    (owner_key_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT a.* FROM alert_subscriptions a
                    JOIN api_keys k ON k.id = a.owner_key_id
                    WHERE a.active = 1 AND k.active = 1
                    ORDER BY a.id ASC
                    """
                ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def deactivate_alert_subscription(self, sub_id: int, owner_key_id: int) -> bool:
        """Soft-delete an alert subscription owned by `owner_key_id`."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE alert_subscriptions SET active = 0 WHERE id = ? AND owner_key_id = ?",
                (sub_id, owner_key_id),
            )
            return int(cur.rowcount) > 0

    def has_delivered(self, subscription_id: int, tender_idweb: str) -> bool:
        """Check whether a tender was already delivered for a subscription."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM delivery_logs
                WHERE subscription_id = ? AND tender_idweb = ? AND status = 'sent'
                """,
                (subscription_id, tender_idweb),
            ).fetchone()
            return row is not None

    def record_deliveries(
        self, subscription_id: int, tender_idwebs: list[str], status: str
    ) -> None:
        """Record notification delivery outcomes."""
        if not tender_idwebs:
            return
        now = _utc_now_iso()
        rows = [(subscription_id, tid, status, now) for tid in tender_idwebs]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO delivery_logs (subscription_id, tender_idweb, status, sent_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(subscription_id, tender_idweb) DO UPDATE SET
                    status = excluded.status,
                    sent_at = excluded.sent_at
                """,
                rows,
            )

    # ------------------------------------------------------------------
    # Billing Events Idempotency
    # ------------------------------------------------------------------
    def register_billing_event(self, event_id: str, provider: str, event_type: str) -> bool:
        """Record a billing event ID; return False if already processed."""
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO billing_events (event_id, provider, event_type, processed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                RETURNING event_id
                """,
                (event_id, provider, event_type, _utc_now_iso()),
            ).fetchone()
        if row is None:
            logger.info("Duplicate billing event ignored: %s (%s)", event_id, provider)
            return False
        return True
