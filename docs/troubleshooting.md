# Troubleshooting

Symptom → cause → fix. Check here first when something fails.

---

## Database and connection

### `asyncpg` rejects the connection string / `unexpected keyword argument 'sslmode'`

**Cause:** the Neon dashboard gives a `postgresql://...?sslmode=require&channel_binding=require` string. `asyncpg` does not accept `sslmode` or `channel_binding` as query parameters.

**Fix:** edit the string.

1. Change `postgresql://` to `postgresql+asyncpg://`.
2. Remove `?sslmode=require` and any other query parameters.

SSL is set in code — `app/core/database.py` adds `connect_args={"ssl": "require"}` for a `postgresql` URL. The correct form:

```
postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb
```

### `TypeError: 'ssl' is an invalid keyword argument` on SQLite

**Cause:** something passes `connect_args={"ssl": ...}` to the SQLite driver. `aiosqlite` does not accept it.

**Fix:** do not remove the branch in `app/core/database.py`. It adds SSL args only when the URL starts with `postgresql`. SQLite gets `{}`.

### The app starts but every query is slow

**Cause (most common):** the app and the database are in different regions. Every query pays the cross-region round trip, and one request runs several queries.

**Fix:** co-locate them. Put the Cloud Run region and the Neon project region in the same geography. See [`cloud/multi-region.md`](../cloud/multi-region.md).

---

## Migrations

### `alembic revision --autogenerate` produces an empty migration

**Cause:** Alembic did not import your new model, so it is not in `target_metadata`.

**Fix:** add the import to `alembic/env.py`:

```python
import app.apps.my_product.models  # noqa: F401
```

Then run autogenerate again.

### A migration applied but a column is missing

**Cause:** autogenerate does not detect every change. It misses some renames, server defaults, and constraint changes.

**Fix:** read every generated migration before you commit it. Add the missing operation by hand. Test with `alembic upgrade head` then `alembic downgrade -1`.

### `alembic upgrade head` fails on a fresh clone with a config error

**Cause:** `alembic/env.py` imports `Settings`, which requires `SECRET_KEY`. Without `.env`, the import fails.

**Fix:** `cp .env.example .env` and set `SECRET_KEY`.

### Migrations race or deadlock on Cloud Run

**Cause:** the container `CMD` runs `alembic upgrade head` on every instance start. With more than one instance, they race.

**Fix:** move the migration to a single deploy-time step and remove it from the `CMD`. See [`.github/instructions/deployment.md`](../.github/instructions/deployment.md).

---

## Tests

### `pytest` uses my real database instead of in-memory SQLite

**Cause:** the app imported before `tests/conftest.py` set `DATABASE_URL`. `pydantic-settings` reads the environment at import time.

**Fix:** keep the `os.environ["DATABASE_URL"] = ...` lines at the very top of `conftest.py`, above every import. Do not move them.

### A test hangs or warns about the event loop

**Cause:** a blocking call inside an async test, or a mismatched loop scope.

**Fix:** await every async call. `pyproject.toml` sets `asyncio_default_fixture_loop_scope = "function"` — keep it. Do not call a synchronous DB driver in a test.

---

## Application

### `/docs` returns 404 in production

**Not a bug.** `main.py` disables `/docs` and `/redoc` when `APP_ENV=production`. Set `APP_ENV=development` to see them locally.

### A protected endpoint returns 401 with a valid-looking token

**Causes, in order:**

1. The `SECRET_KEY` that signed the token differs from the one verifying it. A token signed with the local key fails against the production key. This is expected across environments.
2. The token expired. The default lifetime is 30 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`).
3. The `Authorization` header is malformed. It must be `Bearer <token>`.

### The response leaks `hashed_password` or another internal field

**Cause:** the route returned a raw ORM object, or a response schema included the column.

**Fix:** set `response_model` to a schema that omits the field. `UserResponse` is the pattern — it has no `hashed_password`.

### A request stalls the whole server

**Cause:** a blocking call inside a request — `requests`, a synchronous driver, `time.sleep`, a blocking file or subprocess call. It freezes the event loop for every concurrent user.

**Fix:** use an async client (`httpx.AsyncClient`) with a timeout, and `await` it.

---

## Cloud Run

### "The user-provided container failed to start and listen on the port"

**Causes:**

1. The server binds `localhost` instead of `0.0.0.0`. Keep `--host 0.0.0.0` in the `CMD`.
2. The server ignores `$PORT`. Keep `--port ${PORT}`.
3. The migration in the `CMD` failed, so the server never started. Check the logs for the Alembic error. Reproduce with the Docker-against-Neon run.

### A secret value is visible in `gcloud run services describe`

**Cause:** the secret was passed with `--set-env-vars`, which stores it in the revision.

**Fix:** move it to Secret Manager and reference it with `--set-secrets`. See [`cloud/environment-variables.md`](../cloud/environment-variables.md).

### The deploy authenticated but cannot push or deploy

**Cause:** the service account lacks a role. It needs `roles/run.admin`, `roles/storage.admin` (or Artifact Registry writer), and `roles/iam.serviceAccountUser`.

**Fix:** grant the missing role. See [`cloud/github-actions.md`](../cloud/github-actions.md).

---

## When nothing here matches

1. Reproduce with the production-like Docker run — it removes the SQLite-vs-PostgreSQL variable.
2. Read the actual error and the failing step, not the summary.
3. Check whether a [trap in `CLAUDE.md`](../CLAUDE.md#traps--things-that-look-wrong-and-are-not) describes the behaviour — several failures are things that look wrong and are correct.
