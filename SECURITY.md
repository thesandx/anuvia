# Security

The security model of anuvia, and the checklist to complete before real users.

---

## The model

- **No secret lives in the repository.** `.env`, `.env.docker`, and `*.db` are git-ignored. Secrets reach the app only through the environment.
- **Configuration is validated once, centrally.** Only `app/core/config.py` reads the environment. A required secret with no default (`SECRET_KEY`) makes the app fail at startup, not silently at request time.
- **Passwords are never stored in plaintext.** `app/core/security.py` hashes them with bcrypt. The hash is stored; the password is not, and neither is ever logged.
- **`hashed_password` never leaves the server.** Response schemas (`UserResponse`) exclude it. A route returns a schema, never a raw ORM object.
- **Tokens are short-lived JWTs.** Signed with `SECRET_KEY` (HS256), carrying only the user id and a 30-minute expiry. The server loads the user on each protected request, so a disabled account stops working within the token's lifetime.
- **SQL is parameterised.** Queries use SQLAlchemy expressions, never an f-string with a user value. This is the defence against SQL injection.

---

## How secrets flow

```
Local dev    .env (git-ignored)      → pydantic-settings → app
CI tests     dummy values in ci.yml   → pydantic-settings → app
Production   GitHub Secrets → Cloud Run env → pydantic-settings → app
```

No secret touches git. The `.env.example` file is committed and contains only placeholders.

---

## Known gaps to close before production

These are safe for a low-traffic start and must be closed before real users. Each is documented where it is fixed.

### 1. CORS is open

`app/main.py` sets `allow_origins=["*"]` with `allow_credentials=True`. Browsers reject that combination, and a wildcard is not a safe production setting. **Fix:** restrict `allow_origins` to your real frontend domain(s).

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend.example.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 2. The deploy uses a service account key

`deploy.yml` authenticates with `GCP_SA_KEY`, a long-lived credential. **Fix:** migrate to Workload Identity Federation. See [`cloud/github-actions.md`](./cloud/github-actions.md).

### 3. Secrets are passed as environment variables at deploy

`--set-env-vars` stores the value in the Cloud Run revision, readable by anyone with `roles/run.viewer`. **Fix:** move secrets to Secret Manager and reference them with `--set-secrets`. See [`cloud/environment-variables.md`](./cloud/environment-variables.md).

### 4. Migrations run in the container start command

Not strictly a security issue, but a reliability one: concurrent instances race on boot. **Fix:** run migrations as a deploy-time step. See [`cloud/deployment.md`](./cloud/deployment.md).

---

## Pre-production hardening checklist

- [ ] `APP_ENV=production` in the deployed service — `/docs` and `/redoc` are disabled.
- [ ] `SECRET_KEY` is at least 32 random characters, unique to production.
- [ ] `DATABASE_URL` points to Neon (or Cloud SQL), never local SQLite.
- [ ] CORS `allow_origins` is restricted to your real frontend (gap 1).
- [ ] Deploy authenticates via Workload Identity Federation, not a key (gap 2).
- [ ] Secrets come from Secret Manager, not `--set-env-vars` (gap 3).
- [ ] Migrations run as a deploy-time step, not in the container `CMD` (gap 4).
- [ ] Branch protection on `main` requires a pull request and the gate checks: `Lint & Test`, `Docker image builds`, and `Analyze python` (CodeQL).
- [ ] Any AI or payment provider key is a secret, never in source or a `NEXT_PUBLIC`-style public value.
- [ ] Logs contain no token, password, or full connection string.
- [ ] A budget alert is set on the Google Cloud billing account.

---

## Reporting a vulnerability

This is a personal / small-team project. Report a suspected vulnerability privately to the maintainer — do not open a public issue with exploit detail. Include the affected endpoint, a reproduction, and the impact.
