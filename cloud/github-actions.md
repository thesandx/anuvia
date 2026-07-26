# GitHub Actions and the deploy credential

How the pipeline authenticates to Google Cloud — keyless, with no stored credential.

For the rules an assistant follows when editing a workflow, see [`.github/instructions/github-workflows.md`](../.github/instructions/github-workflows.md).

---

## Today: keyless via Workload Identity Federation

`deploy.yml` authenticates with a short-lived OIDC token. There is **no service account key** anywhere:

```yaml
permissions:
  contents: read
  id-token: write          # required to mint the OIDC token

steps:
  - id: auth
    uses: google-github-actions/auth@v2
    with:
      workload_identity_provider: ${{ vars.WIF_PROVIDER }}
      service_account: ${{ vars.WIF_SERVICE_ACCOUNT }}
```

GitHub presents a token that proves "this run is from `thesandx/anuvia`". Google exchanges it for temporary credentials scoped to the deployer service account. The token expires in minutes and cannot be reused elsewhere.

> **History:** the pipeline used a `GCP_SA_KEY` secret before. A key is a long-lived bearer credential — valid until revoked if it leaks. It has been removed in favour of federation. Do not reintroduce a key.

The deployer service account keeps only the roles it needs: `roles/run.admin`, `roles/storage.admin` (or an Artifact Registry writer role), and `roles/iam.serviceAccountUser`.

---

## How it works, and the one-time setup

Workload Identity Federation needs no key. GitHub presents a short-lived OIDC token that proves "this run is from this repository". Google exchanges it for temporary credentials scoped to the deploy service account. No key exists anywhere.

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

### The GitHub side (already wired in `deploy.yml`)

`deploy.yml` reads two **variables** — not secrets, since neither is sensitive. Set them in Settings → Secrets and variables → Actions → Variables:

| Variable | Value |
| --- | --- |
| `WIF_PROVIDER` | the provider resource name from step 2 above (`projects/NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider`) |
| `WIF_SERVICE_ACCOUNT` | the deployer email (`github-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com`) |

The job also declares `permissions: id-token: write`, which lets GitHub mint the OIDC token. That permission is on the deploy job only — keep it off every other job.

Once a deploy succeeds with federation, delete the old `GCP_SA_KEY` secret and delete the service account key (`gcloud iam service-accounts keys delete`). No key should remain.

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
