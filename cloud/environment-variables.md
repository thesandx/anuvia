# Environment variables

How configuration reaches the app, and how to add a variable.

---

## The model

Every variable is read in exactly one place: `app/core/config.py`, through `pydantic-settings`. Nothing else reads the environment. The value's source differs by environment:

```
Local dev    .env file (git-ignored)      → Settings → app
Tests        set in tests/conftest.py      → Settings → app  (dummy values)
CI           env: block in ci.yml          → Settings → app  (dummy values)
Production   Cloud Run environment          → Settings → app
```

No secret is ever in the repository. `.env`, `.env.docker`, and `*.db` are git-ignored.

---

## The variables

| Variable                    | Required | Default                          | Purpose                                   |
| --------------------------- | -------- | -------------------------------- | ----------------------------------------- |
| `SECRET_KEY`                | **Yes**  | — (fails at startup if missing)  | JWT signing key. Min 32 random chars.     |
| `DATABASE_URL`              | No       | `sqlite+aiosqlite:///./local.db` | SQLAlchemy async connection string.       |
| `APP_NAME`                  | No       | `anuvia`                         | Shown in the API docs.                    |
| `APP_ENV`                   | No       | `development`                    | `production` disables `/docs` and `/redoc`. |
| `DEBUG`                     | No       | `false`                          | `true` echoes every SQL query.            |
| `ALGORITHM`                 | No       | `HS256`                          | JWT signing algorithm.                    |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No     | `30`                             | JWT lifetime in minutes.                  |
| `STRIPE_SECRET_KEY`         | No       | `""`                             | Stripe integration.                       |
| `STRIPE_WEBHOOK_SECRET`     | No       | `""`                             | Stripe webhook signature check.           |

---

## Build-time vs runtime

Every variable here is **runtime**. The container image contains no configuration and no secret. Cloud Run supplies the values at deploy. Two consequences:

- Changing a value never needs a rebuild. Redeploy, or update the service.
- No secret is baked into a layer, so `docker history` reveals nothing.

This app has **no** build-time public configuration. There is no equivalent of a `NEXT_PUBLIC_*` value inlined at build.

---

## Secrets: current state and the hardening path

**Today**, `deploy.yml` passes secrets with `--set-env-vars`:

```bash
--set-env-vars "SECRET_KEY=${{ secrets.SECRET_KEY }}"
```

This works, and it stores the value in the Cloud Run revision. Anyone with `roles/run.viewer` can read it:

```bash
gcloud run services describe anuvia --region us-central1 --format export
```

**The hardening path** is Secret Manager. Store the secret once, grant the runtime service account access, and reference it by name at deploy:

```bash
# Store it
echo -n "the-secret-value" | gcloud secrets create SECRET_KEY --data-file=-

# Grant the Cloud Run runtime service account read access
gcloud secrets add-iam-policy-binding SECRET_KEY \
  --member "serviceAccount:YOUR_RUNTIME_SA" --role roles/secretmanager.secretAccessor

# Reference it at deploy — the value is not stored in the revision
gcloud run deploy anuvia --set-secrets "SECRET_KEY=SECRET_KEY:latest" ...
```

With `--set-secrets`, the revision holds a reference, not the value, and access is audit-logged. Migrate `SECRET_KEY`, `DATABASE_URL`, and the Stripe secrets this way before real users.

---

## Adding a variable — four places, one pull request

Missing any step breaks somebody.

1. **`.env.example`** — add it with a comment: purpose, valid values, default, whether production requires it.
2. **`app/core/config.py`** — add the typed field to `Settings`. Give it a safe default, or no default if it must be present.
3. **`deploy.yml`** — add a `--set-env-vars` line, or a `--set-secrets` reference if it is a secret. Add the matching GitHub variable or secret.
4. **This file** — add a row to the table above, and a note in the secrets section if it is sensitive.

Then add the value where it runs: `.env` locally, and the GitHub variable or secret for production. Tests and CI use dummy values, so add it to `tests/conftest.py` or `ci.yml` only if the code path under test reads it.

---

## Reading a variable in code

Always through `settings`:

```python
from app.core.config import settings

if settings.is_production:
    ...
timeout = settings.ACCESS_TOKEN_EXPIRE_MINUTES
```

Never `os.getenv` outside `config.py`. It is untyped, unvalidated, and invisible to the startup check.
