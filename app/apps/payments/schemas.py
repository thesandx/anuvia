from pydantic import BaseModel


class SubscriptionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    user_id: int
    plan: str
    status: str


class CreateCheckoutRequest(BaseModel):
    plan: str
    success_url: str
    cancel_url: str
