from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None


class ChatResponse(BaseModel):
    session_id: int
    reply: str


class SessionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str | None
