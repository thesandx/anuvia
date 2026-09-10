"""Rule violations, and the route class that turns them into the client's shape.

The Playroom client parses `{ "code": ..., "message": ... }` at the top level of
an error body and shows `message` to the player verbatim. FastAPI's default
handler wraps a detail in `{"detail": ...}`, which the client cannot read, so
every route in this app runs through `RoomErrorRoute` below.

The route class lives here rather than in `app/main.py` on purpose: the
auto-loader registers routers, and a product must not have to edit shared
application setup to define its own error contract.
"""

from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.core.logging import get_logger

logger = get_logger(__name__)

RoomErrorCode = Literal[
    "room-not-found",
    "room-full",
    "room-locked",
    "not-host",
    "not-in-room",
    "wrong-phase",
    "name-taken",
    "not-your-turn",
    "number-taken",
    "invalid-number",
    "invalid-claim",
    "round-over",
]

#: Status per code, from the handover's error table. The client keys off the
#: code, not the status, but the status has to be right for proxies and probes.
ERROR_STATUS: dict[str, int] = {
    "room-not-found": 404,
    "room-full": 409,
    "room-locked": 409,
    "not-host": 403,
    "not-in-room": 403,
    "wrong-phase": 409,
    "name-taken": 409,
    "not-your-turn": 409,
    "number-taken": 409,
    "invalid-number": 422,
    "invalid-claim": 409,
    "round-over": 409,
}


class RoomError(Exception):
    """A rule violation the player is expected to see, not a crash.

    `message` is shown to the player verbatim, so write it as player-facing
    English rather than as a developer note.
    """

    def __init__(self, code: RoomErrorCode | str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status_code(self) -> int:
        return ERROR_STATUS.get(self.code, 409)

    def as_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content={"code": self.code, "message": self.message},
        )


class RateLimited(Exception):
    """Too many requests from one address. Rendered as 429 with a retry hint."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class RoomErrorRoute(APIRoute):
    """Renders `RoomError` in the shape the client parses.

    Anything else that escapes a handler becomes a generic 500 with fixed text.
    The cause goes to the log instead of the response, so a stack trace never
    reaches a player. `HTTPException` and `RequestValidationError` are re-raised
    so FastAPI's own handling of them is unchanged.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RoomError as error:
                return error.as_response()
            except RateLimited as error:
                return JSONResponse(
                    status_code=429,
                    content={"code": "rate-limited", "message": error.message},
                    headers={"Retry-After": str(error.retry_after)},
                )
            except (HTTPException, RequestValidationError):
                # FastAPI raises these while solving the request, inside the
                # call above. Swallowing them would turn every bad body into a
                # 500 and lose the field-level detail.
                raise
            except Exception:
                logger.exception("Unhandled error in %s %s", request.method, request.url.path)
                return JSONResponse(
                    status_code=500,
                    content={
                        "code": "server-error",
                        "message": "Something went wrong on our side. Try again in a moment.",
                    },
                )

        return handler
