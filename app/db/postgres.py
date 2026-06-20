"""
app/db/postgres.py

Async SQLAlchemy engine + session factory for PostgreSQL.

Why async?
FastAPI is built on async Python (ASGI). Using an async DB driver
(asyncpg) means the event loop is never blocked by a DB call —
other requests can be handled while we wait for Postgres.

Usage in route handlers:
  async def some_route(db: AsyncSession = Depends(get_db)):
      result = await db.execute(select(User).where(User.id == user_id))
"""
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# create_async_engine is the async equivalent of create_engine.
# pool_pre_ping=True — checks connection health before each use,
# preventing "server closed the connection unexpectedly" errors after idle.
engine = create_async_engine(
    settings.database_async_url,
    echo=settings.debug,          # logs SQL in dev; silent in production
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Session factory. expire_on_commit=False means we can still access
# ORM attributes after commit() without re-querying the database.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """
    All SQLAlchemy models inherit from this.
    Defined here (not in models/) so the engine and Base are in one place.
    """
    pass


async def get_db():
    """
    FastAPI dependency that yields a database session per request.
    The session is committed if no error occurred, or rolled back if one did.
    Always closed at the end.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
