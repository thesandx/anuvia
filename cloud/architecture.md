# Cloud architecture

The target architecture for anuvia on Google Cloud, the service boundaries, and the scaling and cost model.

---

## The picture

```
        ┌──────────────┐   git push main   ┌──────────────────┐
        │  Developer   │ ────────────────▶ │  GitHub Actions   │
        └──────────────┘                   │  (deploy.yml)     │
                                           └────────┬──────────┘
                                        build image │ push + deploy
                                           ┌────────▼──────────┐
                                           │ Artifact Registry  │
                                           └────────┬──────────┘
                                           deploy   │ SHA tag
                                           ┌────────▼──────────┐        TLS, autoscale
   users ───── HTTPS ──────────────────▶  │     Cloud Run      │  ◀──── scale to zero
                                           │  (anuvia service)  │
                                           └────────┬──────────┘
                                        asyncpg      │  logs (stdout)
                              ┌──────────────────────┼───────────────┐
                     ┌────────▼────────┐    ┌────────▼────────┐
                     │  Neon Postgres  │    │  Cloud Logging  │
                     │  (same region)  │    └─────────────────┘
                     └─────────────────┘
```

---

## The components

| Component          | Responsibility                                                          |
| ------------------ | ----------------------------------------------------------------------- |
| **Cloud Run**      | Runs the container, ends TLS, autoscales from zero, injects `PORT`.      |
| **Artifact Registry** | Stores each built image by SHA tag (regional, in the Cloud Run region). |
| **GitHub Actions** | Builds, pushes, and deploys on push to `main`.                          |
| **Neon**           | The production database. Serverless PostgreSQL, same geography as Cloud Run. |
| **Cloud Logging**  | Collects the container's stdout/stderr automatically.                   |
| **Secret Manager** | The hardening target for secrets (not yet wired). See [environment-variables.md](./environment-variables.md). |

---

## Service boundaries

- **Cloud Run is stateless.** It stores nothing between requests. Every piece of state lives in Neon. This is what lets it scale to zero and run many instances safely.
- **The database is external and regional.** It is the one stateful component. Its region choice drives the app's latency — see [multi-region.md](./multi-region.md).
- **Secrets are runtime, not build-time.** The image contains no credential. Configuration arrives from the environment at deploy. A rebuild is never needed to change a secret.

---

## Request path

```
user → Cloud Run (TLS terminates here) → Uvicorn → FastAPI
     → get_db opens an async session to Neon
     → the route runs, queries Neon, commits
     → response serialised through response_model → user
logs → stdout → Cloud Logging
```

---

## Scaling

- **Horizontal, automatic.** Cloud Run adds instances under load and removes them when idle, down to zero.
- **Concurrency per instance.** One instance handles many concurrent requests. The app is safe under this because it is stateless and each request gets its own database session. Do not add process-global mutable state.
- **The database is the scaling ceiling.** Cloud Run scales faster than a single database primary. Neon's connection pooler (PgBouncer) absorbs the connection churn. If instance count grows large, use Neon's pooled endpoint and consider read replicas for read-heavy paths — the same mechanism as [multi-region.md](./multi-region.md) step 3, applied for scale rather than geography.

---

## Cold starts

With min-instances 0, the first request after idle pays a cold start: the container boots, and Neon (also scaled to zero) wakes. This is acceptable for most low-traffic products. Where it is not, set `--min-instances=1` to keep one instance warm, at ~$10–15/month.

---

## Cost model

| Driver                       | How it bills                                    |
| ---------------------------- | ----------------------------------------------- |
| Cloud Run compute            | Per request-time (CPU + memory), plus per request |
| Cloud Run idle               | $0 at min-instances 0                            |
| Artifact Registry storage    | A few cents per GB per month                     |
| Cloud Logging                | Free within the monthly allotment               |
| Neon                         | Free tier at low traffic; paid tiers for replicas and more compute |

Set a budget alert before the first deploy:

```bash
gcloud billing budgets create \
  --billing-account=YOUR_BILLING_ACCOUNT \
  --display-name="anuvia budget" \
  --budget-amount=20USD
```

---

## What is out of scope today

- **A CDN.** Cloud Run has no built-in CDN. A global static-asset audience would add Cloud CDN behind a load balancer. This API serves JSON, so it is a lesser concern.
- **Multi-region.** Single-region by design. The path is in [multi-region.md](./multi-region.md) and [ADR-0003](../docs/adr/0003-single-region-now-multi-region-later.md).
- **Background jobs.** Cloud Run's request model caps work at 300 seconds. Long work would move to Cloud Tasks or a Cloud Run job.
