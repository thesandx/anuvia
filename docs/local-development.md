# Local development

How to set up, run, and work on anuvia on your own machine.

---

## Prerequisites

- Python 3.12.
- `git`.
- Docker (only for the production-like local run — not for day-to-day coding).

You do **not** need PostgreSQL, Neon, or any cloud account to develop locally. Local development runs on SQLite with no setup.

---

## First-time setup

```bash
git clone https://github.com/YOUR_USERNAME/anuvia.git
cd anuvia

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` and set a real `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the output as `SECRET_KEY`. Leave `DATABASE_URL` as the SQLite default. That is the minimum to run.

Apply the migrations, then start the server:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the interactive API explorer.

---

## The daily loop

```bash
source .venv/bin/activate
uvicorn app.main:app --reload      # hot reload on save
```

- Edit code. Uvicorn reloads on save.
- Add or change a model → generate a migration (see below) → `alembic upgrade head`.
- Before you open a pull request, run the gate:

```bash
ruff check .
ruff format --check .
pytest tests/ -v
```

Fix issues automatically where you can:

```bash
ruff check . --fix
ruff format .
```

Install the pre-commit hook so Ruff runs on every commit:

```bash
pre-commit install
```

---

## Working with the database

Local development uses SQLite in a file, `local.db`, git-ignored.

```bash
# After changing or adding a model
alembic revision --autogenerate -m "describe your change"

# Read the generated file in alembic/versions/ before committing it.

alembic upgrade head        # apply
alembic downgrade -1        # roll back one step
alembic current             # show the current revision
alembic history             # show the full history
```

When you add a new app with its own tables, add its models import to `alembic/env.py`:

```python
import app.apps.my_product.models  # noqa: F401
```

Without that import, autogenerate produces an empty migration and the table never appears. See [troubleshooting.md](./troubleshooting.md).

To start over locally, delete the SQLite file and re-migrate:

```bash
rm -f local.db
alembic upgrade head
```

---

## Two ways to run locally

| | Normal dev | Production-like Docker |
| --- | --- | --- |
| Command | `uvicorn app.main:app --reload` | `docker run --env-file .env.docker -p 8080:8080 anuvia` |
| Database | SQLite (`local.db`) | Neon PostgreSQL |
| Hot reload | Yes | No — rebuild after changes |
| `/docs` | Enabled | Enabled (if `APP_ENV=development`) |
| Matches Cloud Run | No | Yes — same image, same database |
| Use for | Day-to-day coding | Reproducing a production issue |

### The production-like Docker run

Use this to catch problems the SQLite path hides: a PostgreSQL-only migration error, a driver difference, a startup failure.

```bash
cp .env.docker.example .env.docker      # git-ignored; fill in real values
# set SECRET_KEY and a real Neon DATABASE_URL
docker build -t anuvia .
docker run --env-file .env.docker -p 8080:8080 anuvia
```

This runs the migration against Neon, then starts the server — the exact sequence Cloud Run runs. Every request and every SQL query prints to the terminal when `DEBUG=true`.

---

## Environment variables

Every variable is read by `app/core/config.py`. The full reference is in the README and in [`cloud/environment-variables.md`](../cloud/environment-variables.md). The two you must set:

- `SECRET_KEY` — required, no default. Generate it as shown above.
- `DATABASE_URL` — defaults to SQLite. Change it only for the production-like run.

---

## Adding a Neon database (only when you need PostgreSQL locally)

You need Neon only to run the production-like Docker path or to test a PostgreSQL-specific change. The full walkthrough — including the connection-string edit that trips everyone up — is in the README "Setting Up Neon" section. The one rule to remember: change `postgresql://` to `postgresql+asyncpg://` and remove the `?sslmode=...` query parameters. See [troubleshooting.md](./troubleshooting.md).

---

## Common setup problems

Full symptom-to-fix table is in [troubleshooting.md](./troubleshooting.md). The two most common:

- **`pytest` picks up my real database.** It should not — `tests/conftest.py` forces in-memory SQLite. If it does, something imports the app before `conftest.py` sets the environment. Do not move that line.
- **`alembic upgrade head` fails on a fresh clone.** Confirm `.env` exists and `SECRET_KEY` is set. `alembic/env.py` imports `Settings`, which fails without it.
