# Coding rules

The non-negotiables. Everything else in this folder elaborates on these.

---

## 1. Never break the folder structure

The layout is fixed. Every file has exactly one correct home.

- **Do not invent new top-level folders.** No `src/`, no root `services/`, no second `helpers/` next to `app/utils/`.
- A product lives in `app/apps/<name>/` and nowhere else. It has four files: `router.py`, `service.py`, `models.py`, `schemas.py`.
- Shared code lives in `app/core/` (config, engine, security), `app/models/` (shared tables), `app/schemas/` (shared shapes), `app/repositories/` (queries for shared models), `app/utils/` (pure helpers).
- If something does not fit, that is a signal to discuss the architecture — not to create a folder. Propose it, and update `project-structure.md` in the same pull request if it is accepted.

**Why:** the modular monolith works because the shape is predictable. When the layout drifts, the auto-loader assumptions and every assistant's assumptions break at once.

---

## 2. One app is one folder

Adding a product is a mechanical act:

- Create `app/apps/<name>/` with the four files.
- The router sets `router`, `PREFIX`, and `TAGS`. The auto-loader (`app/core/router_loader.py`) registers it at startup.
- **Never edit `main.py` to add a route.** A manual `include_router` duplicates the route and defeats the loader.

**Why:** the loader is the reason a new product does not touch shared code. Bypass it and you reintroduce the merge conflicts it removes.

---

## 3. Respect the layers

Data flows in one direction: `router` → `service` → `repository`/model → database.

- A **router** validates the request shape, checks authentication, sets the status code and `response_model`, and calls one service method. It holds no business logic.
- A **service** holds the business logic and owns the transaction. It calls repositories or issues SQLAlchemy queries. It raises `HTTPException` for domain errors.
- A **repository** holds the database queries for a shared model. It knows nothing about HTTP.

A layer imports downward only. A repository never imports a router. A model never imports a service.

**Why:** the boundaries make a service testable without HTTP and a query reusable across products.

---

## 4. Read configuration only through `app/core/config.py`

- Never call `os.environ` or `os.getenv` anywhere else.
- Add every new variable as a typed field on `Settings`, with a default where a default is safe.
- A required secret with no default (like `SECRET_KEY`) makes the app fail at startup if it is missing. That is correct — fail loud on boot, not silent at request time.

**Why:** one validated, typed source of configuration. Scattered `os.getenv` calls are untyped, unvalidated, and impossible to audit.

---

## 5. Async all the way down

- Every route, service method, and repository method is `async def`.
- Every database call is `await`ed on the async session.
- **Never call a blocking library inside a request.** No `requests`, no synchronous DB driver, no `time.sleep`, no blocking file or subprocess call. Any of them stalls the event loop for every concurrent user.
- Use `httpx.AsyncClient` for outbound HTTP. Give every outbound call an explicit timeout.

**Why:** one blocking call in an async server does not slow one request — it freezes the whole process until it returns.

---

## 6. Write production-quality code

Assume this code runs in production tonight, for real users.

- Handle the error path. Raise `HTTPException` with the correct status at the boundary. Do not let a library exception become an unhandled 500.
- No stub that returns fake data in a merged pull request. The one exception is the documented `ai_chat` echo, which is a marked placeholder for the model call.
- No secret in source — not in a comment, not in a test fixture, not "temporarily".
- Validate input at trust boundaries: request bodies, third-party responses, webhook payloads.

---

## 7. Validate at the boundary with Pydantic

- Request bodies are Pydantic models, never raw dicts pulled from the request.
- Every route sets `response_model`. The response is shaped by a schema, never a raw ORM object.
- Use the right field types: `EmailStr` for email, constrained types for bounded values.
- A schema that reads from an ORM object sets `model_config = {"from_attributes": True}`.

**Why:** the response schema is the contract and the filter. Returning a raw `User` leaks `hashed_password`. The schema is what keeps it server-side.

---

## 8. Migrations are the only way the schema changes

