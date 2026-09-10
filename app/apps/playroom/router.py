"""Playroom HTTP routes.

Mounted at `/games`, so the full path of a room read is
`/games/v1/rooms/{key}` and the client's `NEXT_PUBLIC_PLAYROOM_API_URL` is
`https://api.sandeep.app/games/v1`.

Two things are true of every route here and are worth stating once:

- **Every mutating endpoint returns the `Room` scoped to the caller**, exactly
  as the read would for that player. The client applies the returned room
  directly and skips a re-fetch, which is why none of these return `204`.
- **The router validates and delegates.** Every rule lives in `service.py` and
  `engines.py`. A route that started making a decision would be a route the
  Server-Sent Events stream does not make the same way.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.playroom import ratelimit
from app.apps.playroom.errors import RoomErrorRoute
from app.apps.playroom.schemas import (
    ActionRequest,
    CreateRoomRequest,
    GameSummary,
    JoinedRoom,
    JoinRoomRequest,
    Room,
    SweepResult,
)
from app.apps.playroom.service import PlayroomService, as_uuid, bearer_token, stream_room
from app.core.config import settings
from app.core.database import SessionLocal, get_db

router = APIRouter(route_class=RoomErrorRoute)
PREFIX = "/games"
TAGS = ["playroom"]

Db = Annotated[AsyncSession, Depends(get_db)]
#: Bearer token, or None. Not `HTTPBearer`: several routes are legitimately
#: anonymous, and `HTTPBearer` would 403 a spectator before the handler runs.
Auth = Annotated[str | None, Header(alias="Authorization")]
Idempotency = Annotated[str | None, Header(alias="Idempotency-Key")]


def client_bucket(request: Request) -> str:
    """A salted hash of the caller's address, for rate limiting only.

    Cloud Run puts the client address first in `X-Forwarded-For`. The address is
    never stored: `bucket_key` hashes it with a per-process salt.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    address = forwarded.split(",")[0].strip() or (request.client.host if request.client else None)
    return ratelimit.bucket_key(address)


@router.get("/v1/games", response_model=list[GameSummary])
async def list_games(db: Db) -> list[GameSummary]:
    """The catalogue. Public, and the one route with nothing behind it."""
    games = await PlayroomService(db).list_games()
    return [
        GameSummary(
            id=game.id,
            name=game.name,
            status=game.status,
            min_players=game.min_players,
            max_players=game.max_players,
        )
        for game in games
    ]


@router.post("/v1/rooms", response_model=JoinedRoom, status_code=status.HTTP_201_CREATED)
async def create_room(body: CreateRoomRequest, request: Request, db: Db) -> JoinedRoom:
    """Opens a room and mints the host's identity.

    `playerToken` is returned here and never again. It is not in the room
    payload, and a player who loses it rejoins as somebody new.
    """
    ratelimit.create_limiter.check(client_bucket(request))

    service = PlayroomService(db)
    room, host, token = await service.create_room(
        game_id=body.game_id,
        room_settings=body.settings.model_dump(by_alias=True),
        host_name=body.host_name,
        host_color=body.host_color,
    )
    payload = await service.payload_for(room, host)
    return JoinedRoom(room=Room(**payload), player_id=str(host.id), player_token=token)


