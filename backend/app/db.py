from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.config import get_settings

settings = get_settings()
# Celery tasks use a fresh asyncio loop per execution. NullPool prevents an
# asyncpg connection created by one task loop from leaking into another loop.
engine = create_async_engine(settings.database_url, poolclass=NullPool)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
# HTTP requests share one event loop and can safely reuse bounded connections.
# Celery retains NullPool because each task owns a separate asyncio.run loop.
api_engine = create_async_engine(settings.database_url, pool_size=5, max_overflow=5, pool_pre_ping=True)
ApiSessionLocal = async_sessionmaker(api_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with ApiSessionLocal() as session:
        yield session
