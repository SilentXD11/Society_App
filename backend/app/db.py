from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        url = get_settings().database_url
        connect_args = {}
        if "-pooler." in url:
            # Neon's pooled endpoint runs PgBouncer in transaction mode, which
            # breaks asyncpg's prepared-statement cache.
            connect_args["statement_cache_size"] = 0
        # Small pool + recycle: free-tier databases suspend idle connections after ~5 minutes.
        _engine = create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5,
                                      pool_recycle=240, connect_args=connect_args)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """One transaction per request: commit on success, roll back on error."""
    async with sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
