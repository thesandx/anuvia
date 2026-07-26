# GitHub workflows

Read this before you touch anything in `.github/workflows/`.

---

## The three workflows

| File          | Trigger                              | Purpose                                          |
| ------------- | ------------------------------------ | ------------------------------------------------ |
| `ci.yml`      | Every push, every pull request into `main`, manual | Lint, format check, test, and a Docker build + smoke test. |
| `codeql.yml`  | Pull request into `main`, push to `main`, weekly, manual | Static security analysis (CodeQL). |
| `deploy.yml`  | Push to `main`                       | Build the image, push it, deploy to Cloud Run.   |

`ci.yml` and `codeql.yml` are the pre-merge gate. `deploy.yml` is the release. They never overlap: the gate needs no cloud credentials, and deploy runs only after a merge to `main`.

The gate has **three required checks**: `Lint & Test`, `Docker image builds`, and `Analyze python`. Require all three in branch protection. See [`cloud/deployment.md`](../../cloud/deployment.md).

---

## ci.yml

Two jobs run in parallel.

**Job `test` (`Lint & Test`)** — the fast gate:

```yaml
- run: ruff check .              # lint
- run: ruff format --check .     # format check
- run: pytest tests/ -v          # tests, dummy SECRET_KEY, in-memory SQLite
```

**Job `docker` (`Docker image builds`)** — proves the production image works:

- Builds the real image with Buildx and the GitHub Actions cache. Nothing is pushed.
- Boots the container and polls `/health` until it answers. This catches what a build alone cannot: the app failing to start, migrations failing on boot, binding to `localhost` instead of `0.0.0.0`, or ignoring `$PORT`.
- The container uses its default SQLite database, so the smoke test needs no external database — the same choice the unit tests make.

Rules:

- **The gate uses dummy secrets and SQLite.** The `SECRET_KEY` and `DATABASE_URL` are throwaway values. The gate never needs a real credential. Keep it that way — it is what lets a fork's pull request run.
- **The `test` steps match the local gate.** If `ruff check`, `ruff format --check`, and `pytest` pass locally, they pass in CI. If they do not, your local environment differs from `requirements.txt`.
- **The job names are `Lint & Test` and `Docker image builds`.** Branch protection requires these exact names. If you rename a job, update the branch protection rule.
- **`permissions: contents: read` and `concurrency` cancel-in-progress.** The gate only reads the repository, and a new push cancels the superseded run.

---

## codeql.yml

CodeQL runs static security analysis on the Python source and uploads findings to the Security tab.

Rules:

- **CodeQL is free on public repositories.** anuvia is public, so it works with no setup. On a **private** repository it needs GitHub Advanced Security; without it the analysis runs, then fails at upload. If you make the repository private and lack GHAS, delete `codeql.yml` rather than leave a permanently red check.
- **`build-mode: none`.** Python needs no build, so CodeQL scans the source directly. Do not add an autobuild step.
- **`security-events: write` on the job only.** That permission is needed to upload results. Keep it scoped to this job.

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
