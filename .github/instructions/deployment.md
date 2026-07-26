# Deployment

Read this before you touch the `Dockerfile`, an environment variable, a migration, or anything Cloud Run reads.

---

## What deploys, and how

`git push` to `main` triggers `.github/workflows/deploy.yml`. It:

1. Authenticates to Google Cloud.
2. Builds the Docker image and tags it with the commit SHA and `latest`.
3. Pushes both tags to Google Container Registry.
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
CMD alembic upgrade head && \
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

**Load-bearing lines — keep them:**

- **`--host 0.0.0.0`.** A container that binds `localhost` is unreachable. This produces Cloud Run's least helpful error: "The user-provided container failed to start and listen on the port defined by the PORT environment variable."
- **`--port ${PORT}`.** Cloud Run injects `PORT` and overrides the default. Never hardcode a port.
- **`ENV PORT=8080`.** A default for local runs. Cloud Run replaces it at runtime.
- **`COPY requirements.txt` before `COPY . .`.** This caches the dependency layer. Reorder it and every code change reinstalls every package.

**The migration line is a known limitation.** `alembic upgrade head && uvicorn ...` runs the migration on every container start. It is fine for a single instance. It does not scale — see the next section.

---

## Migrations at deploy time (the scaling fix)

Running `alembic upgrade head` in the container `CMD` is the template's simple default. It has two problems at scale, and both matter before you add instances or regions:

1. **Concurrency.** When Cloud Run runs several instances, each runs the migration on boot. They race for the same locks. One wins; the others may error or start against a half-migrated schema.
2. **Coupling.** A failed migration keeps every instance from starting, so a bad migration is a full outage instead of a failed deploy step.

**The fix, before you scale out:** run the migration once, as a separate deploy step, and remove it from the container `CMD`.

- Run it as a Cloud Run **job** (not the service), or as a step in `deploy.yml` before the `gcloud run deploy` line, against the production database.
- Change the service `CMD` to `uvicorn app.main:app --host 0.0.0.0 --port ${PORT}` only.
- A migration must be **backward compatible** with the currently running revision, because during a rollout old and new instances run at the same time. Add a column before you read it in code; do not drop a column the old revision still writes.

This change is tracked in [ADR-0003](../../docs/adr/0003-single-region-now-multi-region-later.md) as a prerequisite for multi-region.

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

`deploy.yml` currently authenticates with a service account **key** stored as the `GCP_SA_KEY` secret:

```yaml
- uses: google-github-actions/auth@v2
  with:
    credentials_json: ${{ secrets.GCP_SA_KEY }}
```

A key is a long-lived bearer credential. If it leaks, it is valid until someone revokes it. The stronger pattern is **Workload Identity Federation**: GitHub presents a short-lived OIDC token, Google exchanges it for temporary credentials, and no key exists anywhere. The migration is documented in [`cloud/github-actions.md`](../../cloud/github-actions.md). Until then, treat `GCP_SA_KEY` as highly sensitive and rotate it if there is any doubt.

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
