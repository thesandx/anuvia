# ADR-0002: Use Neon PostgreSQL for persistence; SQLite for local and tests

- **Status:** Accepted
- **Date:** 2026-07-26
- **Deciders:** Solo developer

## Context

Cloud Run is stateless. Its filesystem is ephemeral, and instances do not share it. The application therefore needs an external database. The choice must:

- cost near zero at low traffic (a pre-revenue, solo budget)
- work with the app's fully async stack (`create_async_engine`, so an async driver is required)
- need no server to operate
- keep local development and tests fast, offline, and free of setup

A question keeps recurring: "should we use SQLite instead, maybe a distributed SQLite like Turso, to keep it cheap and edge-friendly?" This ADR answers it directly.

## Decision

We will use **Neon** — serverless PostgreSQL over the `asyncpg` driver — for production. We will use **SQLite** over `aiosqlite` for local development and tests. The `DATABASE_URL` scheme selects the driver; no code branches beyond the SSL argument in `app/core/database.py`.

## Alternatives considered

### Option A — Neon PostgreSQL for production, SQLite for local (chosen)

Neon is serverless PostgreSQL: it scales compute to zero, has a free tier with no credit card, and speaks `asyncpg` natively. It pairs well with Cloud Run — both scale to zero, so an idle stack is nearly free. Local development keeps SQLite, so a new contributor needs no account and no network. The same SQLAlchemy models run on both because both go through the async engine.

### Option B — Application-local SQLite in production

Ship a SQLite file inside the container.

Rejected because it cannot work on Cloud Run. The filesystem is ephemeral, so the file — and all its data — vanishes when the instance is recycled. Multiple instances each get their own file, so there is no single source of truth. SQLite as a server database also serialises writes to one writer. It is correct for local development and tests, and wrong for a multi-instance server.

### Option C — Turso / libSQL (distributed SQLite)

Turso is SQLite at the edge, with replicas near users and a free tier. It is a genuinely attractive story for read-heavy, globally distributed apps, and it is the option people mean when they ask "why not SQLite?".

Rejected for this codebase, today, for two concrete reasons. First, the SQLAlchemy dialect for libSQL is **sync-only**, and this app is async end to end (`create_async_engine`); adopting it means rewriting the data layer or running a blocking driver inside the event loop. Second, the benefit — edge-local reads — only pays off with a real, globally distributed, read-dominant user base, which a new solo project does not have. The migration cost is real and the payoff is deferred. It stays on the table for a future read-heavy, global workload — reconsider it in [ADR-0003](./0003-single-region-now-multi-region-later.md)'s revisit conditions.

### Option D — Cloud SQL (managed PostgreSQL on Google Cloud)

Google's managed PostgreSQL, in the same project and the same region as Cloud Run — the tightest possible co-location, and no cross-cloud hop.

Rejected as the default because it does not scale to zero. The smallest instance costs roughly $8–10/month whether or not anyone uses it. That is the wrong shape for a pre-revenue budget. It becomes the right answer when app-to-database latency must be minimal, because it can sit in the exact same region as the service. It is the recommended upgrade target in [ADR-0003](./0003-single-region-now-multi-region-later.md).

## Consequences

**Good**

- Near-zero cost at low traffic: Neon and Cloud Run both scale to zero.
- No database server to operate.
- Native async support through `asyncpg`.
- Local development and tests stay fast, offline, and setup-free on SQLite.
- The same models and queries run on both databases.

**Bad**

- Two databases means two behaviours. SQLite does not reproduce every PostgreSQL rule, so some issues appear only against Neon. Mitigated by the production-like Docker run.
- Neon runs on AWS or Azure, while Cloud Run runs on Google Cloud. "Same region" across clouds means the same geography, not the same datacenter, so a small cross-cloud hop remains. Mitigated by choosing matching regions and using Neon's pooled connection. Removed entirely only by moving to Cloud SQL (Option D).
- The connection string needs a manual edit (`postgresql+asyncpg://`, no `sslmode` query parameter). Documented in the README and [troubleshooting](../troubleshooting.md).

**Neutral**

- Neon's scale-to-zero adds a cold-start delay to the first query after idle. Acceptable at this scale.

## Revisit when

- App-to-database latency becomes the measured bottleneck → move to Cloud SQL in the same region (Option D).
- A real, globally distributed, read-heavy user base appears → reconsider read replicas or Turso (Option C), per [ADR-0003](./0003-single-region-now-multi-region-later.md).
- Neon's free tier stops fitting the workload → compare Neon paid, Cloud SQL, and Supabase on price and features.

## References

- [Neon documentation](https://neon.tech/docs)
- [`app/core/database.py`](../../app/core/database.py)
- README "Setting Up Neon"
- [ADR-0003](./0003-single-region-now-multi-region-later.md)
