"""Seeds ``enriched_unified_data`` from the provided .xlsx/.csv file at startup.

Idempotent: if the table already has rows it is left alone (set FORCE_RESEED=true
to reload). A Postgres advisory lock prevents two backend replicas seeding at once.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .config import TABLE_NAME, Settings
from .db import Database

log = logging.getLogger(__name__)

SEED_LOCK_ID = 774_211  # arbitrary constant for pg_advisory_lock

# Column -> Postgres type. Order defines the table layout.
SCHEMA: dict[str, str] = {
    "uuid": "TEXT NOT NULL",
    "data_source_type": "TEXT",
    "engagement_date": "TIMESTAMP",
    "created_at": "TIMESTAMP",
    "customer_voice_id": "TEXT",
    "customer_voice": "TEXT",
    "product_name": "TEXT",
    "product_family": "TEXT",
    "product_type": "TEXT",
    "brand_name": "TEXT",
    "retailer_name": "TEXT",
    "country": "TEXT",
    "region": "TEXT",
    "maintheme": "TEXT",
    "subtheme": "TEXT",
    "explanation": "TEXT",
    "sentiment": "TEXT",
    "sentiment_intensity": "INTEGER",
    "sentiment_score": "INTEGER",
    "emotion": "TEXT",
    "emotional_intensity": "INTEGER",
    "customer_intent": "TEXT",
    "product_aspects": "TEXT",
    "churn_risk_signal": "TEXT",
    "price_perception": "TEXT",
    "urgency_level": "TEXT",
    "urgency_reason": "TEXT",
    "shopping_journey_stage": "TEXT",
    "customer_rating": "INTEGER",
}
INT_COLS = [c for c, t in SCHEMA.items() if t.startswith("INTEGER")]
TS_COLS = ["engagement_date", "created_at"]
INDEXED_COLS = [
    "uuid", "engagement_date", "brand_name", "product_family", "retailer_name",
    "country", "maintheme", "subtheme", "sentiment", "data_source_type",
]


def resolve_data_file(settings: Settings) -> Path:
    preferred = Path(settings.data_file)
    if preferred.is_file():
        return preferred
    data_dir = Path(settings.data_dir)
    for pattern in ("*.xlsx", "*.csv"):
        matches = sorted(data_dir.glob(pattern))
        if matches:
            log.info("DATA_FILE %s not found, using %s", preferred, matches[0])
            return matches[0]
    raise FileNotFoundError(
        f"No dataset found. Put enriched_unified_data.xlsx (or .csv) in {data_dir}"
    )


def load_dataframe(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, engine="openpyxl")
    else:
        df = pd.read_csv(path)

    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in SCHEMA if c not in df.columns]
    if missing:
        raise ValueError(f"Dataset is missing required columns: {missing}")
    df = df[list(SCHEMA)].copy()

    for col in TS_COLS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in INT_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").round().astype("Int64")
    text_cols = [c for c in SCHEMA if c not in INT_COLS and c not in TS_COLS]
    for col in text_cols:
        df[col] = df[col].astype("string").str.strip()
        df.loc[df[col] == "", col] = pd.NA

    before = len(df)
    df = df.dropna(subset=["uuid"])
    if len(df) != before:
        log.warning("Dropped %d rows without uuid", before - len(df))
    return df


def _records(df: pd.DataFrame) -> list[tuple]:
    out = []
    for row in df.itertuples(index=False, name=None):
        rec = []
        for col, value in zip(SCHEMA, row):
            if pd.isna(value):
                rec.append(None)
            elif col in TS_COLS:
                rec.append(value.to_pydatetime())
            elif col in INT_COLS:
                rec.append(int(value))
            else:
                rec.append(str(value))
        out.append(tuple(rec))
    return out


async def ensure_seeded(db: Database, settings: Settings) -> int:
    """Create table, load data if empty, provision read-only role. Returns row count."""
    assert db.admin_pool is not None
    async with db.admin_pool.acquire() as con:
        await con.execute("SELECT pg_advisory_lock($1)", SEED_LOCK_ID)
        try:
            cols_ddl = ",\n  ".join(f"{c} {t}" for c, t in SCHEMA.items())
            await con.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} (\n  row_id BIGSERIAL PRIMARY KEY,\n  {cols_ddl}\n)"
            )
            for col in INDEXED_COLS:
                await con.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_{col} ON {TABLE_NAME} ({col})"
                )

            count = await con.fetchval(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            if count and not settings.force_reseed:
                log.info("Table already seeded (%d rows), skipping load", count)
            else:
                path = resolve_data_file(settings)
                df = load_dataframe(path)
                async with con.transaction():
                    await con.execute(f"TRUNCATE {TABLE_NAME} RESTART IDENTITY")
                    await con.copy_records_to_table(
                        TABLE_NAME, records=_records(df), columns=list(SCHEMA)
                    )
                await con.execute(f"ANALYZE {TABLE_NAME}")
                count = len(df)
                log.info("Seeded %d rows from %s", count, path.name)

            await _provision_reader(con, settings)
            return int(count)
        finally:
            await con.execute("SELECT pg_advisory_unlock($1)", SEED_LOCK_ID)


async def _provision_reader(con, settings: Settings) -> None:
    """Least-privilege role: can only SELECT from the one table, read-only by default."""
    user = settings.readonly_db_user
    if not user.replace("_", "").isalnum():
        raise ValueError("READONLY_DB_USER must be alphanumeric/underscore")
    pwd = settings.readonly_db_password.get_secret_value().replace("'", "''")
    exists = await con.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", user)
    verb = "ALTER" if exists else "CREATE"
    await con.execute(f"{verb} ROLE {user} LOGIN PASSWORD '{pwd}'")
    db_name = await con.fetchval("SELECT current_database()")
    await con.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO {user}')
    await con.execute(f"GRANT USAGE ON SCHEMA public TO {user}")
    await con.execute(f"REVOKE CREATE ON SCHEMA public FROM {user}")
    await con.execute(f"GRANT SELECT ON {TABLE_NAME} TO {user}")
    await con.execute(f"ALTER ROLE {user} SET default_transaction_read_only = on")
    await con.execute(
        f"ALTER ROLE {user} SET statement_timeout = {int(settings.sql_statement_timeout_ms)}"
    )
