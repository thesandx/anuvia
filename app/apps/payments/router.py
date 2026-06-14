from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User

from .schemas import SubscriptionResponse
from .service import PaymentService

router = APIRouter()
PREFIX = "/payments"
TAGS = ["payments"]


@router.get("/subscription", response_model=SubscriptionResponse | None)
async def get_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await PaymentService(db).get_subscription(current_user.id)
