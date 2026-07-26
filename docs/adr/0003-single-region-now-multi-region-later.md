# ADR-0003: Run single-region now; a defined path to multi-region when needed

- **Status:** Accepted
- **Date:** 2026-07-26
- **Deciders:** Solo developer

## Context

The question that forces this decision: "should anuvia be multi-region, and if so, how, at minimal cost for a solo developer?" It splits into parts that are often confused:

- **Two different goals.** "Multi-region" can mean *low latency for users far away* (serve each user from a nearby region) or *survive a region outage* (disaster recovery). They need different things and cost differently.
- **The real latency source.** In this app the dominant latency is the round trip between the app and the database. One request runs several queries. If the app and the database are far apart, every request pays that distance several times over. This is true within one region already and is the first thing to get right.
- **A writable database cannot be everywhere cheaply.** A relational database has one write primary. Making writes multi-master is expensive and complex, and this app has strongly consistent data (payments, auth). Read replicas near users are cheap and simple; multi-primary writes are neither.
- **The budget.** A solo developer, pre-revenue. Cost and operational simplicity outrank theoretical global performance.
- **Two current blockers.** The container runs migrations on start (they race across instances), and the app reads the user from the database on every authenticated request (the exact call that cross-region latency punishes). Both must be addressed before any multi-region step.

## Decision

We will **run single-region now**, with Cloud Run and the Neon database in the **same geography**, and keep a **single write primary** in every future step. We will add regions only when a real, measured latency problem for a distant user base justifies it, and then by the **regional-compute, local-read-replica, single-write-primary** pattern — never by sharding the schema per app and never by adopting multi-primary writes.

The full runbook is [`cloud/multi-region.md`](../../cloud/multi-region.md). This ADR records why.

## Alternatives considered

### Option A — Single region now, co-located app and database (chosen for phase 1)

Deploy Cloud Run and Neon in the same geography. Pick the region closest to the primary user base. Use Neon's pooled (PgBouncer) connection string. Cost is about $0 on both free tiers.

This removes the real latency problem (app-to-database distance) and defers the cost and complexity of extra regions until there is evidence they are needed. It is the correct starting point for every solo project.

### Option B — Regional compute + local read replica + single write primary (chosen for phase 2)

When a distant user base has a measured latency problem: deploy Cloud Run to each needed region behind a global external HTTPS load balancer. Give each region a **read replica** of the database. Route **all writes to the single primary region**; serve reads that tolerate slight staleness from the local replica.

This is the standard, affordable multi-region pattern. It classifies work by app type — writes and strongly consistent reads go to the primary; read-heavy, lag-tolerant reads go local:

| App                     | Access pattern                          | Where it runs           |
| ----------------------- | --------------------------------------- | ----------------------- |
| `payments`              | Rare writes, strong consistency         | Primary region only     |
| `auth` register/login   | Rare writes, correctness critical       | Write to primary        |
| `auth` token → user     | Read on every authenticated request     | Local replica or cache  |
| `ai_chat` history reads | Read-heavy, append-only, lag-tolerant   | Local replica           |

### Option C — Multi-primary / globally writable database

A database that accepts writes in every region (a distributed SQL system, or multi-master PostgreSQL).

Rejected. It is expensive, operationally heavy, and solves a problem this app does not have. Payments and auth need strong consistency; conflict resolution across write primaries is exactly the complexity a solo developer must avoid. If write latency for distant users ever becomes the binding constraint, that is a specific, later decision with its own ADR — not the default.

### Option D — Turso / distributed SQLite at the edge

Adopt libSQL replicas near users instead of PostgreSQL.

Rejected for now, for the reasons in [ADR-0002](./0002-use-neon-postgres-for-persistence.md): the SQLAlchemy dialect is sync-only and does not fit the async stack, and the edge-read benefit only pays off with a global, read-dominant user base. It remains the option to reconsider if the workload becomes exactly that.

### Option E — Cloud Run multi-region with every region reading the single primary (no replica)

Deploy compute to several regions but keep one database, so distant regions read across the world.

Rejected. It makes latency worse, not better: a distant instance now pays the long round trip on every query. Multi-region compute without local data is slower than single-region. Local data (a replica) is the whole point.

## Consequences

**Good**

- Phase 1 costs about $0 and removes the real latency problem immediately.
- The single-write-primary rule keeps payments and auth simple and correct in every phase.
- The path to multi-region is defined in advance, so the phase-2 step is mechanical, not a redesign.
- Classifying by app type turns "multi-region" into concrete read/write routing, not a vague goal.

**Bad**

- Users far from the single region see higher latency until phase 2. Accepted deliberately — it is not worth paying for regions before there are users in them.
- Phase 2 adds real cost and operational surface: read replicas (a paid Neon feature or a Cloud SQL replica), a global load balancer (~$18+/month base), and read/write routing in the app.
- Read replicas serve slightly stale data. Every read routed to a replica must tolerate replication lag. Reads that cannot must go to the primary.
- Two prerequisites must be fixed first (below), which is work before any region is added.

**Neutral**

- Disaster recovery is a side effect, not the driver. A replica in a second region also protects against a primary outage, but the trigger for phase 2 is latency, not availability.

## Prerequisites (fix before any multi-region step)

1. **Move migrations out of the container start command.** Run `alembic upgrade head` once, as a deploy-time step, not in the `CMD`. Concurrent instances must not race on it, and every migration must be backward compatible with the running revision. See [`cloud/deployment.md`](../../cloud/deployment.md).
2. **Stop reading the user from the primary on every authenticated request.** `get_current_user` loads the user on each protected call. Across regions this is the call latency punishes most. Serve it from a local replica, or cache the user lookup (short TTL) so a distant request does not cross the world to authenticate.

## Revisit when

- Users in a region far from the primary report or measure real latency, and app-to-database co-location alone does not fix it → begin phase 2 (Option B).
- App-to-database latency is the bottleneck even in one region → move the database to Cloud SQL in the same region as Cloud Run ([ADR-0002](./0002-use-neon-postgres-for-persistence.md) Option D).
- The workload becomes globally distributed and read-dominant → reconsider Turso (Option D) for the read path.
- Write latency for distant users becomes the binding constraint → a new ADR on multi-primary or write routing (Option C).

## References

- [`cloud/multi-region.md`](../../cloud/multi-region.md) — the runbook
- [ADR-0001](./0001-use-cloud-run-for-hosting.md), [ADR-0002](./0002-use-neon-postgres-for-persistence.md)
- [Neon read replicas](https://neon.tech/docs/introduction/read-replicas)
- [Cloud Run and global load balancing](https://cloud.google.com/run/docs/multiple-regions)
