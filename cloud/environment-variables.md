# Environment variables

How configuration reaches the app, and how to add a variable.

---

## The model

Every variable is read in exactly one place: `app/core/config.py`, through `pydantic-settings`. Nothing else reads the environment. The value's source differs by environment:

```
Local dev    .env file (git-ignored)      → Settings → app
Tests        set in tests/conftest.py      → Settings → app  (dummy values)
CI           env: block in ci.yml          → Settings → app  (dummy values)
Production   Cloud Run environment          → Settings → app
```

No secret is ever in the repository. `.env`, `.env.docker`, and `*.db` are git-ignored.

---

## The variables

| Variable                    | Required | Default                          | Purpose                                   |
| --------------------------- | -------- | -------------------------------- | ----------------------------------------- |
| `SECRET_KEY`                | **Yes**  | — (fails at startup if missing)  | JWT signing key. Min 32 random chars.     |
| `DATABASE_URL`              | No       | `sqlite+aiosqlite:///./local.db` | SQLAlchemy async connection string.       |
| `APP_NAME`                  | No       | `anuvia`                         | Shown in the API docs.                    |
| `APP_ENV`                   | No       | `development`                    | `production` disables `/docs` and `/redoc`. |
| `DEBUG`                     | No       | `false`                          | `true` echoes every SQL query.            |
| `ALGORITHM`                 | No       | `HS256`                          | JWT signing algorithm.                    |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No     | `30`                             | JWT lifetime in minutes.                  |
| `STRIPE_SECRET_KEY`         | No       | `""`                             | Stripe integration.                       |
| `STRIPE_WEBHOOK_SECRET`     | No       | `""`                             | Stripe webhook signature check.           |
| `DEPLOYED_AT`               | No       | `""`                             | UTC deploy time (ISO-8601), set by the workflow. `/health` renders it in IST. |
| `CORS_ALLOW_ORIGINS`        | No       | `*`                              | Browser origins allowed to call the API. Comma-separated. `*` is for local development only. |
| `PLAYROOM_MAINTENANCE_TOKEN` | No      | `""`                             | Bearer token for `POST /games/v1/maintenance/sweep`. Empty disables the endpoint. Set it if you schedule the sweep. |
| `PLAYROOM_ROOM_TTL_HOURS`   | No       | `2`                              | Hours a room stays reachable after its last change. The client's copy says two hours — change both together. |
| `PLAYROOM_RETENTION_DAYS`   | No       | `7`                              | Days before the sweeper drops nicknames, boards and selections. Rows and ids are kept. |

All of the above are read by `app/core/config.py`. The workflows also use a few values that never reach the app:

| Name | Kind | Used by | Purpose |
| --- | --- | --- | --- |
| `NEON_API_KEY` | Secret | `ci.yml` | Creates and deletes the per-pull-request Neon branch. Optional — the job skips without it. |
| `NEON_PROJECT_ID` | Variable | `ci.yml` | The Neon project to branch from. Its presence is what enables the job. |
| `NEON_PRODUCTION_BRANCH` | Variable | `ci.yml` | Parent branch to clone. Defaults to `production`. |
| `DATABASE_URL_UNPOOLED` | Secret | `deploy.yml` | Neon's **direct** endpoint (hostname without `-pooler`), used only to run migrations. Optional — falls back to `DATABASE_URL`. |

## Latency: put the app and the database in the same geography

This is measurable, not theoretical. A single Playroom move runs about a dozen
sequential queries, so every millisecond between the app and the database is
paid a dozen times.

Measured against a Neon project in `us-east-2` from a client in India — roughly
the worst placement possible, and **not** how this is deployed:

| Request | Time |
| --- | --- |
| One in-game move | ~4.0 s |
| A room read | ~2.7 s |
| A room read that answers `304` | ~1.2 s |

