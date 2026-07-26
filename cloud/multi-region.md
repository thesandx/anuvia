# Multi-region: the practical guide

This document answers one question: **should anuvia be multi-region, and if so, how, at minimal cost for a solo developer?**

The decision and its reasoning are in [ADR-0003](../docs/adr/0003-single-region-now-multi-region-later.md). This is the runbook.

---

## The short answer

**Not yet. Do this instead, in order:**

1. **Now:** run one region. Put Cloud Run and Neon in the **same geography**. Cost: ~$0.
2. **When app-to-database latency is the bottleneck in that one region:** move the database to Cloud SQL in the exact same Google Cloud region. Cost: ~$8–10/month.
3. **Only when a real, distant user base has a measured latency problem:** add regions, each with a local read replica, all writes to one primary. Cost: ~$18+/month plus replicas.

Do not skip to step 3. Multi-region before you have distant users is money and complexity spent on a problem you do not have.

---

## First: understand what actually causes latency here

A single API request in this app runs **several** database queries. For example, an authenticated request:

1. Decodes the JWT.
2. Loads the user from the database (`get_current_user`).
3. Runs the endpoint's own queries.
4. Commits.

Every one of those database calls is a round trip. If the app is in one region and the database is in another, each round trip pays the distance between them. A 50 ms gap becomes 150–250 ms per request once you multiply by the number of queries.

**This is the number that matters, and you fix it by putting the app and the database together — not by adding regions.** A distant user with a co-located app and database is far better off than a distant user hitting a multi-region setup where the database is still across the world.

---

## Step 1 — one region, co-located (do this now)

### Pick the region

Choose the region closest to most of your users. If you do not know, pick a central one (`us-central1`, or a European or Asian region for those audiences).

### Put the database in the same geography

- Create the Neon project in the **same geography** as your Cloud Run region. Neon runs on AWS and Azure; Cloud Run runs on Google Cloud. "Same region" across two clouds means the same metro area (for example, both in `us-east`), not the same datacenter. A small cross-cloud hop remains — it is single-digit to low-double-digit milliseconds within a metro, which is fine at this scale.
- Use Neon's **pooled** connection string (it runs PgBouncer). Pooling matters more than a few milliseconds of distance when Cloud Run opens many short-lived connections.

### If you want zero cross-cloud hop

Move the database to **Cloud SQL** in the exact same Google Cloud region as Cloud Run. Same region, same network, lowest latency. The cost is the trade: Cloud SQL does not scale to zero, so the smallest instance is ~$8–10/month whether or not anyone uses it. Do this when latency is measured and real, not preemptively. See [ADR-0002](../docs/adr/0002-use-neon-postgres-for-persistence.md) Option D.

### Result

One region, app and database together, ~$0 on free tiers. This is the correct production setup for a new solo product, and it removes the real latency problem.

---

## Step 2 — fix the two blockers before adding any region

Two things in the current code break or slow down a multi-region setup. Fix them first.

### Blocker 1 — migrations run in the container start command

The `Dockerfile` runs `alembic upgrade head && uvicorn ...`. With one instance it works. With several instances, or several regions, every instance runs the migration on boot and they race.

**Fix:** run the migration once, as a deploy-time step, and remove it from the container `CMD`.

- Option A: add a step to `deploy.yml` that runs `alembic upgrade head` against the production database **before** the `gcloud run deploy` line.
- Option B: run it as a one-off Cloud Run **job** that shares the image, triggered before the service deploy.
- Either way, change the service `CMD` to just `uvicorn app.main:app --host 0.0.0.0 --port ${PORT}`.
- Every migration must be **backward compatible** with the currently running revision, because old and new instances overlap during a rollout. Add a column before code reads it; do not drop a column the old code still writes.

### Blocker 2 — the app reads the user on every authenticated request

`get_current_user` loads the user from the database on every protected call. In one region this is cheap. Across regions, this is the exact call that cross-region latency punishes — every authenticated request from a distant region would cross the world just to authenticate.

**Fix, before multi-region:**

- Serve this read from a **local read replica** in each region (step 3 provides the replica), or
- **Cache** the user lookup with a short TTL, so repeated requests from the same user do not re-query. Keep the TTL short (seconds to a minute) so a disabled or changed user takes effect quickly.

---

## Step 3 — add regions (only when a distant user base has a real latency problem)

The trigger is evidence: users far from your region measure or report high latency, and step 1 co-location does not fix it because the distance to the single region is the problem. Then, and only then:

