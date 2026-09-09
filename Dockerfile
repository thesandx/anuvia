FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Cloud Run injects PORT env var; default to 8080
ENV PORT=8080

# Start the server. Migrations are NOT run here: they run once, as a deploy-time
# step in .github/workflows/deploy.yml, before the new revision is deployed.
# Running them on boot makes every Cloud Run instance race on the same locks and
# turns a bad migration into a full outage instead of a failed deploy step.
# exec replaces the shell so Uvicorn receives Cloud Run's SIGTERM directly.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
