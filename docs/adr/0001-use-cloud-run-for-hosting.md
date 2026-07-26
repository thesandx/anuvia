# ADR-0001: Use Cloud Run for hosting

- **Status:** Accepted
- **Date:** 2026-07-26
- **Deciders:** Solo developer

## Context

Anuvia is a stateless FastAPI backend for one or more small products. A solo developer with a small budget runs it. It must be deployable immediately, cheap at low traffic, and free of servers to operate.

The constraints:

- Traffic is low and bursty. Paying for idle capacity 24/7 wastes money that is not there.
- There is no operations team. A Kubernetes cluster is time the developer does not have.
- The app is a container already — it must run the same in local Docker, in CI, and in production.
- Server-side request handling means real compute, not static hosting.

## Decision

We will deploy to **Google Cloud Run**, as a container built by GitHub Actions and stored in a Google container registry.

## Alternatives considered

### Option A — Cloud Run (chosen)

Fully managed containers. Scales to zero, bills per request-time, terminates TLS, gives a URL on the first deploy, and streams logs to Cloud Logging with no agent.

The container abstraction is the deciding factor: what runs in `docker run` locally and in production is the same image. That removes an entire class of "works on my machine" failure. Scale-to-zero means an idle service costs nothing, which fits a pre-revenue budget.

### Option B — A VM on Compute Engine

Cheapest at steady high load and fully under our control.

Rejected because it means owning OS patching, process supervision, TLS renewal, log shipping, and a restart policy. It does not scale to zero, so idle cost is constant. For a stateless API with bursty traffic, that is cost and toil with no benefit.

### Option C — Google Kubernetes Engine

Maximum control, the right answer for many interdependent services.

Rejected as disproportionate. A cluster must be upgraded, secured, monitored, and paid for even when idle. For one stateless service, that is operational cost with no return.

### Option D — A platform-as-a-service (Railway, Render, Fly.io)

Excellent developer experience and a fast start.

A reasonable choice, and genuinely competitive for this workload. Rejected here to keep the whole stack — compute, logs, secrets, identity — in one Google Cloud account with one billing and one audit trail, and to keep the keyless-deploy path (Workload Identity Federation) open. A team already invested in one of these platforms could revisit.

## Consequences

**Good**

- No infrastructure to operate. No VM, no cluster, no patching.
- Scale-to-zero: an idle service costs nothing.
- Autoscaling with a configurable ceiling that bounds both load and spend.
- Managed TLS and a public URL on the first deploy.
- The same image runs locally, in CI, and in production.
- A keyless deploy path exists (Workload Identity Federation) for later hardening.
- Rollback is a traffic shift to an earlier revision — seconds, no rebuild.

**Bad**

- Cold starts when scaled to zero. Mitigated with `--min-instances=1` at roughly $10–15/month where latency matters.
- We own the Dockerfile and the pipeline — more setup than a `git push` PaaS.
- A 300-second request cap. Long work must move to a background job.
- No built-in CDN. A global audience needs a load balancer and CDN in front. See [ADR-0003](./0003-single-region-now-multi-region-later.md).
- Regional, not global, by default. Multi-region is possible but explicit.

**Neutral**

- Deployment is GitHub Actions rather than a platform integration: more YAML, more control.
- The database is a separate concern — Cloud Run is stateless, so persistence lives in Neon. See [ADR-0002](./0002-use-neon-postgres-for-persistence.md).

## Revisit when

- Cold starts hurt users even with `--min-instances`.
- The system grows into many interdependent services that need a mesh — then GKE earns its cost.
- A background-work need outgrows Cloud Run's request model — add Cloud Tasks or a job runner.

## References

- [Cloud Run documentation](https://cloud.google.com/run/docs)
- [`cloud/architecture.md`](../../cloud/architecture.md)
- [ADR-0002](./0002-use-neon-postgres-for-persistence.md), [ADR-0003](./0003-single-region-now-multi-region-later.md)
