# Architecture

Read this before you add a layer, a dependency, or a new app.

---

## The shape: a modular monolith

Anuvia is one deployable process. Inside it, every product is a self-contained folder. This is deliberate — it ships as fast as a monolith and splits as cleanly as microservices, without the operational cost of many services.

- **One process, one image, one Cloud Run service.** Not many services, not a mesh.
- **One product, one folder** in `app/apps/`. A product does not import another product. Shared needs move down into `app/core/`, `app/models/`, or `app/schemas/`.
- **The base is reusable.** `app/core/` and the shared models are the template. Products are what you add. Keep the base clean so the next product starts from a known-good floor.

---

## The layers

Dependencies point inward and downward. A layer imports from layers below it, never above.

```
┌─────────────────────────────────────────────┐
│ app/apps/<name>/router.py                     │  HTTP boundary: validate, auth, status, response_model
├─────────────────────────────────────────────┤
│ app/apps/<name>/service.py                    │  Business logic, owns the transaction
├─────────────────────────────────────────────┤
│ app/repositories/  +  product model queries   │  Data access
├─────────────────────────────────────────────┤
│ app/models/  +  app/apps/<name>/models.py      │  ORM tables
├─────────────────────────────────────────────┤
│ app/core/  (config, database, security, deps)  │  Foundation — imports nothing above
└─────────────────────────────────────────────┘
```

**Allowed:** router → service → repository → model → core.
**Forbidden:** a service that reads the request object; a repository that raises about HTTP status; a model that imports a service; `app/core/` that imports an app.

---

## Request lifecycle

```
1. Request arrives          Uvicorn → FastAPI → the matched router
2. Dependencies resolve     get_db opens an async session; get_current_user decodes the JWT and loads the user
3. Body validates           Pydantic parses and validates the request model; a bad body is a 422 before your code runs
4. Router delegates         the route calls one service method and returns its result
5. Service runs logic       reads and writes through the session, commits once
6. Response shapes          FastAPI serialises the return value through response_model
7. Session closes           get_db's context manager closes the session
```

Every step above the service is framework machinery. Your code lives in the service and the query.

---

## The auto-loader

`app/core/router_loader.py` is the reason a product does not touch shared code.

At startup it scans `app/apps/*`. For each folder it imports `router.py` and reads three names:

- `router` — the `APIRouter` (required; the folder is skipped with a warning if it is missing).
- `PREFIX` — the URL prefix (optional; defaults to `/<folder-name>`).
- `TAGS` — the Swagger group (optional; defaults to `[folder-name]`).

It then calls `app.include_router(router, prefix=PREFIX, tags=TAGS)`.

**Consequence:** you add a product by adding a folder. You never edit `main.py`. Registering a router by hand in `main.py` duplicates the route.

---

## Configuration flow

```
Environment  →  app/core/config.py (Settings)  →  the rest of the app
```

- `Settings` (pydantic-settings) reads the environment once, validates it, and exposes typed fields.
- Locally, values come from `.env`. In production, they come from Cloud Run's environment.
- **Nothing else reads the environment.** A new variable is a new field on `Settings`, full stop.

---

## Database flow

```
app/core/config.py (DATABASE_URL)
  → app/core/database.py (create_async_engine, SessionLocal)
    → get_db yields an AsyncSession
      → services and repositories use the session
```

- The engine is created once at import. `SessionLocal` is the session factory.
- `get_db` is a FastAPI dependency that yields a session and closes it after the request.
- The URL scheme selects the driver: `sqlite+aiosqlite` locally, `postgresql+asyncpg` in production. `database.py` adds SSL args only for PostgreSQL.

---

## Authentication flow

```
POST /auth/register  → hash the password (bcrypt) → insert the user
POST /auth/login     → verify the password → return a signed JWT (HS256, sub = user id, 30-minute expiry)
Protected route      → get_current_user decodes the JWT → loads the user → injects it
```

- The token carries only the user id and an expiry. The server looks up the user on every protected request.
- That per-request lookup is cheap in one region and becomes the key call to optimise across regions. See [`cloud/multi-region.md`](../../cloud/multi-region.md).

---

## Adding a layer or crossing a boundary

Most changes need no new layer. Before you add one, check:

- **A new query for User?** Add a method to `UserRepository`. Not a new layer.
- **A new product?** A folder in `app/apps/`. Not a new layer.
- **A cross-cutting concern** (rate limiting, a request id, metrics)? A FastAPI dependency or middleware in `app/core/`, wired in `main.py`. Discuss it in the pull request.
- **An external system** (Stripe, an email provider, a model API)? Call it from the product's `service.py` with `httpx.AsyncClient` and a timeout. If two products need it, extract a small client into `app/core/` or a shared module and record the reason.

Adding a genuinely new layer (a caching tier, a message queue, a read-replica router) is an architectural decision. Write an ADR.

---

## What this architecture is not

- **Not microservices.** One image, one deploy. Splitting a product into its own service is a future option the folder boundary keeps cheap — not the current shape.
- **Not hexagonal / clean architecture.** No ports-and-adapters ceremony. The layers here are the minimum that keeps logic testable, not a full dependency-inversion framework.
- **Not multi-region today.** It is single-region by design, with a defined path to multi-region when a real user base needs it. See [ADR-0003](../../docs/adr/0003-single-region-now-multi-region-later.md).
