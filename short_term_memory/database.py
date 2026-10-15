# short_term_memory/database.py
#
# The ONLY place the engine and session factory are created.
import logging
import os

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base

logger = logging.getLogger("short_term_memory.database")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://lyra:lyra_db_password@short-term-db:5432/short_term_memory",
)

# Accept plain postgres:// or postgresql:// URLs and route them through asyncpg
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _check_existing_tables(sync_conn) -> None:
    """
    create_all() only creates tables that are missing; it never adds columns to
    a table that already exists. So if a live table is behind models.py, stop
    here with a clear message instead of failing later on some random request.
    """
    inspector = inspect(sync_conn)
    problems = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # create_all() will build it
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        missing = [col.name for col in table.columns if col.name not in existing]
        if missing:
            problems.append(f"{table.name}: missing {', '.join(missing)}")
    if problems:
        raise RuntimeError(
            "Database is behind models.py. Run the migration script first.\n  "
            + "\n  ".join(problems)
        )


async def init_db() -> None:
    """Verify existing tables match models.py, then create any that are missing."""
    async with engine.begin() as conn:
        await conn.run_sync(_check_existing_tables)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema verified.")


async def get_db():
    """FastAPI dependency: one async session per request."""
    async with AsyncSessionLocal() as session:
        yield session
