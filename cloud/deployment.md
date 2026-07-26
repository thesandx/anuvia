# Deployment runbook

The operator's guide: one-time setup, deploying, verifying, and rolling back.

For the rules an assistant follows when changing the pipeline, see [`.github/instructions/deployment.md`](../.github/instructions/deployment.md).

---

## One-time Google Cloud setup

### 1. Enable the APIs

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com containerregistry.googleapis.com
```

### 2. Create the deploy service account

```bash
gcloud iam service-accounts create github-deployer \
  --display-name "GitHub Actions Deployer"

for ROLE in roles/run.admin roles/storage.admin roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member "serviceAccount:github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role "$ROLE"
done
```

### 3. Create the key (current pipeline) — or federate (hardening path)

The current pipeline uses a key. Create it, store it in GitHub, and delete the local copy:

```bash
gcloud iam service-accounts keys create key.json \
  --iam-account github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com
# Paste the contents of key.json as the GitHub secret GCP_SA_KEY, then:
rm key.json
```

The stronger option is Workload Identity Federation — no key at all. See [github-actions.md](./github-actions.md). Adopt it when you can; until then, treat `GCP_SA_KEY` as highly sensitive.

### 4. Set GitHub variables and secrets

Repository → Settings → Secrets and variables → Actions.

**Variables** (non-sensitive):

| Name                | Example        |
| ------------------- | -------------- |
| `GCP_PROJECT_ID`    | `my-project-123` |
| `GCP_REGION`        | `us-central1`  |
| `CLOUD_RUN_SERVICE` | `anuvia`       |
| `APP_NAME`          | `anuvia`       |

**Secrets** (sensitive):

| Name                    | Value                                              |
| ----------------------- | -------------------------------------------------- |
| `GCP_SA_KEY`            | Contents of `key.json`                             |
| `SECRET_KEY`            | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL`          | Neon `postgresql+asyncpg://...` (see the README)   |
| `STRIPE_SECRET_KEY`     | Optional                                           |
| `STRIPE_WEBHOOK_SECRET` | Optional                                           |

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
gcloud auth configure-docker
docker build -t gcr.io/YOUR_PROJECT_ID/anuvia:$(git rev-parse HEAD) .
docker push gcr.io/YOUR_PROJECT_ID/anuvia:$(git rev-parse HEAD)

gcloud run deploy anuvia \
  --image gcr.io/YOUR_PROJECT_ID/anuvia:$(git rev-parse HEAD) \
  --region us-central1 --platform managed --allow-unauthenticated --port 8080 \
  --set-env-vars "APP_ENV=production,DEBUG=false,APP_NAME=anuvia" \
  --set-env-vars "SECRET_KEY=...,DATABASE_URL=postgresql+asyncpg://..."
```

Deploy the SHA tag, never `latest`. A revision pinned to a moving tag cannot be traced to a commit.

---

## Migrations

Today the container runs `alembic upgrade head` on start. For a single instance this is fine. **Before you run more than one instance or add a region, move the migration to a deploy-time step** and remove it from the container `CMD`:

```bash
# Run once against the production database, before deploying the new revision
DATABASE_URL="postgresql+asyncpg://..." alembic upgrade head
```

Then deploy a service whose `CMD` only starts Uvicorn. Every migration must be backward compatible with the running revision, because old and new instances overlap during the rollout. See [ADR-0003](../docs/adr/0003-single-region-now-multi-region-later.md).

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
- [ ] Deploy service account created with the three roles.
- [ ] `GCP_SA_KEY` stored; local `key.json` deleted.
- [ ] Variables and secrets set in GitHub.
- [ ] `SECRET_KEY` is at least 32 random characters.
- [ ] `DATABASE_URL` is the Neon `postgresql+asyncpg://` form, no `sslmode` query parameter.
- [ ] Neon project is in the same geography as `GCP_REGION`.
- [ ] Branch protection on `main` requires `Lint & Test`, `Docker image builds`, and `Analyze python`.
- [ ] CORS origins restricted to your real frontend (before real users) — see [SECURITY.md](../SECURITY.md).
- [ ] A budget alert is set.