- Change a model, then generate a migration: `alembic revision --autogenerate -m "..."`.
- **Read the generated file before you commit it.** Autogenerate misses renames, some server defaults, and constraint changes. Fix it by hand where it is wrong.
- Import every new model module in `alembic/env.py`, or autogenerate produces an empty migration and the table never appears.
- A migration must apply and roll back. Test both.

**Why:** the migration history is the schema's source of truth. A model change with no migration is a production outage waiting for the next deploy.

---

## 9. Avoid unnecessary dependencies

Every dependency is permanent attack surface, install time, and an upgrade obligation.

Before adding one, answer all of these:

1. Can the standard library do it? (`secrets`, `hashlib`, `hmac`, `datetime`, `uuid`, `functools`, `pathlib`)
2. Can it be done in under ~50 lines in `app/utils/`?
3. Is it maintained — releases in the last 6 months, no critical advisories?
4. Does it fit the async model? A sync-only library that blocks the event loop is not a fit.

Adding one anyway? Pin it exactly in `requirements.txt` and say why in the pull request.

**Never add:** `requests` (use the bundled `httpx.AsyncClient`); a second ORM or validation library; a settings library other than `pydantic-settings`.

---

## 10. Always update documentation when architecture or behaviour changes

Same pull request. Not "later". This covers a change in **how something works**, not only a change in structure.

| If you change...                     | Update...                                                                  |
| ------------------------------------ | -------------------------------------------------------------------------- |
| Folder layout                        | `project-structure.md`, README structure section                           |
| Layers, data flow, a new dependency  | `architecture.md`, `cloud/architecture.md`                                 |
| `Dockerfile`, runtime configuration  | `deployment.md`, `cloud/deployment.md`, README                             |
| A workflow in `.github/workflows/`   | `github-workflows.md`, README                                              |
| Any environment variable             | `.env.example` **and** `app/core/config.py` **and** `cloud/environment-variables.md` |
| How an endpoint or module behaves    | its docstring, the README endpoint table, and any guide that describes it  |
| A convention or a rule               | the relevant file in this folder                                           |

---

## 11. Verify before you claim

- Run the gate before you say the work is done: `ruff check .`, `ruff format --check .`, `pytest tests/ -v`.
- If a check fails, report the failure with its output. Do not describe unverified work as working.
- Changed the Dockerfile or dependencies? Build the image and run the container. `docker build -t anuvia .` then `docker run --env-file .env.docker -p 8080:8080 anuvia`, then `curl localhost:8080/health`.
- Changed a migration? Confirm it applies and rolls back.

---

## 12. Write documentation in short, plain, present-tense English

Every Markdown document in the repository follows the same style — `CLAUDE.md`, this folder, `docs/`, `cloud/`, the ADRs, and the README.

- **Keep sentences short.** At most 20 words for an instruction, 25 for a description. One idea per sentence.
- **One instruction per sentence.** Split a compound step into separate sentences or list items.
- **Use the active voice and the present tense.** "The container runs the migration", not "the migration is run by the container".
- **Use one term per concept.** Do not call the same thing an "app" here and a "module" there.
- **Start a procedure step with the command verb.** "Run `pytest`", not "You should now run `pytest`".
- **Write for a non-native reader.** Choose the plain word over the clever one. Avoid idiom and long noun clusters.
- **Bring a document into compliance when you edit it.** Improve the file you touch, in the same pull request.

**Why:** many readers — human and machine — parse these documents as instructions. Simple, unambiguous English lowers the chance a reader acts on a sentence in a way the author did not mean.

---

## Quick reference

| Do                                        | Don't                                            |
| ----------------------------------------- | ------------------------------------------------ |
| One product per folder in `app/apps/`     | A route added by editing `main.py`               |
| `async def` and `await` every DB call     | A blocking library inside a request              |
| `from app.core.config import settings`    | `os.getenv(...)` scattered in the code           |
| `response_model=` on every route          | Returning a raw ORM object                       |
| A migration for every model change        | Editing a model with no migration                |
| Standard library first                    | A dependency that wraps a standard-library API   |
| `logger.info(...)` via `app/core/logging` | Logging a token or a password                    |
| Update docs in the same pull request      | "I'll document it later"                         |
| Open a pull request for every change      | Push or commit straight to `main`                |
