from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# asyncpg (PostgreSQL) requires SSL for cloud providers like Neon.
# aiosqlite (SQLite) doesn't support connect_args at all.
_connect_args = {"ssl": "require"} if settings.DATABASE_URL.startswith("postgresql") else {}

engine = create_async_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    echo=settings.DEBUG,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with SessionLocal() as session:
        yield session