@router.get("/v1/rooms/{key}", response_model=Room)
async def get_room(
    key: str,
    request: Request,
    response: Response,
    db: Db,
    authorization: Auth = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Room | Response:
    """Reads a room, scoped to the caller.

    Auth is optional but it changes the answer. A request with a valid token
    gets that player's board in `bingo.cards`; a request without one is a
    spectator and gets no board at all. Both are correct.

    Every client polls this every two seconds whether anything changed or not,
    so an unchanged room answers `304` and never touches the players, the round
    or the boards. A lobby of twenty people waiting for the host then costs two
    queries per poll instead of five.

    The validator carries the viewer as well as the version, because two callers
    of the same room version legitimately receive different boards.
    """
    service = PlayroomService(db)
    room = await service.load_room(key)
    viewer = await service.resolve_player(room, bearer_token(authorization))

    # The read is what drives the turn clock. A player who has closed their tab
    # cannot time their own turn out, so it is settled by whoever else is
    # looking — and everybody in the room reads this every two seconds.
    #
    # Deliberately not enforced on the action route. A tap sent at nineteen
    # seconds that arrives at twenty-one is a player who did take their turn,
    # and replacing their number with a random one because of half a second of
    # network would be a worse bug than the one this fixes.
    await service.enforce_turn_deadline(room)

    # Promotion has to run before the validator is computed: it bumps the
    # version, and a 304 issued after it would hide a new host from the room.
    await service.promote_host_if_needed(room, await service.players_of(room))

    etag = _etag(room.version, viewer.id if viewer else None)
    headers = {
        "ETag": etag,
        # `no-cache` — not `no-store`. The browser may keep this response, but
        # must revalidate before reusing it, which is exactly the poll's
        # semantics and lets the browser send `If-None-Match` on its own. With
        # `no-store` it may not keep anything, so it has nothing to revalidate
        # and a 304 becomes a network error.
        "Cache-Control": "private, no-cache",
        # Two players read the same URL with different tokens and must get
        # different boards. Without this the browser cache would serve one
        # player's room to the other.
        "Vary": "Authorization",
    }

    if if_none_match is not None and etag in _tags(if_none_match):
        # Commit anyway: `last_seen_at` may have moved, and that is what keeps a
        # quiet host from being replaced.
        await db.commit()
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    payload = await service.payload_for(room, viewer)
    await db.commit()

    response.headers.update(headers)
    return Room(**payload)


def _tags(header: str) -> set[str]:
    """The validators in an `If-None-Match` header. It may carry a list."""
    return {tag.strip() for tag in header.split(",") if tag.strip()}


@router.post(
    "/v1/rooms/{key}/players",
    response_model=JoinedRoom,
    status_code=status.HTTP_201_CREATED,
)
async def join_room(key: str, body: JoinRoomRequest, request: Request, db: Db) -> JoinedRoom:
    """Joins an existing room and mints that player's identity."""
    ratelimit.join_limiter.check(client_bucket(request))

    service = PlayroomService(db)
    room, player, token = await service.join_room(key, body.name, body.color)
    payload = await service.payload_for(room, player)
    return JoinedRoom(room=Room(**payload), player_id=str(player.id), player_token=token)


@router.delete("/v1/rooms/{key}/players/{player_id}", response_model=Room)
async def remove_player(key: str, player_id: str, db: Db, authorization: Auth = None) -> Room:
    """The host removes somebody, or a player removes themselves."""
    service = PlayroomService(db)
    room = await service.load_room(key, for_update=True)
    caller = service.require_player(await service.resolve_player(room, bearer_token(authorization)))
    await service.remove_player(room, caller, as_uuid(player_id))
    return await _scoped(service, room, caller)


@router.post("/v1/rooms/{key}/rounds", response_model=Room)
async def start_round(key: str, db: Db, authorization: Auth = None) -> Room:
    """Deals a board to every player and starts the round. Host only."""
    service = PlayroomService(db)
    room = await service.load_room(key, for_update=True)
    caller = service.require_player(await service.resolve_player(room, bearer_token(authorization)))
    await service.start_round(room, caller)
    return await _scoped(service, room, caller)


@router.post("/v1/rooms/{key}/rounds/current/actions", response_model=Room)
async def apply_action(
    key: str,
    body: ActionRequest,
    request: Request,
    db: Db,
    authorization: Auth = None,
    idempotency_key: Idempotency = None,
) -> Room:
    """One in-game move, in the envelope every game shares.

    `{"type": "select_number", "payload": {"value": 17}}` and
    `{"type": "claim_bingo", "payload": {}}` are Bingo's two. Dispatch is on
    `(game_id, type)`, which is what lets a new game arrive without a new route.

    An `Idempotency-Key` header makes a network retry safe. Without it a
    retried "take 17" comes back as `number-taken` and looks like a bug to the
    player.
    """
    ratelimit.action_limiter.check(client_bucket(request))

    service = PlayroomService(db)
    room = await service.load_room(key, for_update=True)
    caller = service.require_player(await service.resolve_player(room, bearer_token(authorization)))

    payload = await service.apply_action(
        room, caller, body.type, body.payload, idempotency_key=idempotency_key
    )
    return Room(**payload)


@router.post("/v1/rooms/{key}/rounds/advance", response_model=Room)
async def next_round(key: str, db: Db, authorization: Auth = None) -> Room:
    """Deals the next round, or ends the session at the configured count."""
    service = PlayroomService(db)
    room = await service.load_room(key, for_update=True)
    caller = service.require_player(await service.resolve_player(room, bearer_token(authorization)))
    await service.next_round(room, caller)
    return await _scoped(service, room, caller)


@router.post("/v1/rooms/{key}/lock", response_model=Room)
async def lock_room(key: str, db: Db, authorization: Auth = None) -> Room:
    """Closes the room to new players. Host only."""
    service = PlayroomService(db)
    room = await service.load_room(key, for_update=True)
    caller = service.require_player(await service.resolve_player(room, bearer_token(authorization)))
    await service.lock_room(room, caller)
    return await _scoped(service, room, caller)


@router.post("/v1/rooms/{key}/end", response_model=Room)
async def end_session(key: str, db: Db, authorization: Auth = None) -> Room:
    """Ends the session for everyone and shows the final scoreboard."""
    service = PlayroomService(db)
    room = await service.load_room(key, for_update=True)
    caller = service.require_player(await service.resolve_player(room, bearer_token(authorization)))
    await service.end_session(room, caller)
    return await _scoped(service, room, caller)


@router.post("/v1/rooms/{key}/replay", response_model=Room)
async def replay_session(key: str, db: Db, authorization: Auth = None) -> Room:
    """Starts over in the same room, keeping the players and zeroing scores."""
    service = PlayroomService(db)
    room = await service.load_room(key, for_update=True)
    caller = service.require_player(await service.resolve_player(room, bearer_token(authorization)))
    await service.replay_session(room, caller)
    return await _scoped(service, room, caller)


@router.get("/v1/rooms/{key}/stream")
async def stream(key: str, authorization: Auth = None) -> StreamingResponse:
    """Server-Sent Events: the full `Room` on every change.

    One-way, so it needs no WebSocket infrastructure, survives proxies and
    reconnects on its own. The 2-second poll in the client stays as the
    fallback — see `broker.py` for why that matters on more than one instance.

    The credential is a bearer header, not a query parameter. A token in a URL
    ends up in access logs and browser history, so the client reads this stream
    with `fetch` rather than `EventSource`, which cannot set headers.

    This route opens its own sessions rather than taking the request-scoped one:
    the connection outlives the handler, and `get_db` closes its session when
    the handler returns.
    """
    token = bearer_token(authorization)
    return StreamingResponse(
        stream_room(SessionLocal, key, token),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Tells nginx-style proxies not to buffer, which would hold every
            # frame until the response ended — that is, forever.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/v1/maintenance/sweep", response_model=SweepResult)
async def sweep(db: Db, authorization: Auth = None) -> SweepResult:
    """Expiry and anonymisation. For a scheduler, not for a player.

    Point Cloud Scheduler or `pg_cron` at it every few minutes. It is
    idempotent, so a double fire is harmless.
    """
    expected = settings.PLAYROOM_MAINTENANCE_TOKEN
    supplied = bearer_token(authorization)
    if not expected or supplied != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authorised")

    counts = await PlayroomService(db).sweep()
    for limiter in ratelimit.ALL_LIMITERS:
        limiter.prune()
    return SweepResult(
        expired_rooms=counts["expiredRooms"],
        anonymised_players=counts["anonymisedPlayers"],
        purged_rounds=counts["purgedRounds"],
    )


async def _scoped(service: PlayroomService, room, caller) -> Room:
    """The room the mutation produced, narrowed to the caller who made it."""
    payload = await service.payload_for(room, caller)
    await service.db.commit()
    return Room(**payload)


def _etag(version: int, viewer_id: UUID | None) -> str:
    """Weak validator. The viewer is part of it because two callers of the same
    room version legitimately receive different boards."""
    return f'W/"{version}-{viewer_id or "anon"}"'
