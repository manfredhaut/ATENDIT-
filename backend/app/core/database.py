import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger("atendit.database")


class Base(DeclarativeBase):
    """Classe base declarativa para todos os modelos ORM do SQLAlchemy."""
    pass


# Inicialização da Engine Assíncrona do PostgreSQL com pool de conexões otimizado
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Sessionmaker assíncrono para injeção de dependência nas rotas FastAPI
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependência assíncrona do FastAPI que fornece uma sessão de banco de dados.
    Garante commit em caso de sucesso e rollback automático em caso de exceção.
    """
    async with AsyncSessionLocal() as session:
        logger.debug("[DB SESSION] Sessão de banco de dados aberta.")
        try:
            yield session
            await session.commit()
            logger.debug("[DB SESSION] Transação confirmada (commit) com sucesso.")
        except Exception as exc:
            await session.rollback()
            logger.error(f"[DB SESSION ERROR] Transação revertida (rollback) devido a erro: {str(exc)}")
            raise
        finally:
            await session.close()
            logger.debug("[DB SESSION] Sessão de banco de dados fechada.")
