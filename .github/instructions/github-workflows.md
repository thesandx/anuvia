# GitHub workflows

Read this before you touch anything in `.github/workflows/`.

---

## The two workflows

| File          | Trigger                    | Purpose                                          |
| ------------- | -------------------------- | ------------------------------------------------ |
| `ci.yml`      | Every push, every pull request | Lint, format check, and test.                |
| `deploy.yml`  | Push to `main`             | Build the image, push it, deploy to Cloud Run.   |

`ci.yml` is the gate. `deploy.yml` is the release. They never overlap: CI needs no cloud credentials, and deploy runs only after a merge to `main`.

---

## ci.yml

```yaml
name: CI
on:
  push:
    branches: ["**"]
  pull_request:
jobs:
  test:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"
      - run: pip install -r requirements.txt
      - run: ruff check .
      - run: ruff format --check .
      - run: pytest tests/ -v
        env:
          SECRET_KEY: "ci-test-secret-key-not-used-in-production"
          DATABASE_URL: "sqlite+aiosqlite:///:memory:"
          APP_ENV: "development"
```

Rules:

- **CI uses dummy secrets and an in-memory database.** The `SECRET_KEY` and `DATABASE_URL` in the `env:` block are throwaway values. CI never needs a real credential. Keep it that way — it is what lets a fork run CI.
- **The three steps match the local gate.** If `ruff check`, `ruff format --check`, and `pytest` pass locally, they pass in CI. If they do not, your local environment differs from `requirements.txt`.
- **The job name is `Lint & Test`.** Branch protection requires this exact name as a status check. If you rename the job, update the branch protection rule.

---

## deploy.yml

```yaml
name: Deploy to Cloud Run
on:
  push:
    branches: [main]
```

Rules:

- **It runs only on `main`.** A feature branch never deploys. The deploy happens on the push that a merged pull request creates.
- **It builds, tags with the commit SHA, pushes, and deploys the SHA tag.** The SHA tag is what makes a rollback a traffic shift. Do not deploy the `latest` tag to a revision — a revision pinned to a moving tag cannot be traced to a commit.
- **Secrets come from GitHub, non-sensitive values from GitHub variables.** `vars.*` for the project id, region, service name. `secrets.*` for `SECRET_KEY`, `DATABASE_URL`, and the GCP credential.
- **`permissions: contents: read`.** The job needs no more. When you migrate to Workload Identity Federation, add `id-token: write` — and only then.

---

## Rules for editing a workflow

1. **A secret goes in `env:` or a `with:` input, never spliced into a `run:` block.** `${{ secrets.X }}` inside a shell line becomes part of the command source before it runs. A value with a shell metacharacter breaks the command or worse. Pass it as an environment variable and reference `$X`.
2. **Pin actions to a major version** (`actions/checkout@v4`), matching the existing style.
3. **Do not add a real credential to `ci.yml`.** CI runs on untrusted pull requests. It must never hold anything a fork could exfiltrate.
4. **Keep the CI steps equal to the local gate.** If you add a check to CI, add it to the documented gate in `CLAUDE.md`, and the reverse.
5. **Least privilege.** Grant a job only the `permissions` it uses. Add `id-token: write` only to the job that federates identity.

---

## Verifying a workflow change

- **YAML that parses is not a workflow that runs.** A lint pass proves syntax, not behaviour.
- `ci.yml` validates itself: open a pull request and watch the run.
- `deploy.yml` only runs on `main`, so you cannot fully exercise it from a branch. Verify the deploy logic by running the equivalent `gcloud` commands manually against a test service first, or by reading the run log after the next merge.
- Before changing the build or deploy steps, verify the image locally: `docker build -t anuvia .` then run it. See [deployment.md](./deployment.md).

---

## The hardening backlog for these workflows

These are known gaps, not style choices. They are safe to leave for a low-traffic start and worth closing before real users:

- **`deploy.yml` uses a service account key** (`GCP_SA_KEY`). Migrate to Workload Identity Federation — see [`cloud/github-actions.md`](../../cloud/github-actions.md).
- **Secrets are passed as `--set-env-vars`**, which stores them in the revision. Migrate to Secret Manager with `--set-secrets` — see [`cloud/environment-variables.md`](../../cloud/environment-variables.md).
- **`deploy.yml` runs the migration inside the container.** Move it to a deploy-time step before you scale to more than one instance — see [deployment.md](./deployment.md).
