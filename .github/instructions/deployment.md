# Deployment

Read this before you touch the `Dockerfile`, an environment variable, a migration, or anything Cloud Run reads.

---

## What deploys, and how

`git push` to `main` triggers `.github/workflows/deploy.yml`. It:

1. Authenticates to Google Cloud.
2. Builds the Docker image and tags it with the commit SHA and `latest`.
3. Pushes both tags to Artifact Registry.
4. Deploys the **SHA-tagged** image to Cloud Run with the runtime environment variables.
5. Prints the service URL.

Cloud Run deploys the immutable SHA tag, so a rollback is a traffic shift to an earlier revision, not a rebuild. See [`cloud/deployment.md`](../../cloud/deployment.md) for the operator runbook.

---

## The Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

**Load-bearing lines — keep them:**

- **`--host 0.0.0.0`.** A container that binds `localhost` is unreachable. This produces Cloud Run's least helpful error: "The user-provided container failed to start and listen on the port defined by the PORT environment variable."
- **`--port ${PORT}`.** Cloud Run injects `PORT` and overrides the default. Never hardcode a port.
- **`ENV PORT=8080`.** A default for local runs. Cloud Run replaces it at runtime.
- **`COPY requirements.txt` before `COPY . .`.** This caches the dependency layer. Reorder it and every code change reinstalls every package.

- **`exec`.** It replaces the shell, so Uvicorn runs as PID 1 and receives Cloud Run's `SIGTERM` directly. Without it the shell holds PID 1, swallows the signal, and the instance is killed instead of shutting down gracefully.

**There is deliberately no migration in the `CMD`.** Do not add one back — see the next section.

---

## Migrations at deploy time

Migrations run **once**, in the `Run database migrations` step of `deploy.yml`, between the image push and `gcloud run deploy`:

```yaml
- name: Run database migrations
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
    SECRET_KEY: ${{ secrets.SECRET_KEY }}
  run: |
    docker run --rm -e DATABASE_URL -e SECRET_KEY \
      $IMAGE:${{ github.sha }} \
      alembic upgrade head
```

Why it is not in the container `CMD` — the two reasons, both of which bite before you add instances or regions:

1. **Concurrency.** When Cloud Run runs several instances, each would run the migration on boot. They race for the same locks. One wins; the others may error or start against a half-migrated schema.
2. **Coupling.** A failed migration would keep every instance from starting, so a bad migration becomes a full outage instead of a failed deploy step.

Why it runs **inside the image being deployed** rather than on the runner: the migration then executes with exactly the code and pinned dependencies of the new revision. A separate `pip install` on the runner can drift from what ships.

Two rules this buys you, and one obligation:

- The migration fails **before** any traffic moves, so a broken migration is a red deploy, not an outage.
- The step is separately visible in the deploy log, with its own pass or fail.
- **The obligation:** a migration must be **backward compatible** with the currently running revision. It lands while the *old* revision is still serving, and old and new instances overlap during the rollout. Add a column before you read it in code; do not drop a column the old revision still writes. Split a rename into add → backfill → switch reads → drop, across two deploys.

This closes prerequisite 1 of [ADR-0003](../../docs/adr/0003-single-region-now-multi-region-later.md).

**Local consequence:** running the image no longer creates the schema. Migrate first:

```bash
docker run --rm --env-file .env.docker anuvia alembic upgrade head
docker run --env-file .env.docker -p 8080:8080 anuvia
```

---

## Environment variables at deploy

`deploy.yml` passes runtime configuration with `--set-env-vars`. The current set:

| Variable                | Source                | Note                                             |
| ----------------------- | --------------------- | ------------------------------------------------ |
| `APP_NAME`              | GitHub variable       | Shown in the API docs                            |
| `APP_ENV`              | Hardcoded `production` | Disables `/docs` and `/redoc`                    |
| `DEBUG`                 | Hardcoded `false`      | Turns off SQL echo                               |
| `SECRET_KEY`            | GitHub secret          | JWT signing key                                  |
| `DATABASE_URL`          | GitHub secret          | Neon `postgresql+asyncpg://...`                  |
| `STRIPE_SECRET_KEY`     | GitHub secret          | Optional                                         |
| `STRIPE_WEBHOOK_SECRET` | GitHub secret          | Optional                                         |

**A secret passed with `--set-env-vars` is stored in the revision's metadata.** Anyone with `roles/run.viewer` can read it with `gcloud run services describe`. It works, and it is weaker than the target. The hardening path is **Secret Manager** with `--set-secrets`, which mounts the secret by reference and keeps the value out of the revision. See [`cloud/environment-variables.md`](../../cloud/environment-variables.md).

**To add a variable**, follow the four-place recipe in [`coding-rules.md`](./coding-rules.md#10-always-update-documentation-when-architecture-or-behaviour-changes): `.env.example`, `app/core/config.py`, `deploy.yml`, and `cloud/environment-variables.md`.

---

## Authentication to Google Cloud

`deploy.yml` authenticates with **Workload Identity Federation** — no key:

```yaml
permissions:
  id-token: write            # lets GitHub mint the OIDC token
- uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
    service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}
```

GitHub presents a short-lived OIDC token bound to this repository, Google exchanges it for temporary credentials, and no key exists anywhere. The `id-token: write` permission is on the deploy job only. Do not reintroduce a service account key — the setup is documented in [`cloud/github-actions.md`](../../cloud/github-actions.md).

---

## Verifying a deploy locally

The honest test of a deploy change is the real image against the real database.

```bash
cp .env.docker.example .env.docker      # fill in real values; it is git-ignored
docker build -t anuvia .
docker run --env-file .env.docker -p 8080:8080 anuvia
curl localhost:8080/health              # {"status":"ok","app":"anuvia"}
```

This runs the migration and starts the server exactly as Cloud Run does. If it fails here, it fails in production.

---

## Cloud Run settings that matter

- **`--allow-unauthenticated`.** The API is public. Authentication happens in the app (JWT), not at the Cloud Run edge. Correct for a public API.
- **`--port 8080`.** Matches the container's default and `HOSTNAME`.
- **Min instances.** Default is 0 (scale to zero, no idle cost, but cold starts). Set `--min-instances=1` (~$10–15/month) only if cold starts hurt.
- **Concurrency.** Cloud Run sends many requests to one instance. This is safe because the app is stateless and each request gets its own session. Do not add process-global mutable state.
- **Region.** Deploy to the region closest to your users **and** your database. See [`cloud/multi-region.md`](../../cloud/multi-region.md).