The client gives up after eight seconds, so that placement is close to unusable
even though nothing is wrong with the code. Co-locate Cloud Run and Neon and the
same requests cost tens of milliseconds. See
[ADR-0003](../docs/adr/0003-single-region-now-multi-region-later.md) and
[`multi-region.md`](./multi-region.md).

The `304` row is also why `GET /games/v1/rooms/{key}` supports `If-None-Match`:
every player polls every two seconds whether or not anything changed, and an
unchanged room should not cost a full read.

## Pooled and direct connections

Neon gives two connection strings for the same database. Use the right one:

- **Pooled** (hostname contains `-pooler`) — `DATABASE_URL`, the application's
  normal traffic. Cloud Run opens a connection per instance and this is what
  keeps the total inside Neon's limit.
- **Direct** (no `-pooler`) — `DATABASE_URL_UNPOOLED`, for migrations, dumps and
  `LISTEN`/`NOTIFY`.

The pooled endpoint is PgBouncer in transaction mode and does not keep session
state. A migration run over it fails in ways that never mention pooling, so the
deploy workflow uses the direct endpoint for that one step.

Both strings need the same two edits before this app can use them: change the
scheme to `postgresql+asyncpg://` and remove the query parameters. See the
`asyncpg` trap in `CLAUDE.md`.

These are **not** `Settings` fields and must not be added to `app/core/config.py` — the app never reads them.

---

## Build-time vs runtime

Every variable here is **runtime**. The container image contains no configuration and no secret. Cloud Run supplies the values at deploy. Two consequences:

- Changing a value never needs a rebuild. Redeploy, or update the service.
- No secret is baked into a layer, so `docker history` reveals nothing.

This app has **no** build-time public configuration. There is no equivalent of a `NEXT_PUBLIC_*` value inlined at build.

---

## Secrets: current state and the hardening path

**Today**, `deploy.yml` passes secrets with `--set-env-vars`:

```bash
--set-env-vars "SECRET_KEY=${{ secrets.SECRET_KEY }}"
```

This works, and it stores the value in the Cloud Run revision. Anyone with `roles/run.viewer` can read it:

```bash
gcloud run services describe anuvia --region us-central1 --format export
```

**The hardening path** is Secret Manager. Store the secret once, grant the runtime service account access, and reference it by name at deploy:

```bash
# Store it
echo -n "the-secret-value" | gcloud secrets create SECRET_KEY --data-file=-

# Grant the Cloud Run runtime service account read access
gcloud secrets add-iam-policy-binding SECRET_KEY \
  --member "serviceAccount:YOUR_RUNTIME_SA" --role roles/secretmanager.secretAccessor

# Reference it at deploy — the value is not stored in the revision
gcloud run deploy anuvia --set-secrets "SECRET_KEY=SECRET_KEY:latest" ...
```

With `--set-secrets`, the revision holds a reference, not the value, and access is audit-logged. Migrate `SECRET_KEY`, `DATABASE_URL`, and the Stripe secrets this way before real users.

---

## Adding a variable — four places, one pull request

Missing any step breaks somebody.

1. **`.env.example`** — add it with a comment: purpose, valid values, default, whether production requires it.
2. **`app/core/config.py`** — add the typed field to `Settings`. Give it a safe default, or no default if it must be present.
3. **`deploy.yml`** — add a `--set-env-vars` line, or a `--set-secrets` reference if it is a secret. Add the matching GitHub variable or secret.
4. **This file** — add a row to the table above, and a note in the secrets section if it is sensitive.

Then add the value where it runs: `.env` locally, and the GitHub variable or secret for production. Tests and CI use dummy values, so add it to `tests/conftest.py` or `ci.yml` only if the code path under test reads it.

---

## Reading a variable in code

Always through `settings`:

```python
from app.core.config import settings

if settings.is_production:
    ...
timeout = settings.ACCESS_TOKEN_EXPIRE_MINUTES
```

Never `os.getenv` outside `config.py`. It is untyped, unvalidated, and invisible to the startup check.
