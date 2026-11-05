"""Database access.

Two connection pools are used on purpose:

* ``admin_pool``  - owner role, used only at startup to create/seed the table and
  to provision the read-only role.
* ``reader_pool`` - least-privilege ``voc_reader`` role (SELECT on one table only,
  ``default_transaction_read_only = on``). Every query that originates from the
  LLM runs through this pool, inside a READ ONLY transaction with a statement
  timeout. Even if the SQL guard were bypassed, the database itself refuses writes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from decimal import Decimal
from typing import Any

import asyncpg

from .config import Settings

log = logging.getLogger(__name__)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return round(float(value), 2)
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def records_to_dicts(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [{k: _to_jsonable(v) for k, v in r.items()} for r in records]


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.admin_pool: asyncpg.Pool | None = None
        self.reader_pool: asyncpg.Pool | None = None

    async def connect_admin(self, retries: int = 30, delay_s: float = 2.0) -> None:
        s = self.settings
        for attempt in range(1, retries + 1):
            try:
                self.admin_pool = await asyncpg.create_pool(
                    host=s.postgres_host,
                    port=s.postgres_port,
                    database=s.postgres_db,
                    user=s.postgres_user,
                    password=s.postgres_password.get_secret_value(),
                    min_size=1,
                    max_size=2,
                )
                log.info("Connected to Postgres as admin")
                return
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("DB not ready (attempt %d/%d): %s", attempt, retries, exc)
                await asyncio.sleep(delay_s)
        raise RuntimeError("Could not connect to Postgres")

    async def connect_reader(self) -> None:
        s = self.settings
        self.reader_pool = await asyncpg.create_pool(
            host=s.postgres_host,
            port=s.postgres_port,
            database=s.postgres_db,
            user=s.readonly_db_user,
            password=s.readonly_db_password.get_secret_value(),
            min_size=1,
            max_size=10,
        )
        log.info("Read-only pool ready (%s)", s.readonly_db_user)

    async def close(self) -> None:
        for pool in (self.reader_pool, self.admin_pool):
            if pool is not None:
                await pool.close()

    async def fetch_readonly(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Run a query as the read-only role, inside a READ ONLY transaction."""
        assert self.reader_pool is not None, "reader pool not initialised"
        timeout_ms = int(self.settings.sql_statement_timeout_ms)
        async with self.reader_pool.acquire() as con:
            async with con.transaction(readonly=True):
                await con.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                records = await con.fetch(sql, *params)
        return records_to_dicts(records)

    async def ping(self) -> bool:
        try:
            await self.fetch_readonly("SELECT 1 AS ok")
            return True
        except Exception:  # noqa: BLE001 - health check must not raise
            return False
