import os

# Force in-memory SQLite for all tests regardless of what .env contains.
# Must be set before any app module is imported so pydantic-settings picks it up.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-used-in-production")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, enforce_sqlite_foreign_keys, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL)
# The tests build their own engine, so they have to opt into foreign-key
# enforcement the same way the application engine does. Without this the
# tests are more permissive than production, which is the wrong direction.
enforce_sqlite_foreign_keys(test_engine)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def db_session():
    """The test session factory, for a test that has to reach past the API.

    Used to age a row so a time-based rule (a host who stopped being seen) can
    be exercised without a sleep.
    """
    return TestSession


@pytest.fixture
async def client():
    async def override_get_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
