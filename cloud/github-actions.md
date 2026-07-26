# GitHub Actions and the deploy credential

How the pipeline authenticates to Google Cloud today, and the keyless path to harden it.

For the rules an assistant follows when editing a workflow, see [`.github/instructions/github-workflows.md`](../.github/instructions/github-workflows.md).

---

## Today: a service account key

`deploy.yml` authenticates with a service account key stored as the `GCP_SA_KEY` secret:

```yaml
- id: auth
  uses: google-github-actions/auth@v2
  with:
    credentials_json: ${{ secrets.GCP_SA_KEY }}
```

This works. The cost is that the key is a **long-lived bearer credential**: whoever holds it can act as the service account until someone revokes it. A leaked key in a log, a fork, or a backup is valid indefinitely.

While you use a key:

- Grant the account only the roles it needs: `roles/run.admin`, `roles/storage.admin` (or an Artifact Registry writer role), `roles/iam.serviceAccountUser`.
- Never print it. Never interpolate it into a `run:` block — pass it only through the `with:` input.
- Rotate it on any suspicion of exposure, and on a schedule.

---

## The hardening path: Workload Identity Federation

Workload Identity Federation removes the key entirely. GitHub presents a short-lived OIDC token that proves "this run is from this repository". Google exchanges it for temporary credentials scoped to the deploy service account. No key exists anywhere.

### Set it up

```bash
# 1. A workload identity pool
gcloud iam workload-identity-pools create github-pool \
  --location=global --display-name="GitHub pool"

# 2. A provider that trusts GitHub's OIDC issuer, bound to your repository
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github-pool \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='YOUR_GITHUB_USER/anuvia'"

# 3. Let the pool impersonate the deploy service account
gcloud iam service-accounts add-iam-policy-binding \
  github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/YOUR_GITHUB_USER/anuvia"
```

The `attribute-condition` is the security boundary. It binds the credential to **this repository**, so no other repository can use the provider.

### Change the workflow

```yaml
permissions:
  contents: read
  id-token: write          # required for OIDC — add it only to this job

steps:
  - uses: actions/checkout@v4
  - id: auth
    uses: google-github-actions/auth@v2
    with:
      workload_identity_provider: projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider
      service_account: github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

Then delete the `GCP_SA_KEY` secret and the service account key.

---

## Enable the extra APIs

```bash
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com
```

---

## Troubleshooting

### `Permission denied` exchanging the token

The `attribute-condition` does not match, or the `workloadIdentityUser` binding is missing. Confirm the repository string in the condition matches `owner/repo` exactly, and that the `principalSet` member uses the same attribute.

### `id-token` not available

The job is missing `permissions: id-token: write`. Add it to the job, not just the workflow. Keep it off every other job — only the deploy needs it.

### The deploy authenticates but cannot push the image or deploy

The service account lacks a role. Grant `roles/run.admin`, a registry write role, and `roles/iam.serviceAccountUser`.

### It worked from `main` but fails from a fork

Correct and intended. A fork cannot present this repository's identity, so it cannot deploy. Keep deploy credentials out of `ci.yml` so a fork's CI still runs.

---

## Why this matters

The whole point is that **no credential lives anywhere it can leak**. A key in a secret is one exfiltration away from being someone else's key. A federated token is minted per run, scoped to this repository, and expires in minutes. It is the same reasoning as keeping secrets out of the image: remove the standing credential, remove the standing risk.
