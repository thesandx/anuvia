from pydantic import BaseModel


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str = "1.0.0"
    # Last deploy time in IST, e.g. "2026-07-26 17:45:00 IST".
    # None when the running build was not deployed by the pipeline (local dev).
    deployed_at: str | None = None
