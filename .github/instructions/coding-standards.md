# Coding standards

How to write Python, an endpoint, a model, and a query in this repository. Follow the patterns already in the code over patterns from your training data.

---

## Python style

- **Target Python 3.12.** Use modern syntax: `str | None` instead of `Optional[str]`, `list[int]` instead of `List[int]`, `datetime.now(UTC)` instead of `utcnow()`.
- **Ruff is the only style tool.** It lints (`E`, `F`, `I`, `UP`, `B`, `SIM`) and formats. Line length is 100. Run `ruff check . --fix` and `ruff format .`.
- **Type the boundaries.** Annotate function parameters and return types on public methods. Let inference handle obvious local variables.
- **No bare `except`.** Catch the specific exception. `app/core/security.py` catches `JWTError`, not `Exception`.
- **Docstrings explain intent, not mechanism.** Write one when a function's purpose is not obvious from its name and signature.

---

## Writing an endpoint

A route is thin. It validates, delegates, and shapes the response.

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User

from .schemas import ThingRequest, ThingResponse
from .service import ThingService

router = APIRouter()
PREFIX = "/things"
TAGS = ["things"]


@router.post("/", response_model=ThingResponse, status_code=201)
async def create_thing(
    body: ThingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ThingService(db).create(current_user.id, body)
```

Rules:

- **Set `response_model` on every route.** It is the contract and the output filter.
- **Set the status code** where it is not 200 (`201` for create, `204` for delete).
- **Inject the session** with `Depends(get_db)`. Never construct an engine or session in a route.
- **Protect a route** by adding `current_user: User = Depends(get_current_user)`. Its presence both authenticates and gives you the user.
- **Keep the body one line** where possible: build the service, call one method, return.

---

## Writing a service

The service holds the logic and owns the transaction.

```python
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession


class ThingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, user_id: int, body) -> Thing:
        thing = Thing(user_id=user_id, name=body.name)
        self.db.add(thing)
        await self.db.commit()
        await self.db.refresh(thing)
        return thing
```

Rules:

- **Take the session in `__init__`.** Every service in this codebase does (`AuthService`, `AIChatService`, `PaymentService`).
- **Raise `HTTPException` for domain errors** with the right status: `409` for a conflict, `401` for bad credentials, `403` for an inactive account, `404` for a missing row.
- **Commit once, at the end of the operation.** After a commit, `refresh` the object if you return it, so it carries its generated id and defaults.
- **No HTTP knowledge below this line.** A service raises `HTTPException`, but it does not read the request or write the response.

---

## Writing a model

```python
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Thing(Base, TimestampMixin):
    __tablename__ = "things"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
```

Rules:

- **Inherit `Base` and `TimestampMixin`.** The mixin gives `created_at` and `updated_at` for free.
- **Type columns with `Mapped[...]`** and `mapped_column(...)`. This is the SQLAlchemy 2.0 style used throughout.
- **Index foreign keys and lookup columns.** `user_id` and any column you filter on gets `index=True`.
- **Set a length on `String`.** PostgreSQL is forgiving; some databases are not. Use `Text` for unbounded content (see `ChatMessage.content`).
- **After adding or changing a model**, generate a migration and add the module to `alembic/env.py` if it is new.

---

## Writing a query

Use SQLAlchemy expressions, never a hand-built SQL string.

```python
from sqlalchemy import select

# By primary key — the session get is enough
thing = await db.get(Thing, thing_id)

# By a column
result = await db.execute(select(Thing).where(Thing.user_id == user_id))
things = result.scalars().all()

# One or none
result = await db.execute(select(Thing).where(Thing.name == name))
thing = result.scalar_one_or_none()
```

Rules:

- **Never build SQL with an f-string that contains a user value.** That is SQL injection. Bound parameters come from the expression, not from string formatting.
- **`scalar_one_or_none()`** for a lookup that may miss. **`scalars().all()`** for a list.
- **Queries for the shared `User`** go through `UserRepository`, not inline.

---

## Writing a schema

```python
from pydantic import BaseModel, EmailStr


class ThingRequest(BaseModel):
    name: str


class ThingResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
```

Rules:

- **A response schema that reads from an ORM object** sets `model_config = {"from_attributes": True}`.
- **Never include a secret column** in a response schema. `UserResponse` has no `hashed_password` — that is the point.
- **Use `EmailStr`** for email, so validation happens at the boundary.
- **Keep request and response schemas separate.** They diverge as soon as one field is server-generated.

---

## Logging

```python
from app.core.logging import get_logger

logger = get_logger(__name__)

logger.info("registered user %s", user.id)   # id, not email or password
```

Rules:

- **Use `get_logger(__name__)`.** Do not call `print` or the root logger directly.
- **Never log a secret**: no password, no token, no full `DATABASE_URL`, no API key.
- **Log identifiers, not personal data**, where an id is enough to trace the event.

---

## Errors

- Raise `HTTPException(status_code=..., detail="...")` at the boundary. The `detail` reaches the client — keep it accurate and free of internal detail.
- Do not catch an exception only to re-raise it unchanged. Let it propagate, or handle it meaningfully.
- An outbound call (a model provider, Stripe) gets a timeout and a failure branch. A provider error becomes a clear `HTTPException`, never an unhandled 500.
