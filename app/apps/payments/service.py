from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.payments.models import Subscription


class PaymentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_subscription(self, user_id: int) -> Subscription | None:
        result = await self.db.execute(select(Subscription).where(Subscription.user_id == user_id))
        return result.scalar_one_or_none()

    async def create_free_subscription(self, user_id: int) -> Subscription:
        sub = Subscription(user_id=user_id, plan="free", status="active")
        self.db.add(sub)
        await self.db.commit()
        await self.db.refresh(sub)
        return sub

    # Wire up Stripe here: create_checkout_session(), handle_webhook(), etc.