### The pattern: regional compute, local read replica, single write primary

```
                       ┌─────────────────────────┐
   users (US)  ─────▶  │ Global external HTTPS LB │  ◀─────  users (EU)
                       └───────────┬──────────────┘
                       ┌───────────┴───────────┐
                 ┌─────▼─────┐           ┌──────▼──────┐
                 │ Cloud Run │           │  Cloud Run  │
                 │  us-east  │           │  eu-west    │
                 └─────┬─────┘           └──────┬──────┘
              reads    │  writes ───────▶       │  reads
                 ┌─────▼─────┐  (all writes)┌───▼─────────┐
                 │  DB read  │◀─replication─│  DB PRIMARY  │
                 │  replica  │              │  (writes)    │
                 └───────────┘              └──────────────┘
```

- **Global external HTTPS load balancer** in front, one anycast IP. It routes each user to the nearest healthy Cloud Run region. Base cost ~$18+/month before traffic.
- **Cloud Run deployed to each region**, same image, same config.
- **A read replica of the database in each region.** Neon offers read replicas on paid plans; Cloud SQL offers cross-region read replicas. The replica serves local reads with low latency.
- **All writes go to the single primary region.** There is one writable database. Distant regions send writes to it and accept the write latency, because writes are rarer and must stay consistent.

### Route by app type (this is what "multi-region based on app type" means)

Do not split the schema per app. Split **reads from writes**, and classify each app by whether its reads tolerate slight replication lag:

| App                     | Reads                              | Writes                    |
| ----------------------- | ---------------------------------- | ------------------------- |
| `payments`              | From primary (must be consistent)  | Primary                   |
| `auth` register / login | —                                  | Primary                   |
| `auth` token → user     | From local replica, or cached      | Primary (on register)     |
| `ai_chat` history       | From local replica (lag is fine)   | Primary (append messages) |

In code, this means two engines: a **writer** engine pointed at the primary and a **reader** engine pointed at the local replica. A read-only endpoint uses the reader; anything that writes uses the writer. Add this only in step 3 — it is needless complexity in one region.

### What not to do

- **Do not deploy compute to many regions while keeping one database with no replica.** Distant instances then pay the long round trip on every query — slower than one region. Local data is the entire point.
- **Do not make the database multi-primary** to avoid write latency. That is expensive, complex, and wrong for payments and auth. If write latency for distant users ever becomes the binding constraint, write a new ADR for that specific case.
- **Do not switch to Turso** to get edge reads unless the workload has genuinely become global and read-dominant — and even then, the async-dialect limitation in [ADR-0002](../docs/adr/0002-use-neon-postgres-for-persistence.md) applies.

---

## Cost summary

| Phase                                          | What runs                                        | Approx. monthly cost      |
| ---------------------------------------------- | ------------------------------------------------ | ------------------------- |
| Step 1 — one region, Neon free                 | Cloud Run scale-to-zero + Neon free              | $0                        |
| Step 1 + no cold starts                        | Cloud Run `--min-instances=1`                    | ~$10–15                   |
| Step 2 — zero-hop database                     | Cloud SQL smallest instance, same region         | +$8–10                    |
| Step 3 — two regions                           | Global LB + 2× Cloud Run + 1 read replica        | ~$18+ (LB) + replica cost |

For a solo developer on a small budget, **step 1 is the answer for a long time.** Steps 2 and 3 are triggered by measured problems, not by planning.

---

## Decision checklist

Before you add a region, confirm all of these. If any is "no", you are not ready for step 3:

- [ ] The app and database are already co-located in one region (step 1 done).
- [ ] You have **measured** latency (not guessed) and a distant user base is affected.
- [ ] Migrations no longer run in the container `CMD` (blocker 1 fixed).
- [ ] The per-request user read is served locally or cached (blocker 2 fixed).
- [ ] You accept the ~$18+/month load-balancer base cost plus replica cost.
- [ ] Every read you will route to a replica tolerates a little staleness.

## References

- [ADR-0003 — the decision](../docs/adr/0003-single-region-now-multi-region-later.md)
- [ADR-0002 — the database choice](../docs/adr/0002-use-neon-postgres-for-persistence.md)
- [Cloud Run: serving from multiple regions](https://cloud.google.com/run/docs/multiple-regions)
- [Neon read replicas](https://neon.tech/docs/introduction/read-replicas)
