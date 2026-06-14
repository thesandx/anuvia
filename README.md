# Anuvia — FastAPI SaaS Starter

A **modular monolith** FastAPI template built for solo founders who want to ship multiple AI/SaaS products fast. Every new product is a folder. No microservice complexity — just a clean, deployable base that grows with you.

[![CI](https://github.com/YOUR_USERNAME/anuvia/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/anuvia/actions/workflows/ci.yml)

> **Open source note:** This repository contains no secrets. All sensitive values (`SECRET_KEY`, `DATABASE_URL`, API keys) are injected at runtime via environment variables — locally through `.env` (git-ignored), in CI/CD through GitHub Secrets.

---

## Why this architecture?

| Approach | Problem |
|---|---|
| Microservices | Too much ops overhead before you have revenue |
| Clean / Hexagonal Architecture | Too many layers for a one-person team |
| **Modular Monolith** (this) | Ships fast, scales when needed, easy to split later |

Every product lives in `app/apps/<name>/`. Adding a new one means creating one folder and four files. You never touch `main.py` or any other module.

---

## Tech Stack

| Layer | Library | Version |
|---|---|---|
| Web framework | FastAPI | 0.115.6 |
| ASGI server | Uvicorn | 0.32.1 |
| ORM | SQLAlchemy (async) | 2.0.36 |
| Migrations | Alembic | 1.14.0 |
| Validation | Pydantic v2 | 2.10.3 |
| Config | pydantic-settings | 2.6.1 |
| Auth | python-jose (JWT) + bcrypt | 3.3.0 / 4.2.1 |
| Local DB | aiosqlite (SQLite) | 0.20.0 |
| Production DB | Neon (PostgreSQL serverless) | — |
| HTTP client | httpx | 0.28.1 |
| Linter | Ruff | 0.8.4 |
| Testing | pytest + pytest-asyncio | 8.3.4 / 0.24.0 |
| Deploy | Docker + Google Cloud Run | — |

---

## Project Structure

```
anuvia/
│
├── .github/
│   └── workflows/
│       ├── ci.yml           # Lint + test on every push and PR
│       └── deploy.yml       # Build + deploy to Cloud Run on push to main
│
├── app/
│   ├── main.py              # FastAPI app entry point
│   │
│   ├── core/
│   │   ├── config.py        # Pydantic Settings — reads all config from env vars
│   │   ├── database.py      # Async SQLAlchemy engine + get_db dependency
│   │   ├── security.py      # bcrypt hashing + JWT create/decode
│   │   ├── dependencies.py  # get_current_user FastAPI dependency
│   │   ├── logging.py       # Structured stdout logging
│   │   └── router_loader.py # Auto-discovers and registers app routers
│   │
│   ├── models/
│   │   ├── base.py          # DeclarativeBase + TimestampMixin (created_at, updated_at)
│   │   └── user.py          # User table — shared across all apps
│   │
│   ├── schemas/
│   │   ├── common.py        # HealthResponse, MessageResponse
│   │   └── user.py          # UserCreate, UserUpdate, UserResponse
│   │
│   ├── repositories/
│   │   └── user_repository.py  # All User DB queries in one place
│   │
│   ├── utils/
│   │   └── helpers.py       # Shared utilities
│   │
│   └── apps/                # ← One folder per product
│       ├── auth/            # POST /auth/register, /login  GET /auth/me
│       ├── payments/        # GET /payments/subscription
│       └── ai_chat/         # POST /ai-chat/chat
│           (each has router.py, service.py, models.py, schemas.py)
│
├── alembic/                 # Database migrations
│   ├── env.py               # Async-aware migration runner
│   └── versions/            # Generated migration files
│
├── tests/
│   ├── conftest.py          # In-memory SQLite fixture + async test client
│   └── test_auth.py         # Auth flow tests
│
├── .env.example             # Safe template — copy to .env, fill in real values
├── .gitignore               # .env, *.db, .venv, caches all excluded
├── Dockerfile               # Cloud Run ready (runs migrations on startup)
├── alembic.ini
├── pyproject.toml           # Ruff + pytest config
└── requirements.txt
```

---

## Security Model

### What is never committed to git

| File / Pattern | Why excluded |
|---|---|
| `.env` | Contains your real `SECRET_KEY`, `DATABASE_URL`, API keys |
| `*.db` | Local SQLite database — contains real data |
| `.venv/` | Local Python environment |

The `.env.example` file is committed — it contains only placeholder values and documents which variables are needed.

### How secrets flow

```
Local dev:   .env file (git-ignored)  →  pydantic-settings  →  app
CI tests:    GitHub Actions env block  →  pydantic-settings  →  app (dummy values, in-memory DB)
Production:  GitHub Secrets            →  Cloud Run env vars  →  pydantic-settings  →  app
```

No secret ever touches the git repository.

---

## Local Development Setup

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/anuvia.git
cd anuvia

# Copy the template — this is the only file you need to fill in
cp .env.example .env
```

Edit `.env` with your real values:

```bash
# Generate a strong secret key
python -c "import secrets; print(secrets.token_hex(32))"
```

Minimum required in `.env`:

```
SECRET_KEY=<output from command above>
DATABASE_URL=sqlite+aiosqlite:///./local.db   # works out of the box
```

### 2. Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

alembic upgrade head          # apply migrations
uvicorn app.main:app --reload # start server
```

Open `http://localhost:8000/docs` for the interactive API explorer.

### 3. Test

```bash
pytest tests/ -v
```

Tests use an in-memory SQLite database — `.env` is not required to run them.

---

## Setting Up Neon (Production Database)

**Why not Turso?** Turso uses a custom `libsql` dialect for SQLAlchemy that is sync-only. This project uses `create_async_engine` throughout, which requires an async-capable database driver. Neon is a free serverless PostgreSQL provider with first-class async SQLAlchemy support via `asyncpg`.

> Local development still uses SQLite (`aiosqlite`) — no change there. Only the production `DATABASE_URL` differs.

### 1. Create a free Neon account

Go to [https://neon.tech](https://neon.tech) and sign up. It's free, no credit card required.

### 2. Create a project and database

In the Neon dashboard:
1. Click **New Project**
2. Give it a name (e.g. `anuvia`)
3. Choose your region (pick the one closest to your Cloud Run region)
4. Click **Create Project**

Neon creates a default database called `neondb` automatically.

### 3. Get your connection string

In the project dashboard, click **Connection Details** (or the **Connect** button). Make sure to:
- Set **Connection type** to `Connection string`
- Set **Driver** to `SQLAlchemy (asyncpg)`

It outputs a string in this format:

```
postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
```

> **Important — Neon may give you a plain `postgresql://` string instead of `postgresql+asyncpg://`.** If the dashboard does not offer an asyncpg-specific option, or gives you a URL starting with `postgresql://`, you must manually adjust it before use:
>
> 1. Change `postgresql://` → `postgresql+asyncpg://`
> 2. Remove the `?sslmode=require` (and any other query params like `channel_binding=require`) — asyncpg does not accept these; SSL is already enforced in `database.py` via `connect_args`.
>
> **Before (what Neon gives you):**
> ```
> postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
> ```
>
> **After (what you actually use):**
> ```
> postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb
> ```

### 4. Save it in your .env

```bash
DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb
```

### 5. Run migrations against Neon

```bash
alembic upgrade head
```

Alembic connects to Neon and creates all tables. The SSL connection is handled automatically — `database.py` detects the `postgresql` prefix and adds `ssl=require` to the connection args.

### Where to put the DATABASE_URL

| Environment | Where to set it |
|---|---|
| Local dev | `.env` file (already git-ignored) — use the SQLite default |
| GitHub CI | Not needed — CI uses in-memory SQLite |
| Production | GitHub Secret named `DATABASE_URL` → injected into Cloud Run at deploy |

---

## Environment Variables Reference

All variables are read from the environment by `app/core/config.py` using Pydantic Settings. In local dev they come from `.env`. In production they come from Cloud Run's environment.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | **Yes** | — | JWT signing key. Min 32 chars. Generate with `secrets.token_hex(32)`. |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./local.db` | SQLAlchemy async connection string |
| `APP_NAME` | No | `anuvia` | Displayed in API docs |
| `APP_ENV` | No | `development` | Set to `production` to disable `/docs` |
| `DEBUG` | No | `false` | Enables SQLAlchemy query logging |
| `ALGORITHM` | No | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | JWT lifetime in minutes |
| `STRIPE_SECRET_KEY` | No | — | Stripe payments integration |
| `STRIPE_WEBHOOK_SECRET` | No | — | Stripe webhook signature verification |

### Database URL formats

```bash
# Local development (SQLite, no setup needed)
DATABASE_URL=sqlite+aiosqlite:///./local.db

# Production (Neon — serverless PostgreSQL, see "Setting Up Neon" section for how to build this string)
DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb
```

---

## GitHub Actions CI/CD

### How it works

| Trigger | Workflow | What it does |
|---|---|---|
| Every push, every PR | `ci.yml` | Runs Ruff lint + format check + pytest |
| Push to `main` | `deploy.yml` | Builds Docker image, pushes to GCR, deploys to Cloud Run |

CI uses dummy secrets (hardcoded in the workflow file) and an in-memory database — it never needs real credentials.

The deploy workflow reads real secrets from GitHub and injects them as Cloud Run environment variables at deploy time.

### Setting up GitHub Secrets and Variables

Go to your repository → **Settings** → **Secrets and variables** → **Actions**.

#### Variables (non-sensitive — visible in logs)

Click **Variables** → **New repository variable**:

| Name | Example value | Description |
|---|---|---|
| `GCP_PROJECT_ID` | `my-project-123` | Your Google Cloud project ID |
| `GCP_REGION` | `us-central1` | Cloud Run deployment region |
| `CLOUD_RUN_SERVICE` | `anuvia` | Name of the Cloud Run service |
| `APP_NAME` | `anuvia` | Application name passed to the container |

#### Secrets (sensitive — masked in logs, never visible after saving)

Click **Secrets** → **New repository secret**:

| Name | Description |
|---|---|
| `GCP_SA_KEY` | Service account JSON key for GCP deployment (see below) |
| `SECRET_KEY` | JWT signing secret — run `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | Your Neon connection string (copy from Neon dashboard → Connect → SQLAlchemy asyncpg) |
| `STRIPE_SECRET_KEY` | Your Stripe secret key (optional — leave empty if not using payments) |
| `STRIPE_WEBHOOK_SECRET` | Your Stripe webhook secret (optional) |

### Creating the GCP Service Account

The deploy workflow needs a GCP service account with permission to push Docker images and deploy to Cloud Run.

```bash
# Set your project
gcloud config set project YOUR_PROJECT_ID

# Create the service account
gcloud iam service-accounts create github-deployer \
  --display-name "GitHub Actions Deployer"

# Grant required roles
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member "serviceAccount:github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role "roles/run.admin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member "serviceAccount:github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role "roles/storage.admin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member "serviceAccount:github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role "roles/iam.serviceAccountUser"

# Download the JSON key
gcloud iam service-accounts keys create key.json \
  --iam-account github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Copy the entire contents of key.json → paste as the GCP_SA_KEY secret
cat key.json

# Delete the local key file — it's now in GitHub Secrets
rm key.json
```

> **Security note:** The service account key (`key.json`) must be deleted locally immediately after pasting into GitHub Secrets. It must never be committed to git. The `.gitignore` already excludes `*.json` credential files by extension pattern — but treat it as highly sensitive regardless.

### First deploy

Enable the required GCP APIs (one time):

```bash
gcloud services enable run.googleapis.com containerregistry.googleapis.com
```

Push to `main`. The deploy workflow runs automatically. After the first deploy succeeds, Cloud Run creates the service and returns a public URL.

---

## Local Docker Testing (Production-like)

Run the exact same Docker image locally but pointed at Neon — no SQLite, no dev shortcuts. This is the fastest way to catch environment issues before pushing to Cloud Run.

### 1. Copy the env template

```bash
cp .env.docker.example .env.docker
```

### 2. Fill in `.env.docker`

Open `.env.docker` and set real values:

```bash
APP_NAME=anuvia
APP_ENV=production
DEBUG=false
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb
```

> `.env.docker` is git-ignored. Never commit it. It contains real credentials.

### 3. Build the image

```bash
docker build -t anuvia .
```

### 4. Run it

```bash
docker run --env-file .env.docker -p 8080:8080 anuvia
```

This runs migrations against Neon first, then starts the server — identical to what Cloud Run does.

### 5. Test it

```
http://localhost:8080/health     → {"status": "ok", "app": "anuvia"}
http://localhost:8080/auth/register
http://localhost:8080/auth/login
```

> `/docs` will be `404` because `APP_ENV=production` disables it. Change to `APP_ENV=development` in `.env.docker` if you want the Swagger UI locally.

### Difference from normal local dev

| | Normal local dev | Local Docker |
|---|---|---|
| Command | `uvicorn app.main:app --reload` | `docker run --env-file .env.docker` |
| Database | SQLite (`.env` default) | Neon PostgreSQL |
| Hot reload | Yes | No (rebuild image to pick up changes) |
| Matches Cloud Run | No | Yes |
| Use when | Day-to-day coding | Debugging prod issues locally |

---

## Deployment: Manual (without GitHub Actions)

If you prefer to deploy manually without CI/CD:

```bash
# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud auth configure-docker

# Build and push
docker build -t gcr.io/YOUR_PROJECT_ID/anuvia:latest .
docker push gcr.io/YOUR_PROJECT_ID/anuvia:latest

# Deploy
gcloud run deploy anuvia \
  --image gcr.io/YOUR_PROJECT_ID/anuvia:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --set-env-vars "APP_ENV=production" \
  --set-env-vars "APP_NAME=anuvia" \
  --set-env-vars "SECRET_KEY=your-secret-key" \
  --set-env-vars "DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.neon.tech/neondb"
```

### Production checklist

- [ ] `APP_ENV=production` — disables `/docs` and `/redoc`
- [ ] `SECRET_KEY` is at least 32 random characters
- [ ] `DATABASE_URL` points to Neon PostgreSQL (not local SQLite)
- [ ] `GCP_SA_KEY` local file deleted after saving to GitHub Secrets
- [ ] CORS origins restricted to your actual frontend domain (in `app/main.py`)
- [ ] Cloud Run min-instances set to 1 if cold starts are unacceptable

---

## Core Concepts

### Auto-Router Discovery

`app/core/router_loader.py` scans every folder under `app/apps/` at startup and registers any `router.py` it finds. **You never edit `main.py`.**

Each `router.py` declares its own URL prefix and Swagger tag:

```python
# app/apps/my_product/router.py
from fastapi import APIRouter

router = APIRouter()
PREFIX = "/my-product"   # URL prefix — defaults to /<folder-name> if omitted
TAGS  = ["my-product"]   # Swagger group — defaults to [folder-name] if omitted

@router.get("/hello")
async def hello():
    return {"message": "hello"}
```

### Request Flow

```
HTTP Request
    ↓
Router   (app/apps/<name>/router.py)   — validates input shape, calls service
    ↓
Service  (app/apps/<name>/service.py)  — all business logic lives here
    ↓
Repository  (app/repositories/*.py)    — all DB queries live here
    ↓
Database    (app/core/database.py)     — SQLAlchemy async session
```

Routers never contain business logic. Services never write raw SQL. Repositories never know about HTTP.

### Authentication

**Flow:**
1. `POST /auth/register` → creates user, returns user object
2. `POST /auth/login` → verifies password, returns `access_token`
3. Protected endpoints use `Depends(get_current_user)` — FastAPI injects the authenticated `User`

**How to protect an endpoint:**

```python
from fastapi import Depends
from app.core.dependencies import get_current_user
from app.models.user import User

@router.get("/profile")
async def profile(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email}
```

**Token details:**
- Algorithm: `HS256`
- Payload: `{"sub": "<user_id>", "exp": <timestamp>}`
- Expiry: 30 minutes (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`)
- Passwords: hashed with bcrypt (cost factor 12, no plaintext ever stored)

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | No | Health check |
| `POST` | `/auth/register` | No | Create account |
| `POST` | `/auth/login` | No | Get JWT token |
| `GET` | `/auth/me` | Yes | Current user profile |
| `GET` | `/payments/subscription` | Yes | Get user's subscription |
| `POST` | `/ai-chat/chat` | Yes | Send a chat message |

Interactive docs at `http://localhost:8000/docs` (disabled in production).

---

## Adding a New Product

Example: adding an **AI Resume Builder**.

```bash
mkdir -p app/apps/resume_builder
touch app/apps/resume_builder/__init__.py
touch app/apps/resume_builder/router.py
touch app/apps/resume_builder/service.py
touch app/apps/resume_builder/models.py
touch app/apps/resume_builder/schemas.py
```

**`router.py`:**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from .service import ResumeService
from .schemas import ResumeRequest, ResumeResponse

router = APIRouter()
PREFIX = "/resume"
TAGS  = ["resume-builder"]

@router.post("/generate", response_model=ResumeResponse)
async def generate(
    body: ResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ResumeService(db).generate(current_user.id, body)
```

**`service.py`:**

```python
from sqlalchemy.ext.asyncio import AsyncSession

class ResumeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(self, user_id: int, body) -> dict:
        # call AI, save to DB, return result
        ...
```

Restart the server — the router is live at `/resume/generate`. No other files touched.

If your new app has its own DB tables, import the models in `alembic/env.py` and run:

```bash
alembic revision --autogenerate -m "add resume_builder tables"
alembic upgrade head
```

---

## Alembic Cheat Sheet

```bash
# Generate a migration after changing/adding a model
alembic revision --autogenerate -m "describe your change"

# Apply all pending migrations
alembic upgrade head

# Roll back one step
alembic downgrade -1

# See current state
alembic current

# See full history
alembic history
```

When you add a new model, import it in `alembic/env.py` so Alembic can detect it:

```python
import app.apps.my_product.models  # noqa: F401
```

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run a specific file
pytest tests/test_auth.py -v
```

Tests use an isolated in-memory SQLite database per test — no `.env` needed, no side effects.

To add tests for a new app:

```python
# tests/test_my_product.py
import pytest

@pytest.mark.asyncio
async def test_hello(client):
    response = await client.get("/my-product/hello")
    assert response.status_code == 200
```

---

## Code Quality

```bash
# Lint
ruff check .

# Format
ruff format .

# Auto-fix
ruff check . --fix
```

Install pre-commit to run Ruff automatically on every `git commit`:

```bash
pre-commit install
```

---

## Wiring Up an AI Model

In `app/apps/ai_chat/service.py`, replace the echo stub:

```python
import anthropic

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

async def chat(self, user_id: int, message: str, session_id: int | None) -> dict:
    session = await self.get_or_create_session(user_id, session_id)

    self.db.add(ChatMessage(session_id=session.id, role="user", content=message))

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": message}],
    )
    reply = response.content[0].text

    self.db.add(ChatMessage(session_id=session.id, role="assistant", content=reply))
    await self.db.commit()
    return {"session_id": session.id, "reply": reply}
```

Add `ANTHROPIC_API_KEY` to `.env.example`, `app/core/config.py`, and as a GitHub Secret.

---

## Contributing

1. Fork and clone
2. `cp .env.example .env` — fill in your own values
3. `pre-commit install` — enables auto-lint on commit
4. Make your changes in a feature branch
5. `pytest tests/ -v` must pass before opening a PR
6. Open a PR — CI runs automatically
