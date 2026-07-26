# Testing

How tests work in this repository, and how to add one.

---

## The model

Tests run against an **in-memory SQLite** database, one per test, with no network and no `.env`. A test creates the schema, runs, and drops the schema. Nothing persists between tests.

This is fast and isolated. It also means tests exercise the SQLite path, not PostgreSQL. A PostgreSQL-only issue (a server default, a specific type, a concurrency behaviour) does not appear in a unit test — catch it with the production-like Docker run instead. See [local-development.md](./local-development.md).

---

## Running tests

```bash
pytest tests/ -v                    # all tests
pytest tests/test_auth.py -v        # one file
pytest tests/test_auth.py::test_login -v   # one test
```

`pyproject.toml` sets `asyncio_mode = "auto"`, so an `async def test_...` runs without an explicit decorator on newer setups. The existing tests still mark themselves with `@pytest.mark.asyncio`, which is fine and explicit — match the existing style.

---

## How the fixtures work

`tests/conftest.py` provides two things.

**1. The environment, set before any import.**

```python
import os
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-used-in-production")
# ... then the imports
```

This runs at the top of the file, above the imports, on purpose. `pydantic-settings` reads the environment when `Settings()` is constructed at import time. If the app imports before these lines run, it reads your real `.env`. **Do not move these lines.**

**2. A fresh schema and an async client per test.**

```python
@pytest.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    async def override_get_db():
        async with TestSession() as session:
            yield session
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
```

- `setup_db` is `autouse`, so every test starts with an empty schema.
- `client` overrides `get_db` with the test session and drives the app in-process through `ASGITransport` — no real network, no running server.

---

## Writing a test

Request the `client` fixture and call the API as a real client would.

```python
import pytest


@pytest.mark.asyncio
async def test_create_thing(client):
    # Register and log in to get a token
    await client.post("/auth/register", json={"email": "a@b.com", "password": "secret123"})
    login = await client.post("/auth/login", json={"email": "a@b.com", "password": "secret123"})
    token = login.json()["access_token"]

    # Call the protected endpoint
    response = await client.post(
        "/things/",
        json={"name": "example"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "example"
```

Patterns to follow (see `tests/test_auth.py`):

- **Test through the API, not the service directly**, for endpoint behaviour. It covers routing, validation, auth, and the response shape in one test.
- **Register then log in** to get a token for a protected route.
- **Assert the status code and the response body.** A 200 with the wrong body is still a bug.
- **One behaviour per test.** Name it for the behaviour: `test_login_rejects_wrong_password`.

---

## What to test

- **The happy path** of each endpoint.
- **The error paths that your code raises**: a duplicate email is a 409, a wrong password is a 401, an inactive account is a 403, a missing row is a 404.
- **Auth boundaries**: a protected route with no token is a 401; with a valid token it succeeds.
- **A service method with real logic**, directly, when the logic is worth isolating from HTTP.

You do not need to test framework behaviour (Pydantic already rejects a malformed body with a 422) or the standard library.

---

## What is hard to test here

- **The `ai_chat` model call.** It is a stub today. When you wire a real provider, do not call the live API in a test — inject or patch the client and assert on the recorded messages, not on a real completion.
- **PostgreSQL-specific behaviour.** SQLite does not reproduce every PostgreSQL rule. Use the Docker-against-Neon run for those.
- **Migrations.** Tests build the schema from the models, not from the migration history. Verify a migration separately: `alembic upgrade head` then `alembic downgrade -1`.

---

## The gate

CI runs exactly three checks (`.github/workflows/ci.yml`):

```bash
ruff check .
ruff format --check .
pytest tests/ -v
```

Run all three before you call the work done. A green `pytest` with a failing `ruff` still fails CI.
