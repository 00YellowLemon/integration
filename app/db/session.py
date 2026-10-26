"""Database engine, session factory, and initialization for async PostgreSQL / Supabase."""
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.base import Base
# Ensure all models are imported so Base.metadata knows about them
from app.models.account import Account  # noqa: F401
from app.models.thread import Thread  # noqa: F401
from app.models.message import Message  # noqa: F401
from app.models.analysis import EmailAnalysis, ActionItem  # noqa: F401


_engine: Optional[AsyncEngine] = None
_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Get or create singleton async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        # pool_pre_ping helps with Supabase cloud connection timeouts
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Get or create singleton async session maker."""
    global _session_maker
    if _session_maker is None:
        engine = get_engine()
        _session_maker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db(engine: Optional[AsyncEngine] = None) -> None:
    """Initialize database tables and views idempotently."""
    db_engine = engine or get_engine()
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.db.views import create_views
    await create_views(db_engine)

