# CLAUDE.md

**Read this file completely before you make any change.** It is the operating manual for AI coding assistants (Claude Code, Copilot, Cursor, ChatGPT) and new human contributors in this repository.

It exists because several things here look wrong but are correct. Several obvious "improvements" break the build or the deploy. [Traps](#traps--things-that-look-wrong-and-are-not) and [Never do this](#never-do-this) record them. Both sections come from real behaviour of this stack, not speculation.

---

## Table of contents

1. [What this is](#what-this-is)
2. [Verified state](#verified-state)
3. [Documentation map](#documentation-map)
4. [Commands](#commands)
5. [The ten rules](#the-ten-rules)
6. [Where files go](#where-files-go)
7. [Architecture in brief](#architecture-in-brief)
8. [The multi-region decision](#the-multi-region-decision)
9. [Traps — things that look wrong and are not](#traps--things-that-look-wrong-and-are-not)
10. [Never do this](#never-do-this)
11. [Task recipes](#task-recipes)
12. [Verification protocol](#verification-protocol)
13. [Dependency policy](#dependency-policy)
14. [Security invariants](#security-invariants)
15. [Decision log](#decision-log)

---

## What this is

A production **FastAPI** backend deployed to Google Cloud Run. It is a **modular monolith**: every product is a folder under `app/apps/`, and the app auto-discovers each folder's router at startup. One person can run it.

The stack is async end to end: FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2. Local development uses SQLite (`aiosqlite`). Production uses Neon, a serverless PostgreSQL provider, over `asyncpg`.

The purpose is that **the path to production already works**: a container that runs on Cloud Run, a pipeline that deploys it, and documentation that explains each decision. Adding a product must not degrade that path. Keep the reusable base clean.

If the repository still contains only `auth`, `payments`, and `ai_chat` with the echo stub in `ai_chat/service.py`, it is not customised yet.

---

## Verified state

These versions are pinned in `requirements.txt` and are known to work together. Do not assume a newer version works. See [Dependency policy](#dependency-policy).

|                     | Version    | Note                                             |
| ------------------- | ---------- | ------------------------------------------------ |
| Python              | `3.12`     | `target-version = "py312"`; Docker base `3.12-slim` |
| FastAPI             | `0.115.6`  | App Router style routers, auto-registered        |
| Uvicorn             | `0.32.1`   | `[standard]` extras                              |
| SQLAlchemy          | `2.0.36`   | Async engine, `Mapped[...]` typing               |
| Alembic             | `1.14.0`   | Async migration runner                           |
| Pydantic            | `2.10.3`   | v2 only; `[email]` for `EmailStr`                |
| pydantic-settings   | `2.6.1`    | The only place that reads the environment        |
| python-jose         | `3.3.0`    | JWT encode/decode                                |
| bcrypt              | `4.2.1`    | Password hashing                                 |
| asyncpg             | `0.30.0`   | Production PostgreSQL driver (Neon)              |
| aiosqlite           | `0.20.0`   | Local and test SQLite driver                     |
| Ruff                | `0.8.4`    | Lint **and** format — the only style tool        |
| pytest              | `8.3.4`    | `asyncio_mode = "auto"`                          |

**Measured facts:**

- The container runs `alembic upgrade head` and then starts Uvicorn on `$PORT`.
- `/health` returns `{"status": "ok", "app": "anuvia"}`.
- Tests run on in-memory SQLite. They need no `.env` and no network.
- `docs` and `redoc` are disabled when `APP_ENV=production`.

---

## Documentation map

This file is the index and the warnings. The detail lives in `.github/instructions/`, `docs/`, and `cloud/`. **Read the relevant one before you work in that area**, rather than reasoning from training defaults.

| Read this                                                                    | Before                                                        |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------- |
| [`.github/instructions/coding-rules.md`](./.github/instructions/coding-rules.md)         | Writing anything. The non-negotiables in full.    |
| [`.github/instructions/project-structure.md`](./.github/instructions/project-structure.md) | Creating any file — it decides where it goes.   |
| [`.github/instructions/coding-standards.md`](./.github/instructions/coding-standards.md) | Writing Python, an endpoint, a model, or a query.   |
| [`.github/instructions/architecture.md`](./.github/instructions/architecture.md)         | Adding a layer, a dependency, or a new app.       |
| [`.github/instructions/deployment.md`](./.github/instructions/deployment.md)             | Touching the `Dockerfile`, env vars, or migrations. |
| [`.github/instructions/github-workflows.md`](./.github/instructions/github-workflows.md) | Touching `.github/workflows/`.                    |
| [`docs/local-development.md`](./docs/local-development.md)                    | Setting up, or confused by the tooling.                       |
| [`docs/testing.md`](./docs/testing.md)                                       | Writing tests. Explains the async client fixture.             |
| [`docs/troubleshooting.md`](./docs/troubleshooting.md)                        | **Anything failing.** Symptom → cause → fix. Check here first.|
| [`docs/adr/`](./docs/adr/)                                                    | Asking "why is it done this way?"                             |
| [`cloud/deployment.md`](./cloud/deployment.md)                               | Deploying, rolling back, or setting up GCP.                   |
| [`cloud/multi-region.md`](./cloud/multi-region.md)                           | Thinking about regions, replicas, or global latency.         |
| [`cloud/environment-variables.md`](./cloud/environment-variables.md)         | Adding or changing configuration.                             |
| [`SECURITY.md`](./SECURITY.md)                                               | The security model and the pre-production hardening checklist.|

**Precedence when guidance conflicts** (later wins): your training defaults → general FastAPI/GCP docs → `.github/instructions/` → this file → an explicit instruction from the human you work with.

---

## Commands

```bash
# Setup (once)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Develop
uvicorn app.main:app --reload        # dev server, hot reload, SQLite
open http://localhost:8000/docs      # interactive API explorer

# The gate — run before you claim work is done
ruff check .                         # lint
ruff format --check .                # format check
pytest tests/ -v                     # tests (in-memory SQLite)

# Fix
ruff check . --fix                   # fix lint + import order
ruff format .                        # write formatting

# Database
alembic revision --autogenerate -m "describe change"   # create a migration
alembic upgrade head                 # apply migrations
alembic downgrade -1                 # roll back one step

# Run the real production image locally, against Neon
docker build -t anuvia . && docker run --env-file .env.docker -p 8080:8080 anuvia
```

The three gate commands (`ruff check`, `ruff format --check`, `pytest`) are exactly what CI runs in `.github/workflows/ci.yml`. Run them before you say work is complete.

---

## The ten rules

Full reasoning in [`coding-rules.md`](./.github/instructions/coding-rules.md).

1. **Never break the folder structure.** The layout is fixed: `app/core/`, `app/models/`, `app/schemas/`, `app/repositories/`, `app/apps/<name>/`, `app/utils/`. Do not invent `src/`, a root `services/`, or a second `helpers/`. A product goes in `app/apps/<name>/` and nowhere else.
2. **One app is one folder with four files.** `router.py`, `service.py`, `models.py`, `schemas.py`. Adding a product never edits `main.py` — the [auto-loader](#trap-1) finds the router.
3. **Respect the layers.** `router` → `service` → `repository` → database. A router holds no business logic. A service writes no raw SQL by hand where a repository fits. A repository knows nothing about HTTP. See [Architecture](#architecture-in-brief).
4. **Read configuration only through `app/core/config.py`.** Never call `os.environ` or `os.getenv` elsewhere. `Settings` validates every value once, at startup. A missing `SECRET_KEY` must fail loudly on boot, not silently at request time.
5. **Async all the way down.** Every route, service, and repository method is `async def`. Every DB call is `await`ed. Never call a blocking library (a sync DB driver, `requests`, `time.sleep`) inside a request — it stalls the event loop. Use `httpx.AsyncClient` and give every outbound call a timeout.
6. **Write production-quality code.** Handle the error path. Raise `HTTPException` with the right status at the boundary. No stub that returns fake data in a merged PR (the `ai_chat` echo is the one known, documented stub). No secret in source, a test, or a comment.
7. **Validate at the boundary with Pydantic.** Request bodies and responses are Pydantic models, never raw dicts. Set `response_model` on every route. Use `EmailStr` for email. Never return an ORM object the response schema did not shape.
8. **Migrations are the only way the schema changes.** Change a model, then `alembic revision --autogenerate`, then read the generated file before you commit it. Import every new model module in `alembic/env.py` or the migration will miss the table.
9. **Avoid unnecessary dependencies.** Check the standard library first (`secrets`, `hashlib`, `datetime`, `uuid`, `functools`). See [Dependency policy](#dependency-policy).
10. **Update docs when architecture or behaviour changes** — same PR, not later. A new env var touches four files (see [Task recipes](#add-an-environment-variable)). Write docs in short, plain, present-tense English.

---

## Where files go

| Writing                                    | Goes in                        |
| ------------------------------------------ | ------------------------------ |
| A product / feature (routes + logic)       | `app/apps/<name>/`             |
| An HTTP route for that product             | `app/apps/<name>/router.py`    |
| Business logic for that product            | `app/apps/<name>/service.py`   |
| A DB table for that product                | `app/apps/<name>/models.py`    |
| A request/response shape                   | `app/apps/<name>/schemas.py`   |
| A table shared across products (e.g. User) | `app/models/`                  |
| A schema shared across products            | `app/schemas/`                 |
| All DB queries for a shared model          | `app/repositories/`            |
| App-wide config, DB engine, security, deps | `app/core/`                    |
| A pure helper with no I/O                  | `app/utils/`                   |
| A test                                     | `tests/test_<subject>.py`      |
| A database migration                       | `alembic/versions/` (generated)|
| An explanation of how something works      | `docs/`                        |
| An explanation of the cloud setup          | `cloud/`                       |

**Naming:** modules `snake_case.py`; classes `PascalCase`; functions and variables `snake_case`; tests `test_<subject>.py`; app folders `snake_case` (this becomes the URL prefix by default).

**Imports are always absolute** from the `app` package — `from app.core.config import settings`, never `from ...core.config import settings`. Ruff's import sort (`I`) enforces ordering. Run `ruff check . --fix` rather than hand-sorting.

---

## Architecture in brief

Dependencies point **inward and downward**. A layer may import from layers below it, never above.

```
app/apps/<name>/router.py     HTTP: input shape, status codes, auth       ← composition
app/apps/<name>/service.py    business logic, transactions
app/repositories/*.py         database queries for shared models          ← data access
app/models/, app/apps/*/models.py   ORM tables
app/core/                     config, engine, security, logging           ← foundation
```

**Request flow:**

```
HTTP request
  → router   (app/apps/<name>/router.py)    validates the body, checks auth, calls the service
  → service  (app/apps/<name>/service.py)   business logic, owns the transaction
  → repository / model query                 reads and writes rows
  → database (app/core/database.py)          async SQLAlchemy session from get_db
```

**The auto-loader** (`app/core/router_loader.py`) scans `app/apps/*` at startup and registers each `router.py`. Each router sets its own `PREFIX` and `TAGS`. This is why you never edit `main.py` to add a product. See [Trap 1](#trap-1).

**Deployment:** `git push main` → GitHub Actions (`deploy.yml`) → build image → push to Google Container Registry → deploy to Cloud Run. The image tags with the commit SHA and Cloud Run deploys that immutable tag, so a rollback is a traffic shift, not a rebuild.

---

## The multi-region decision

This is a recorded decision, not an open question. Full reasoning and the runbook are in [ADR-0003](./docs/adr/0003-single-region-now-multi-region-later.md) and [`cloud/multi-region.md`](./cloud/multi-region.md). The short version, because contributors keep asking:

**1. Run single-region now. Co-locate the app and the database.** Deploy Cloud Run and the Neon project in the **same geography** (for example both in `us-east`). The dominant latency in this app is the round trip between the app and the database — a single request runs several queries. Put them far apart and every request pays that distance several times. Put them together and the problem disappears. This costs about **$0** on the Cloud Run and Neon free tiers.

**2. Keep PostgreSQL. Do not switch to SQLite or Turso.** The whole stack is async (`asyncpg`, `aiosqlite`). Turso's SQLAlchemy dialect is **sync-only**, so it does not fit `create_async_engine`. A local SQLite file on Cloud Run does not persist — the filesystem is ephemeral and instances do not share it — so app-local SQLite loses data. SQLite is correct for local development and tests, and wrong for a multi-instance production server. This is settled in [ADR-0002](./docs/adr/0002-use-neon-postgres-for-persistence.md).

**3. "Multi-region based on app type" means read/write splitting, not a database per app.** Do not shard the schema by product. When you genuinely need multiple regions, classify each app by its consistency need:

| App                     | Access pattern                        | Region strategy                          |
| ----------------------- | ------------------------------------- | ---------------------------------------- |
| `payments`              | Rare writes, must be strongly consistent | Primary region only                    |
| `auth` (register/login) | Writes rare, correctness critical        | Write to primary                       |
| `auth` (token → user)   | Read on every authenticated request      | Local read replica, or cache the lookup |
| `ai_chat` (history)     | Read-heavy, append-only, tolerant of lag | Local read replica                     |

The pattern is **one write primary, read replicas near users**. Writes always go to the primary region. Reads that tolerate a little staleness go to a local replica.

**4. Go multi-region only when a real, distant user base has a real latency problem.** For a solo developer on a small budget, premature multi-region buys cost and complexity, not speed. The trigger to revisit is in the ADR: measured latency complaints from a region far from your primary, not a guess about future scale.

**Two things block a clean multi-region setup today. Fix them first** (both are [traps](#trap-6) and are tracked in the ADR):

- Migrations run in the container start command. With many instances or many regions, they race on boot. Move migrations to a single deploy-time step.
- The app hits the database on every authenticated request to load the user. Across regions this is the exact call you must serve locally or cache.

---

## Traps — things that look wrong and are not

Every item here reflects real behaviour of this stack. Do not "fix" any of them without reading the reason.

### Trap 1

**`main.py` never imports the app routers. That is correct.** `app/core/router_loader.py` scans `app/apps/*` at startup and registers every `router.py` it finds. Adding `app.include_router(...)` by hand in `main.py` duplicates the route and is the wrong way to add a product. Add a folder; the loader finds it.

### Trap 2

**A new model that Alembic "ignores" is almost always a missing import.** Alembic only sees models that are imported by the time `alembic/env.py` builds `target_metadata`. `env.py` imports each models module explicitly (`import app.apps.ai_chat.models  # noqa: F401`). Add a new app with tables, add its import line, or `--autogenerate` produces an empty migration.

### Trap 3

**The Neon connection string is not the one the dashboard gives you.** Neon hands out `postgresql://...?sslmode=require&channel_binding=require`. This app needs two edits: change the scheme to `postgresql+asyncpg://`, and remove the query parameters. `asyncpg` rejects `sslmode` and `channel_binding`; SSL is set in code instead — `app/core/database.py` adds `connect_args={"ssl": "require"}` when the URL starts with `postgresql`. The full walkthrough is in the README "Setting Up Neon" section.

### Trap 4

**SQLite gets no `connect_args`; PostgreSQL requires them.** `app/core/database.py` branches on the URL scheme: `{"ssl": "require"}` for `postgresql`, `{}` for SQLite. `aiosqlite` does not accept an `ssl` argument and errors if you pass one. Do not "simplify" this to a single unconditional `connect_args`.

### Trap 5

**Tests set `DATABASE_URL` before importing the app, on purpose.** `tests/conftest.py` writes `os.environ["DATABASE_URL"]` at the top of the file, above the imports. `pydantic-settings` reads the environment when `Settings()` is constructed at import time. Move that line below the imports and the tests pick up your real `.env` database instead of in-memory SQLite.

### Trap 6

**Migrations run in the container `CMD`, and that does not scale.** The `Dockerfile` runs `alembic upgrade head && uvicorn ...`. It works for one instance. With Cloud Run running several instances, or with more than one region, every instance runs migrations on boot and they race. This is a real limitation, documented in [ADR-0003](./docs/adr/0003-single-region-now-multi-region-later.md). Before you scale out, move migrations to a single deploy-time step (see [`cloud/deployment.md`](./cloud/deployment.md)). Do not "fix" it by adding retries.

### Trap 7

**`/docs` disappears in production, and that is deliberate.** `main.py` sets `docs_url` and `redoc_url` to `None` when `APP_ENV=production`. A 404 on `/docs` in production is the design, not a broken deploy. Set `APP_ENV=development` locally to see the explorer.

### Trap 8

**The `ai_chat` reply is an echo stub, and it is the one allowed stub.** `app/apps/ai_chat/service.py` returns `f"Echo: {message}"`. It is a documented placeholder for the model call, marked in the code and in the README ("Wiring Up an AI Model"). Every other service must be real. Do not copy the stub pattern into a new app.

### Trap 9

**CORS is wide open (`allow_origins=["*"]`), which is fine for local and wrong for production.** `main.py` allows every origin with credentials. Browsers reject `*` combined with credentials, and it is not a safe production setting. Restrict `allow_origins` to your real frontend domain before you ship. This is on the [hardening checklist](./SECURITY.md).

---

## Never do this

Violations here are defects, not style disagreements.

| Never                                                        | Why                                                                                                              |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Push or commit directly to `main`                           | A push to `main` deploys to production. Every change goes through a reviewed pull request.                       |
| Call `os.environ` / `os.getenv` outside `app/core/config.py`| Untyped, unvalidated, and it bypasses startup validation. Add the field to `Settings`.                          |
| Add a route by editing `main.py`                            | The auto-loader registers routers. A manual `include_router` duplicates the route. See [Trap 1](#trap-1).       |
| Put business logic in a router                              | Routers validate and delegate. Logic lives in a service, queries in a repository.                               |
| Call a blocking library inside a request                    | It stalls the event loop for every user. Use async clients and `await`.                                         |
| Edit a model without a migration                            | The database drifts from the code. Always `alembic revision --autogenerate` and read the result.                |
| Commit a service account key, or a `.env`, or a `*.db`      | `.gitignore` excludes them. A key is a permanent bearer credential.                                              |
| Write a raw SQL string with an f-string user value          | SQL injection. Use SQLAlchemy expressions and bound parameters.                                                 |
| Return an ORM model directly where a schema is expected     | Leaks columns (like `hashed_password`). Shape it through a Pydantic `response_model`.                            |
| Log a token, a password, or a full connection string        | Use `app/core/logging.py`. Never log a secret.                                                                  |
| Deploy application-local SQLite to Cloud Run                | The filesystem is ephemeral and unshared. Data is lost on the next instance. See [the multi-region decision](#the-multi-region-decision). |
| Disable a CI check to make a PR green                       | Fix the code, or change the check deliberately and say why.                                                     |
| Claim work is done without running the gate                 | See [Verification protocol](#verification-protocol).                                                            |

---

## Task recipes

### Add a new product (app)

1. Create the folder and its four files:
   ```bash
   mkdir -p app/apps/<name>
   touch app/apps/<name>/{__init__.py,router.py,service.py,models.py,schemas.py}
   ```
2. In `router.py`, define `router = APIRouter()`, set `PREFIX = "/<name>"` and `TAGS = ["<name>"]`, and delegate each route to a service method.
3. Put all logic in `service.py`. Keep the router thin.
4. If the app has tables, define them in `models.py`, then add `import app.apps.<name>.models  # noqa: F401` to `alembic/env.py`.
5. Generate and apply the migration: `alembic revision --autogenerate -m "add <name> tables"` then `alembic upgrade head`.
6. Add `tests/test_<name>.py`.
7. Restart the server. The route is live. You did not touch `main.py`.

### Add an environment variable — four places, one PR

Missing any step breaks somebody:

1. `.env.example` — document the purpose, valid values, default, and whether production requires it.
2. `app/core/config.py` — add the typed field to `Settings` and validate it.
3. `.github/workflows/deploy.yml` — add a `--set-env-vars` line (or a Secret Manager reference for a secret).
4. `cloud/environment-variables.md` — note it if an operator needs the context.

### Add a database table

1. Define the model in the right `models.py` (shared → `app/models/`, product-specific → `app/apps/<name>/models.py`).
2. Ensure `alembic/env.py` imports the module.
3. `alembic revision --autogenerate -m "..."`, then **read the generated file** — autogenerate misses some changes (renames, server defaults).
4. `alembic upgrade head` locally. Add or update a test.

### Wire a real AI model into `ai_chat`

1. Replace the echo line in `app/apps/ai_chat/service.py` with a real client call.
2. Add the API key as a `Settings` field, in `.env.example`, and as a deploy secret (four places, above).
3. Use the async client and a timeout. Handle the provider error path — never let a provider 500 become an unhandled 500.

### Change the Dockerfile

1. Read [`.github/instructions/deployment.md`](./.github/instructions/deployment.md) first.
2. Keep: `$PORT` honoured, `--host 0.0.0.0`, the migration step (until it moves to deploy-time — see [Trap 6](#trap-6)).
3. **Verify**: `docker build -t anuvia . && docker run --env-file .env.docker -p 8080:8080 anuvia`, then `curl localhost:8080/health`.

---

## Verification protocol

**Never describe unverified work as working.** If a check fails, report the failure with its output.

Minimum, always:

```bash
ruff check .
ruff format --check .
pytest tests/ -v
```

If you touched the `Dockerfile`, `requirements.txt`, migrations, or the env model:

```bash
docker build -t anuvia .
docker run --env-file .env.docker -p 8080:8080 anuvia
curl localhost:8080/health          # must return {"status":"ok","app":"anuvia"}
```

If you touched a migration, confirm it applies **and** rolls back:

```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

If you touched a workflow: YAML that parses is not a workflow that runs. The gate is `ci.yml` (job `Lint & Test` plus `Docker image builds`) and `codeql.yml` (job `Analyze python`); `deploy.yml` only runs on `main`. All three gate checks must be green before a merge — see [`.github/instructions/github-workflows.md`](./.github/instructions/github-workflows.md).

---

## Dependency policy

Before adding anything, answer: can the standard library do it? Can it be ~50 lines in `app/utils/`? Is it maintained? Does it fit the async model?

**Prefer the platform:** `secrets` (tokens), `hashlib`/`hmac`, `datetime` with `UTC`, `uuid`, `functools.lru_cache`, `pathlib`. FastAPI already bundles Starlette and Pydantic — do not add a second validation or routing library.

**Never add:** a sync HTTP library (`requests`) — use the bundled `httpx.AsyncClient`; a second ORM or query builder; a settings library other than the `pydantic-settings` already in use; a background-task framework before there is a background task.

**Pin every version** in `requirements.txt`, exact (`==`), never a range. A range makes two machines build two different apps. When you add a package, pin it and commit the change with the reason in the PR.

**Upgrade philosophy:** `latest` on PyPI is not the same as _supported here_. These versions are pinned together because they work together. Upgrade deliberately, one thing at a time, and run the full gate plus a Docker build after.

---

## Security invariants

Full model and the hardening checklist are in [`SECURITY.md`](./SECURITY.md).

- **No secret lives in the repository.** `.env`, `.env.docker`, and `*.db` are git-ignored. Secrets reach the app only through the environment: `.env` locally, GitHub Secrets → Cloud Run in production.
- **Configuration is validated once, centrally.** Only `app/core/config.py` reads the environment. A missing required secret fails at startup.
- **Passwords are bcrypt-hashed. Tokens are short-lived JWTs.** No plaintext password is ever stored or logged. `hashed_password` never leaves the server — response schemas exclude it.
- **The current pipeline uses a service account key and passes secrets as env vars.** Both work and both are weaker than the target. The hardening path — Workload Identity Federation instead of a key, Secret Manager instead of `--set-env-vars` — is in [`SECURITY.md`](./SECURITY.md) and [`cloud/github-actions.md`](./cloud/github-actions.md). Do not add a new long-lived credential.
- **CORS is open in the template and must be closed before production.** See [Trap 9](#trap-9).

---

## Decision log

Recorded in [`docs/adr/`](./docs/adr/). Read before proposing a change to any of them.

| ADR                                                                   | Decision                                                        |
| --------------------------------------------------------------------- | -------------------------------------------------------------- |
| [0001](./docs/adr/0001-use-cloud-run-for-hosting.md)                  | Cloud Run for hosting — over a VM, GKE, or a PaaS               |
| [0002](./docs/adr/0002-use-neon-postgres-for-persistence.md)          | Neon PostgreSQL for production; SQLite for local and tests     |
| [0003](./docs/adr/0003-single-region-now-multi-region-later.md)       | Single region now; a defined path to multi-region when needed  |

Add an ADR when a decision is expensive to reverse, affects how everyone works, or rejects an obvious alternative. Never edit an accepted ADR to change its decision — write a new one that supersedes it, and link both ways.

---

## If a rule here blocks you

Say so explicitly and propose a change to the rule. Do not silently work around it, and do not disable a check to force a green result. If you change how something works, update the relevant document in the same change. A stale rulebook is worse than none: assistants follow it with confidence and produce confidently wrong code.
