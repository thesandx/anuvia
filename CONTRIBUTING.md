# Contributing

How to make a change to anuvia. This is the human quickstart. The full rulebook is in [`CLAUDE.md`](./CLAUDE.md) and [`.github/instructions/`](./.github/instructions/) — read those before a non-trivial change.

---

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # set SECRET_KEY; leave DATABASE_URL as SQLite
pre-commit install                   # Ruff runs on every commit
alembic upgrade head
uvicorn app.main:app --reload
```

Full detail: [`docs/local-development.md`](./docs/local-development.md).

---

## The workflow

1. **Branch.** Never commit to `main` — a push to `main` deploys to production.
   ```bash
   git checkout -b feat/my-change
   ```
2. **Make the change**, following the rules below.
3. **Run the gate**:
   ```bash
   ruff check .
   ruff format --check .
   pytest tests/ -v
   ```
4. **Update the docs** in the same change if you changed architecture or behaviour.
5. **Push and open a pull request.** CI runs the gate. It must be green before merge.
6. **Merge.** The push to `main` deploys automatically.

---

## The rules in one screen

- **One product, one folder** in `app/apps/`. It has `router.py`, `service.py`, `models.py`, `schemas.py`. Adding a product never edits `main.py`.
- **Layers:** router validates and delegates → service holds logic → repository or model holds queries. No logic in a router. No HTTP in a repository.
- **Everything is `async`.** Never call a blocking library inside a request.
- **Config only through `app/core/config.py`.** Never `os.getenv` elsewhere.
- **Schema changes only through Alembic.** Change a model → `alembic revision --autogenerate` → read the file → `alembic upgrade head`. Import a new model module in `alembic/env.py`.
- **Every route sets `response_model`.** Never return a raw ORM object.
- **No secret in source.** Not in a comment, a test, or "temporarily".
- **Pin every dependency** exactly in `requirements.txt`, and say why in the pull request.

---

## Adding a product

```bash
mkdir -p app/apps/my_product
touch app/apps/my_product/{__init__.py,router.py,service.py,models.py,schemas.py}
```

- `router.py`: define `router`, `PREFIX`, `TAGS`, and thin routes.
- `service.py`: the logic, in a class that takes the session.
- If it has tables: define them in `models.py`, add the import to `alembic/env.py`, generate a migration.
- Add `tests/test_my_product.py`.

Restart the server. The route is live. See the README "Adding a New Product" for a full example.

---

## Adding an environment variable

Four places, one pull request: `.env.example`, `app/core/config.py`, `deploy.yml`, and [`cloud/environment-variables.md`](./cloud/environment-variables.md). Detail in [`CLAUDE.md`](./CLAUDE.md#task-recipes).

---

## Commit and pull request style

- **Commits:** short imperative subject (`add resume_builder app`), body explains why if it is not obvious.
- **Pull requests:** say what changed and why. Note any architectural decision and the alternative you rejected. If the change is expensive to reverse, add an ADR in [`docs/adr/`](./docs/adr/).
- **Never** disable a CI check to make a pull request green. Fix the code, or change the check deliberately and explain why.

---

## Before you ask "why is it done this way?"

Check [`CLAUDE.md`](./CLAUDE.md) — especially the "Traps" section — and [`docs/adr/`](./docs/adr/). Several things that look wrong are correct and are documented there.
