# Cloud infrastructure

Everything about running anuvia on Google Cloud: the architecture, the deployment pipeline, the configuration model, the region strategy, and the hardening path.

## Contents

| Document                                               | What it covers                                                       |
| ------------------------------------------------------ | -------------------------------------------------------------------- |
| [architecture.md](./architecture.md)                   | Target architecture, services, scaling, and the cost model           |
| [deployment.md](./deployment.md)                       | The operator runbook: one-time setup, deploying, verifying, rollback |
| [environment-variables.md](./environment-variables.md) | Runtime configuration, secrets, and how to add a variable            |
| [github-actions.md](./github-actions.md)               | The deploy credential today, and the keyless hardening path          |
| [multi-region.md](./multi-region.md)                   | Regions, replicas, latency, and cost — the answer to "go multi-region?" |

## The stack in one paragraph

GitHub Actions builds the FastAPI app into a small Python container. It pushes the image to a Google container registry and deploys it to Cloud Run as a new revision tagged with the commit SHA. Cloud Run autoscales from zero, terminates TLS, and streams logs to Cloud Logging. The application data lives in Neon, a serverless PostgreSQL database, reached over `asyncpg`. Local development and tests use SQLite instead, so they need no cloud account.

## Google Cloud services used

| Service               | Role                                       | Why this one                                             |
| --------------------- | ------------------------------------------ | -------------------------------------------------------- |
| **Cloud Run**         | Runs the container, autoscales, ends TLS    | Serverless containers, scale-to-zero, per-request billing |
| **Container registry**| Stores the image                            | The deploy target for the built image                    |
| **Cloud Logging**     | Log aggregation and search                  | Automatic — Cloud Run forwards stdout/stderr            |
| **Secret Manager**    | Secret storage (hardening target)           | Versioned, IAM-controlled; mounted into Cloud Run        |
| **IAM**               | Deploy identity                             | Grants the pipeline permission to push and deploy        |

The database (Neon) is **not** a Google Cloud service. It is a separate managed provider, chosen in [ADR-0002](../docs/adr/0002-use-neon-postgres-for-persistence.md).

## Required Google Cloud APIs

Enable once, before the first deploy:

```bash
gcloud services enable run.googleapis.com containerregistry.googleapis.com
# Add when you adopt the hardening path:
gcloud services enable secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com
```

## Cost model

Cloud Run bills per request-time, plus a small per-request fee. With min-instances 0, an idle service costs nothing. Neon's free tier scales to zero as well.

Indicative for a low-traffic service:

| Setup                                             | Approximate monthly cost      |
| ------------------------------------------------- | ----------------------------- |
| Cloud Run under the free tier + Neon free tier     | $0                            |
| Cloud Run `--min-instances=1` (no cold starts)     | ~$10–15 for the warm instance |
| Cloud SQL smallest instance (if you leave Neon)    | ~$8–10, always on             |
| Multi-region: global load balancer base            | ~$18+, before traffic         |

Set a budget alert before the first deploy. Multi-region cost is the subject of [multi-region.md](./multi-region.md).

## Where to start

- Deploying for the first time? → [deployment.md](./deployment.md)
- Adding or changing a config value? → [environment-variables.md](./environment-variables.md)
- Wondering about regions and global users? → [multi-region.md](./multi-region.md)
- The deploy credential and how to harden it? → [github-actions.md](./github-actions.md)
- Something failing? → [../docs/troubleshooting.md](../docs/troubleshooting.md)
