"""asyncpg connection pool and a health ping.

A single lazily-created pool is shared across tool calls. pgvector type codecs
are registered on each new connection so VECTOR columns round-trip as lists.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from src.config import settings
from src.logging import get_logger

logger = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=5,
            init=_init_connection,
        )
    return _pool


async def close_pool() -> None:
    """Close the shared pool, if open."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ping() -> bool:
    """Return True if Postgres is reachable, False otherwise (logged)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            value: Any = await conn.fetchval("SELECT 1")
        return bool(value == 1)
    except Exception as exc:
        logger.warning("postgres_ping_failed", error=str(exc))
        return False
