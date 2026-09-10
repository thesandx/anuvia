from sqlalchemy import event
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


def enforce_sqlite_foreign_keys(target) -> None:
    """Makes SQLite check foreign keys, which it does not do by default.

    Production is PostgreSQL, which always enforces them. Without this, a
    foreign-key bug — a row written before the row it points at, say — passes
    every local test and fails on the first real deploy. Turning it on is what
    makes the local database tell the truth.

    Exported rather than applied once, because the tests build their own engine
    and need the same behaviour. It is a no-op on any other dialect.
    """
    if not target.url.get_backend_name().startswith("sqlite"):
        return

    @event.listens_for(target.sync_engine, "connect")
    def _pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


enforce_sqlite_foreign_keys(engine)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with SessionLocal() as session:
        yield session
