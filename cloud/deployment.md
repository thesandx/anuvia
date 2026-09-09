# Deployment runbook

The operator's guide: one-time setup, deploying, verifying, and rolling back.

For the rules an assistant follows when changing the pipeline, see [`.github/instructions/deployment.md`](../.github/instructions/deployment.md).

---

## One-time Google Cloud setup

### 1. Enable the APIs

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com

# Create the Artifact Registry repo in the SAME region as Cloud Run (us-central1).
gcloud artifacts repositories create containers \
  --repository-format=docker \
  --location=us-central1 \
  --description="anuvia container images"
```

### 2. Create the deploy service account

```bash
gcloud iam service-accounts create github-deployer \
  --display-name "GitHub Actions Deployer"

for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member "serviceAccount:github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role "$ROLE"
done
```

### 3. Set up keyless auth (Workload Identity Federation)

The pipeline authenticates with no key. Create a workload identity pool and a provider bound to this repository, then let the pool impersonate the deployer service account. The full command set — pool, provider, binding, and the resulting `WIF_PROVIDER` value — is in [github-actions.md](./github-actions.md).

Do **not** create a service account key. A key is a long-lived bearer credential; federation replaces it with a short-lived token.

### 4. Set GitHub variables and secrets

Repository → Settings → Secrets and variables → Actions.

**Variables** (non-sensitive):

| Name                  | Example        |
| --------------------- | -------------- |
| `GCP_PROJECT_ID`      | `my-project-123` |
| `GCP_REGION`          | `us-central1`  |
| `CLOUD_RUN_SERVICE`   | `anuvia`       |
| `APP_NAME`            | `anuvia`       |
| `ARTIFACT_REPOSITORY` | `containers`   |

**Secrets** (sensitive):

| Name                    | Value                                              |
| ----------------------- | -------------------------------------------------- |
| `WIF_PROVIDER`          | `projects/NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `WIF_SERVICE_ACCOUNT`   | `github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com` |
| `SECRET_KEY`            | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL`          | Neon `postgresql+asyncpg://...` (see the README)   |
| `STRIPE_SECRET_KEY`     | Optional                                           |
| `STRIPE_WEBHOOK_SECRET` | Optional                                           |

There is no `GCP_SA_KEY` — the deploy is keyless. The two `WIF_*` values are not truly sensitive (a resource path and an SA email); they are kept as secrets to mirror the `nextjs-cloudrun-template`. Plain variables would also work if `deploy.yml` reads `${{ vars.WIF_* }}`.

### 5. Enable branch protection on `main`

Require a pull request and the passing gate checks — `Lint & Test`, `Docker image builds`, and `Analyze python` — before merge. The README "Branch Protection" section has the exact settings and the CLI command.

---

## Deploying

The deploy is automatic. Merging a pull request to `main` pushes to `main`, which triggers `deploy.yml`:

1. Authenticate to Google Cloud.
2. Build the image, tag it with the commit SHA and `latest`.
3. Push both tags.
4. Deploy the **SHA-tagged** image to Cloud Run.
5. Print the service URL.

You do not deploy by hand in normal operation. To deploy manually (first bring-up, or a pipeline outage):

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
IMAGE=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/containers/anuvia
docker build -t $IMAGE:$(git rev-parse HEAD) .
docker push $IMAGE:$(git rev-parse HEAD)

gcloud run deploy anuvia \
  --image $IMAGE:$(git rev-parse HEAD) \
  --region us-central1 --platform managed --allow-unauthenticated --port 8080 \
  --set-env-vars "APP_ENV=production,DEBUG=false,APP_NAME=anuvia" \
  --set-env-vars "SECRET_KEY=...,DATABASE_URL=postgresql+asyncpg://..."
```

Deploy the SHA tag, never `latest`. A revision pinned to a moving tag cannot be traced to a commit.

---

## Migrations

Migrations run **once per deploy**, as the `Run database migrations` step in `deploy.yml`, after the image is pushed and before `gcloud run deploy`. The container `CMD` starts Uvicorn only — it does not migrate.

The step runs `alembic upgrade head` inside the image being deployed, so the migration uses exactly the code and pinned dependencies of the new revision:

```bash
docker run --rm -e DATABASE_URL -e SECRET_KEY "$IMAGE:$SHA" alembic upgrade head
```

**What this means when you operate it:**

- A failed migration is a **failed deploy**, not an outage. The step fails before any traffic shifts, and the previous revision keeps serving.
- Every migration must be **backward compatible with the running revision**. It applies while the old revision is still serving, and old and new instances overlap during the rollout. Add a column before code reads it; never drop a column the old revision still writes. Split a rename into add → backfill → switch reads → drop, across two deploys.
- **A rollback does not undo a migration.** See "Rolling back" below.

To run a migration by hand against production (recovery, or a migration you want to apply out of band):

```bash
DATABASE_URL="postgresql+asyncpg://..." alembic upgrade head
```

Pull requests get an automatic check: `ci.yml`'s `Migrations (Neon branch)` job clones the production Neon branch and applies the pull request's migrations to it, so a migration that breaks against the real schema fails review rather than the deploy. See [ADR-0003](../docs/adr/0003-single-region-now-multi-region-later.md).

---

## Verifying a deploy

```bash
# The URL the deploy printed, or:
gcloud run services describe anuvia --region us-central1 --format 'value(status.url)'

curl https://YOUR_SERVICE_URL/health        # {"status":"ok","app":"anuvia"}
```

`/docs` returns 404 in production — that is correct (`APP_ENV=production`). Check the logs in Cloud Logging or:

```bash
gcloud run services logs read anuvia --region us-central1 --limit 50
```

---

## Rolling back

A rollback is a traffic shift to an earlier revision. No rebuild.

```bash
# List revisions, newest first
gcloud run revisions list --service anuvia --region us-central1

# Send all traffic to a known-good revision
gcloud run services update-traffic anuvia \
  --region us-central1 --to-revisions anuvia-00042-abc=100
```

Because each revision is tied to an immutable SHA-tagged image, you always know exactly which commit a revision runs.

**Caution with migrations:** rolling the app back does not roll back a migration. If the bad deploy included a schema change the old code cannot use, roll the migration back too (`alembic downgrade`), and only if the migration was written to be reversible. This is why migrations must be backward compatible.

---

## First deploy checklist

- [ ] APIs enabled.
- [ ] Artifact Registry repo (`containers`) created in the Cloud Run region.
- [ ] Deploy service account created with the three roles.
- [ ] Workload Identity Federation pool, provider, and binding created; no key exists.
- [ ] Variables (`ARTIFACT_REPOSITORY`, …) and secrets (`WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`, …) set in GitHub.
- [ ] `SECRET_KEY` is at least 32 random characters.
- [ ] `DATABASE_URL` is the Neon `postgresql+asyncpg://` form, no `sslmode` query parameter.
- [ ] Neon project is in the same geography as `GCP_REGION`.
- [ ] Branch protection on `main` requires `Lint & Test`, `Docker image builds`, and `Analyze python`.
- [ ] *(Optional)* `NEON_API_KEY` secret and `NEON_PROJECT_ID` variable set, so pull requests test migrations against a Neon branch.
- [ ] CORS origins restricted to your real frontend (before real users) — see [SECURITY.md](../SECURITY.md).
- [ ] A budget alert is set.
