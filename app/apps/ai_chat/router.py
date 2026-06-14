from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User

from .schemas import ChatRequest, ChatResponse
from .service import AIChatService

router = APIRouter()
PREFIX = "/ai-chat"
TAGS = ["ai-chat"]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await AIChatService(db).chat(current_user.id, body.message, body.session_id)
    return ChatResponse(**result)
