# Project structure

This document decides where a new file goes. Read it before you create one.

---

## The tree

```
anuvia/
├── app/
│   ├── main.py                 # FastAPI app. CORS, lifespan, /health. Never add routes here.
│   ├── core/                   # Foundation — imported by everything, imports nothing above it
│   │   ├── config.py           # Settings (pydantic-settings). The only reader of the environment.
│   │   ├── database.py         # Async engine, SessionLocal, Base, get_db dependency.
│   │   ├── security.py         # bcrypt hashing, JWT create/decode.
│   │   ├── dependencies.py     # get_current_user and other FastAPI dependencies.
│   │   ├── logging.py          # setup_logging, get_logger.
│   │   └── router_loader.py    # Auto-discovers app/apps/*/router.py at startup.
│   ├── models/                 # Shared ORM tables
│   │   ├── base.py             # Base + TimestampMixin (created_at, updated_at).
│   │   └── user.py             # User table. Shared by every app.
│   ├── schemas/                # Shared Pydantic shapes
│   │   ├── common.py           # HealthResponse, MessageResponse.
│   │   └── user.py             # UserCreate, UserUpdate, UserResponse.
│   ├── repositories/           # Queries for shared models
│   │   └── user_repository.py  # Every User query in one place.
│   ├── utils/                  # Pure helpers, no I/O
│   │   └── helpers.py
│   └── apps/                   # One folder per product
│       ├── auth/               # register, login, me
│       ├── payments/           # subscription
│       └── ai_chat/            # chat
├── alembic/                    # Migrations
│   ├── env.py                  # Async runner. Imports every model module.
│   └── versions/               # Generated migration files.
├── tests/
│   ├── conftest.py             # In-memory SQLite + async test client.
│   └── test_*.py
├── .github/workflows/          # ci.yml, codeql.yml, deploy.yml
├── docs/                       # How things work; ADRs.
├── cloud/                      # Google Cloud setup and runbooks.
├── Dockerfile
├── alembic.ini
├── pyproject.toml              # Ruff + pytest config.
└── requirements.txt
```

---

## Where a new file goes

| Writing                                        | Put it in                       |
| ---------------------------------------------- | ------------------------------- |
| A new product with its own routes              | `app/apps/<name>/`              |
| An HTTP route                                  | `app/apps/<name>/router.py`     |
| Business logic for a product                   | `app/apps/<name>/service.py`    |
| A table used by one product                    | `app/apps/<name>/models.py`     |
| A request or response shape for one product    | `app/apps/<name>/schemas.py`    |
| A table used by two or more products           | `app/models/`                   |
| A schema used by two or more products          | `app/schemas/`                  |
| Queries for a shared model                     | `app/repositories/`             |
| Config, DB engine, security, a dependency      | `app/core/`                     |
| A pure helper with no I/O                       | `app/utils/`                    |
| A test                                         | `tests/test_<subject>.py`       |
| A migration                                    | `alembic/versions/` (generated) |
| An explanation of how something works          | `docs/`                         |
| Cloud setup or an operator runbook             | `cloud/`                        |

---

## The four files of an app

Every folder in `app/apps/` has the same shape. Keep it, even when a file is nearly empty — a consistent shape is what makes the codebase navigable.

| File         | Holds                                                                 |
| ------------ | -------------------------------------------------------------------- |
| `router.py`  | `router`, `PREFIX`, `TAGS`, and one thin route per endpoint.         |
| `service.py` | The business logic. One class, methods that take the session.        |
| `models.py`  | The SQLAlchemy tables for this app. Empty with a note if it has none. |
| `schemas.py` | The Pydantic request and response models.                            |

`auth/models.py` is the example of an intentionally empty models file — auth reuses the shared `User`. It keeps a comment that says so.

---

## Repository vs service query

Both a repository and a service can issue a query. The rule for which:

- A query for a **shared model** (User) goes in `app/repositories/`, so every app reuses it.
- A query for a **product-local model** (ChatSession, Subscription) can live in that product's `service.py`. It is not shared, so a repository adds indirection with no reuse.

When a product-local query grows complex or gets reused within the app, extract it. Do not create a repository speculatively.

---

## Naming

- Modules: `snake_case.py`.
- Classes: `PascalCase` (`AuthService`, `UserRepository`, `ChatSession`).
- Functions and variables: `snake_case`.
- App folders: `snake_case` — the folder name becomes the default URL prefix and tag.
- Tests: `test_<subject>.py`, functions `test_<behaviour>`.

---

## Imports

Imports are always absolute from the `app` package.

```python
# Yes
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.models.user import User

# No — relative walk-ups
from ...core.config import settings
```

Ruff's import rules (`I` in `pyproject.toml`) sort and group imports. Run `ruff check . --fix` rather than sorting by hand.

Within an app, importing its own siblings with a leading dot is acceptable and matches the existing code:

```python
# Inside app/apps/auth/router.py
from .schemas import LoginRequest
from .service import AuthService
```
