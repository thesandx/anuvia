"""Room sessions. Everything that is true of a room whatever game it holds.

The layer split: this module owns the session (players, seats, phases, scores,
expiry, the audit log and the transaction). `engines.py` owns one game's moves.
Adding a game touches the engine, not this file.

**The client is not trusted.** It disables an out-of-turn button as a
convenience and expects the server to reject the request anyway. Every check
here runs regardless of what the client believes.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.playroom import models, serializers
from app.apps.playroom.broker import broker
from app.apps.playroom.engines import ActionResult, RejectedClaim, engine_for
from app.apps.playroom.errors import RoomError
from app.apps.playroom.keys import (
    create_player_token,
    create_room_key,
    hash_token,
    is_valid_room_key,
    normalise_name,
    normalise_room_key,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: How long a room stays reachable after its last change. On screen, so it is a
#: promise: "keys stop working two hours after the last round".
ROOM_TTL = timedelta(hours=settings.PLAYROOM_ROOM_TTL_HOURS)

#: How long a player has to take their turn. Past this, the turn is played for
#: them and passes on.
#:
#: Twenty seconds is long enough to read a board of 25 numbers and short enough
#: that a room does not stall on one person. It is a game-design number rather
#: than an operational one, so it is a constant here instead of a setting, and
#: the client never hard-codes it, it renders the seconds the server reports.
TURN_SECONDS = 20
TURN_LIMIT = timedelta(seconds=TURN_SECONDS)

#: A host who has not been seen for this long is replaced, provided somebody
#: else has been. Two seconds is the client's poll interval, so a minute of
#: silence is a closed tab rather than a slow network.
HOST_IDLE = timedelta(seconds=60)

#: `last_seen_at` is only written when it is at least this stale. Without it
#: every 2-second poll from every player would be a write.
SEEN_WRITE_INTERVAL = timedelta(seconds=15)

#: Attempts before key generation gives up. Six characters from a 32-character
#: alphabet is about 1.07 billion combinations, so a collision needs an
#: implausible number of live rooms.
KEY_ATTEMPTS = 5


def now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every write here is UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class PlayroomService:
    """One instance per request, holding that request's session."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- catalogue -----------------------------------------------------------

    async def list_games(self) -> list[models.Game]:
        rows = await self.db.execute(select(models.Game).order_by(models.Game.name))
        return list(rows.scalars().all())

    async def _playable_game(self, game_id: str) -> models.Game:
        game = await self.db.get(models.Game, game_id)
        if game is None:
            raise RoomError("room-not-found", "That game does not exist.")
        if game.status != "playable":
            raise RoomError("wrong-phase", "That game is not playable yet.")
        return game

    # --- loading and identity ------------------------------------------------

    async def load_room(self, key: str, *, for_update: bool = False) -> models.Room:
        """The active room for a key, or `room-not-found`.

        Expiry is applied lazily as well as by the sweeper. A room whose window
        has passed is flipped to `expired` here, which both hides it and frees
        its key at the moment somebody asks. The sweeper is what bounds how
        long an untouched room lingers, not what makes the promise true.
        """
        if not is_valid_room_key(key):
            raise RoomError("room-not-found", "That room no longer exists.")

        statement = select(models.Room).where(
            models.Room.key == normalise_room_key(key),
            models.Room.status == models.ROOM_STATUS_ACTIVE,
        )
        if for_update:
            statement = statement.with_for_update()

        room = (await self.db.execute(statement)).scalar_one_or_none()
        if room is None:
            raise RoomError("room-not-found", "That room no longer exists.")

        expires_at = _aware(room.expires_at)
        if expires_at is not None and expires_at <= now():
            room.status = models.ROOM_STATUS_EXPIRED
            await self._record(room, "room_expired", {})
            await self.db.commit()
            raise RoomError("room-not-found", "That room no longer exists.")

        return room

    async def resolve_player(self, room: models.Room, token: str | None) -> models.Player | None:
        """The player a bearer token names, if they are in this room.

        Returns None for a caller with no token: a spectator. A token that
        resolves to nobody in this room is an error, not a spectator: it is
        almost always a stale tab, and silently downgrading it would show that
        player a board-less room with no explanation.
        """
        if not token:
            return None

        player = (
            await self.db.execute(
                select(models.Player).where(
                    models.Player.room_id == room.id,
                    models.Player.token_hash == hash_token(token),
                    models.Player.left_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if player is None:
            raise RoomError("not-in-room", "You are no longer in this room.")

        seen = _aware(player.last_seen_at)
        if seen is None or now() - seen > SEEN_WRITE_INTERVAL:
            player.last_seen_at = now()

        return player

    async def players_of(self, room: models.Room) -> list[models.Player]:
        rows = await self.db.execute(
            select(models.Player)
            .where(models.Player.room_id == room.id, models.Player.left_at.is_(None))
            .order_by(models.Player.seat)
        )
        return list(rows.scalars().all())

    async def current_round(self, room: models.Room) -> models.Round | None:
        """The latest round of the room.

        `playroom_rounds.round_number` is a per-room sequence that never resets,
        so replaying a session keeps every earlier round for analytics instead
        of colliding with it. The round number the *client* sees is
        `playroom_rooms.round_number`, which does reset. The two are deliberately
        different numbers.
        """
        rows = await self.db.execute(
            select(models.Round)
            .where(models.Round.room_id == room.id)
            .order_by(models.Round.round_number.desc())
            .limit(1)
        )
        return rows.scalars().first()

    def require_host(self, room: models.Room, player: models.Player | None) -> models.Player:
        person = self.require_player(player)
        if room.host_player_id != person.id:
            raise RoomError("not-host", "Only the host can do that.")
        return person

    def require_player(self, player: models.Player | None) -> models.Player:
        if player is None:
            raise RoomError("not-in-room", "You are no longer in this room.")
        return player

    async def promote_host_if_needed(
        self, room: models.Room, players: list[models.Player]
    ) -> models.Player | None:
        """Replaces a host who has gone, so a room is never stranded.

        Only the host can start a round or advance past the results, and nothing
        else promotes a replacement. A lobby whose host closed the tab would
        never begin. The longest-present remaining player takes over, which is
        the lowest seat.

        Two triggers: the host is no longer an active player, or the host has
        not been seen for `HOST_IDLE` **while somebody else has**. The second
        condition matters, without it a room where everybody stepped away would
        churn its host on the first person to come back.
        """
        if not players:
            return None

        host = next((person for person in players if person.id == room.host_player_id), None)
        moment = now()

        if host is not None:
            host_seen = _aware(host.last_seen_at) or moment
            if moment - host_seen <= HOST_IDLE:
                return None
            others_active = any(
                person.id != host.id
                and moment - (_aware(person.last_seen_at) or moment) <= HOST_IDLE
                for person in players
            )
            if not others_active:
                return None

        successor = next((person for person in players if person.id != room.host_player_id), None)
        if successor is None:
            return None

        for person in players:
            person.is_host = person.id == successor.id
        room.host_player_id = successor.id
        self._touch(room)
        await self._record(
            room,
            "host_promoted",
            {"reason": "host_left" if host is None else "host_idle", "seat": successor.seat},
            player_id=successor.id,
        )
        logger.info("Promoted a new host in room %s", room.key)
        return successor

    # --- payload -------------------------------------------------------------

    async def payload_for(
        self,
        room: models.Room,
        viewer: models.Player | None,
        *,
        players: list[models.Player] | None = None,
        game_round: models.Round | None = None,
    ) -> dict:
        """The `Room` object, scoped to one caller.

        `players` and `game_round` are accepted so a caller that already holds
        them does not pay for them twice. Every read here is a network round
        trip to the database, and a move has to answer well inside the eight
        seconds the client waits before it gives up.
        """
        if players is None:
            players = await self.players_of(room)
        if game_round is None:
            game_round = await self.current_round(room)

        selections: list[int] = []
        boards: dict[UUID, list[int]] = {}
        last_pick: tuple[int, UUID] | None = None
        if game_round is not None:
            engine = engine_for(room.game_id)
            selections, boards, last_pick = await engine.load_view(self.db, game_round)

        return serializers.room_payload(
            room=room,
            players=players,
            game_round=game_round,
            selections=selections,
            boards=boards,
            last_pick=last_pick,
            viewer_id=viewer.id if viewer else None,
        )

    # --- writes --------------------------------------------------------------

    def _touch(self, room: models.Room) -> None:
        """One state change: bump the version, and push the expiry window out."""
        room.version += 1
        room.expires_at = now() + ROOM_TTL

    async def _record(
        self,
        room: models.Room | None,
        event_type: str,
        payload: dict,
        *,
        player_id: UUID | None = None,
        round_id: UUID | None = None,
    ) -> None:
        """Writes one audit row in the caller's transaction.

        Never in a background task. An event that can be lost is not an audit
        log, and several product questions have no other source of truth.
        """
        self.db.add(
            models.Event(
                room_id=room.id if room else None,
                round_id=round_id,
                player_id=player_id,
                game_id=room.game_id if room else None,
                type=event_type,
                payload=payload,
            )
        )

    async def _commit_and_publish(self, room: models.Room) -> None:
        """Commits, then wakes the room's open streams.

        The order is not negotiable: a listener woken before the commit would
        read the previous state and show it as the new one. The broker carries
        only the version, each stream re-reads the room scoped to its own
        viewer, because two viewers must not receive the same boards.
        """
        await self.db.commit()
        broker.publish(room.key, {"version": room.version})

    # --- room lifecycle ------------------------------------------------------

    async def create_room(
        self, game_id: str, room_settings: dict, host_name: str, host_color: str
    ) -> tuple[models.Room, models.Player, str]:
        game = await self._playable_game(game_id)

        max_players = int(room_settings["maxPlayers"])
        if not game.min_players <= max_players <= game.max_players:
            raise RoomError(
                "room-full",
                f"{game.name} takes {game.min_players} to {game.max_players} players.",
            )

        token = create_player_token()
        moment = now()

        # Insert and retry on a unique violation. Do not pre-check for
        # existence: the unique index is the check, and a pre-check is a race.
        for attempt in range(KEY_ATTEMPTS):
            room = models.Room(
                id=uuid4(),
                key=create_room_key(),
                game_id=game_id,
                phase=models.PHASE_LOBBY,
                round_number=1,
                settings=room_settings,
                status=models.ROOM_STATUS_ACTIVE,
                version=1,
                expires_at=moment + ROOM_TTL,
            )
            host = models.Player(
                id=uuid4(),
                room_id=room.id,
                display_name=host_name,
                avatar_color=host_color,
                score=0,
                is_host=True,
                seat=1,
                token_hash=hash_token(token),
                last_seen_at=moment,
            )
            room.host_player_id = host.id

            try:
                async with self.db.begin_nested():
                    # The room is flushed before the host, and the order is not
                    # cosmetic: `playroom_players.room_id` points at the room,
                    # and no `relationship()` joins these two mappers, so the
                    # unit of work has nothing to infer an order from and will
                    # happily insert the player first. PostgreSQL rejects that
                    # on the foreign key; SQLite does not check it at all,
                    # which is exactly why this has to be explicit.
                    self.db.add(room)
                    await self.db.flush()
                    self.db.add(host)
                    await self.db.flush()
            except IntegrityError:
                if attempt == KEY_ATTEMPTS - 1:
                    raise
                continue

            await self._record(
                room,
                "room_created",
                {"maxPlayers": max_players, "rounds": room_settings.get("rounds")},
                player_id=host.id,
            )
            await self._record(room, "player_joined", {"seat": 1}, player_id=host.id)
            await self._commit_and_publish(room)
            return room, host, token

        raise RoomError("room-not-found", "Could not open a room just now. Try again.")

    async def join_room(
        self, key: str, name: str, color: str
    ) -> tuple[models.Room, models.Player, str]:
        room = await self.load_room(key, for_update=True)
        players = await self.players_of(room)

        if room.phase == models.PHASE_FINISHED:
            raise RoomError("wrong-phase", "This session has already finished.")

        max_players = int(room.settings.get("maxPlayers", 8))
        if len(players) >= max_players:
            raise RoomError("room-full", f"This room is full. It holds {max_players} players.")

        if (
            room.settings.get("privacy") == "Locked after start"
            and room.phase != models.PHASE_LOBBY
        ):
            raise RoomError("room-locked", "The host locked this room after the game started.")

        name = normalise_name(name)
        if any((person.display_name or "").casefold() == name.casefold() for person in players):
            raise RoomError("name-taken", "Someone in this room already uses that nickname.")

        token = create_player_token()
        player = models.Player(
            id=uuid4(),
            room_id=room.id,
            display_name=name,
            avatar_color=color,
            score=0,
            is_host=False,
            seat=await self._next_seat(room),
            token_hash=hash_token(token),
            last_seen_at=now(),
        )

        # The unique index on (room_id, lower(display_name)) is the real check.
        # Two players typing the same nickname at the same instant both pass the
        # scan above; exactly one passes this.
        try:
            async with self.db.begin_nested():
                self.db.add(player)
                await self.db.flush()
        except IntegrityError as error:
            raise RoomError(
                "name-taken", "Someone in this room already uses that nickname."
            ) from error

        # Joining mid-round is allowed while the room is unlocked, so the new
        # player is dealt a board and appended to the turn order rather than
        # left without one. Appending does not move the running turn.
        game_round = await self.current_round(room)
        if (
            room.phase == models.PHASE_PLAYING
            and game_round is not None
            and game_round.status == models.ROUND_PLAYING
        ):
            engine = engine_for(room.game_id)
            await engine.deal_one(self.db, game_round, player.id)
            game_round.turn_order = [*(game_round.turn_order or []), str(player.id)]

        self._touch(room)
        await self._record(room, "player_joined", {"seat": player.seat}, player_id=player.id)
        await self._commit_and_publish(room)
        return room, player, token

    async def _next_seat(self, room: models.Room) -> int:
        """Seats never repeat, including seats a departed player used.

        Reusing a seat would break the unique index, and it would also make a
        seat-based fairness measurement meaningless.
        """
        highest = (
            await self.db.execute(
                select(func.max(models.Player.seat)).where(models.Player.room_id == room.id)
            )
        ).scalar()
        return int(highest or 0) + 1

    async def remove_player(
        self, room: models.Room, caller: models.Player, target_id: UUID
    ) -> None:
        """Host removes somebody, or a player leaves.

        Mid-round the target also leaves the turn order. The index is rebased so
        play continues with the player it was waiting on, rather than silently
        skipping whoever followed the one who left.
        """
        if target_id == room.host_player_id:
            raise RoomError("not-host", "The host cannot be removed.")
        if caller.id != target_id:
            self.require_host(room, caller)

        target = (
            await self.db.execute(
                select(models.Player).where(
                    models.Player.id == target_id,
                    models.Player.room_id == room.id,
                    models.Player.left_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if target is None:
            raise RoomError("not-in-room", "That player is not in this room.")

        target.left_at = now()

        game_round = await self.current_round(room)
        if game_round is not None and game_round.status == models.ROUND_PLAYING:
            order: list[str] = list(game_round.turn_order or [])
            if order:
                active_id = order[game_round.current_turn_index % len(order)]
                remaining = [entry for entry in order if entry != str(target_id)]
                if not remaining:
                    game_round.current_turn_index = 0
                elif active_id == str(target_id):
                    # The slot they vacated is now the next player along.
                    game_round.current_turn_index = game_round.current_turn_index % len(remaining)
                else:
                    game_round.current_turn_index = remaining.index(active_id)
                game_round.turn_order = remaining
                if active_id == str(target_id) and remaining:
                    # The turn just changed hands. Whoever inherits it should
                    # not inherit the departing player's remaining seconds.
                    self._restart_turn_clock(game_round)

        self._touch(room)
        await self._record(
            room,
            "player_removed" if caller.id != target_id else "player_left",
            {"seat": target.seat},
            player_id=target_id,
            round_id=game_round.id if game_round else None,
        )
        await self.promote_host_if_needed(room, await self.players_of(room))
        await self._commit_and_publish(room)

    # --- rounds --------------------------------------------------------------

    async def start_round(self, room: models.Room, caller: models.Player) -> None:
        self.require_host(room, caller)
        if room.phase == models.PHASE_PLAYING:
            raise RoomError("wrong-phase", "The round already started.")
        if room.phase != models.PHASE_LOBBY:
            raise RoomError("wrong-phase", "The round is not finished.")

        await self._deal_new_round(room)
        await self._commit_and_publish(room)

    async def _deal_new_round(self, room: models.Room) -> models.Round:
        """Deals a board to every player and puts the room into play.

        Turn order is fixed here, in seat order, and does not change for the
        rest of the round except when a player leaves.
        """
        players = await self.players_of(room)
        if not players:
            raise RoomError("wrong-phase", "There is nobody in this room.")

        previous = await self.current_round(room)
        sequence = (previous.round_number + 1) if previous else 1

        game_round = models.Round(
            id=uuid4(),
            room_id=room.id,
            game_id=room.game_id,
            round_number=sequence,
            status=models.ROUND_PLAYING,
            turn_order=[str(person.id) for person in players],
            current_turn_index=0,
            state={},
        )
        self.db.add(game_round)
        await self.db.flush()
        self._restart_turn_clock(game_round)

        engine = engine_for(room.game_id)
        await engine.deal(self.db, game_round, [person.id for person in players])

        room.phase = models.PHASE_PLAYING
        self._touch(room)
        await self._record(
            room,
            "round_started",
            {"players": len(players), "round": room.round_number},
            round_id=game_round.id,
        )
        return game_round

    async def apply_action(
        self,
        room: models.Room,
        caller: models.Player,
        action_type: str,
        payload: dict,
        idempotency_key: str | None = None,
    ) -> dict:
        """Applies one in-game move and returns the room the caller should see.

        The round row is locked first. `FOR UPDATE` is what makes the turn check
        correct under concurrency: two players sending at the same instant are
        serialised, so the second reads the first one's advanced turn index.
        The unique constraints inside the engine are the backstop for when the
        turn check is ever wrong.

        The whole move, the round lock, the idempotency lookup, the game's own
        writes, the events, and the record of what this key produced, happens
        against **one** load of the round and commits **once**. Both properties
        are load-bearing:

        - Every extra read is a network round trip to the database, and a move
          has to answer well inside the eight seconds the client waits.
        - Storing the idempotency record in a second transaction would leave a
          window where the move happened and the record did not. A retry landing
          in that window takes the number twice, which is the exact failure the
          record exists to prevent.
        """
        if room.phase == models.PHASE_ROUND_RESULTS:
            # A move that arrives after the round was decided is `round-over`,
            # not `wrong-phase`. The distinction is the message the player sees:
            # "someone already called bingo" against "no round is in progress".
            raise RoomError("round-over", "This round is already over.")
        if room.phase != models.PHASE_PLAYING:
            raise RoomError("wrong-phase", "No round is in progress.")

        game_round = await self._locked_current_round(room)
        if game_round is None:
            raise RoomError("wrong-phase", "No round is in progress.")

        # A replay of a move this player already made. Checked after the lock so
        # it reads the same round the move would be applied to.
        replayed = await self._replayed_response(game_round, idempotency_key, caller)
        if replayed is not None:
            await self.db.commit()
            return replayed

        players = await self.players_of(room)
        engine = engine_for(room.game_id)
        try:
            result = await engine.apply_action(
                self.db, game_round, caller, players, action_type, payload
            )
        except RejectedClaim as rejected:
            # A rejected claim leaves the round running, but it is still a
            # thing that happened. A rejection at four lines is a player who
            # misread the rule; at one, a player who did not know there was
            # one. Those want different fixes, so both are recorded.
            await self._record(
                room,
                "bingo_rejected",
                {"lines": rejected.lines_held},
                player_id=caller.id,
                round_id=game_round.id,
            )
            await self.db.commit()
            raise

        await self._apply_result(room, game_round, players, result)

        # Built before the commit, because the record of what this key produced
        # has to be written in the same transaction as the move it describes.
        result_payload = await self.payload_for(
            room, caller, players=players, game_round=game_round
        )
        if idempotency_key:
            self.db.add(
                models.IdempotencyKey(
                    round_id=game_round.id,
                    key=_scoped_key(caller, idempotency_key),
                    player_id=caller.id,
                    response=result_payload,
                )
            )

        await self._commit_and_publish(room)
        return result_payload

    def _restart_turn_clock(self, game_round: models.Round) -> None:
        """Gives whoever is now on turn a full slice of time.

        Called wherever the turn changes hands, a deal, a selection, a player
        leaving, rather than only after a move, because a player who inherits
        the turn from somebody who left should not inherit their remaining two
        seconds either.
        """
        game_round.turn_expires_at = now() + TURN_LIMIT

    async def _apply_result(
        self,
        room: models.Room,
        game_round: models.Round,
        players: list[models.Player],
        result: ActionResult,
    ) -> None:
        """The half of a move that is the same whoever, or whatever, made it.

        Shared by a player's action and by a turn played out on the clock, so
        the two cannot drift: an automatic move ends a round, scores it and
        records its events exactly as a deliberate one does.
        """
        if result.ended:
            self._finish_round(room, game_round, players, result)
            game_round.turn_expires_at = None
        else:
            self._restart_turn_clock(game_round)

        self._touch(room)
        for event in result.events:
            await self._record(
                room,
                event.type,
                event.payload,
                player_id=event.player_id,
                round_id=game_round.id,
            )

    async def enforce_turn_deadline(self, room: models.Room) -> bool:
        """Plays the turn of anybody who has run out of time.

        This is what makes the deadline real when the player it applies to has
        closed their tab: their own client cannot fire it, so it is driven by
        whoever else is looking. Every other client polls every two seconds, so
        somebody notices within that.

        At most one turn is settled per call. If a whole room walks away, the
        alternative is that the first person to come back watches the board
        play itself out: bounded, but startling. One at a time means play
        resumes at the pace of people actually being there.
        """
        if room.phase != models.PHASE_PLAYING:
            return False

        game_round = await self.current_round(room)
        if game_round is None or game_round.status != models.ROUND_PLAYING:
            return False
        if not self._turn_is_due(game_round):
            return False

        # Re-read under a lock and re-check: every client in the room is polling,
        # so several of them see the same expired turn at the same moment and
        # only one may act on it.
        locked = await self._locked_current_round(room)
        if locked is None or locked.status != models.ROUND_PLAYING:
            return False
        if not self._turn_is_due(locked):
            return False

        players = await self.players_of(room)
        order: list[str] = list(locked.turn_order or [])
        if not players or not order:
            return False

        on_turn_id = order[locked.current_turn_index % len(order)]
        player = next((person for person in players if str(person.id) == on_turn_id), None)
        if player is None:
            # The player on turn has left and removal already rebased the index;
            # nothing to play for them.
            return False

        engine = engine_for(room.game_id)
        result = await engine.auto_move(self.db, locked, player, players)
        if result is None:
            # Nothing left to play. The round ends on its own rules, not here.
            return False

        await self._apply_result(room, locked, players, result)
        await self._commit_and_publish(room)
        logger.info("Played a timed-out turn in room %s", room.key)
        return True

    @staticmethod
    def _turn_is_due(game_round: models.Round) -> bool:
        """True when the current turn has run past its deadline.

        A round with no deadline is never due. Rounds that were already running
        when the deadline shipped have none, and they finish under the old rules
        rather than having every turn expire at once.
        """
        deadline = _aware(game_round.turn_expires_at)
        return deadline is not None and deadline <= now()

    async def _locked_current_round(self, room: models.Room) -> models.Round | None:
        """The current round, locked for the length of the transaction.

        `with_for_update()` renders nothing on SQLite, which has one writer
        anyway. On PostgreSQL it is what serialises two players taking a number
        at the same moment.
        """
        rows = await self.db.execute(
            select(models.Round)
            .where(models.Round.room_id == room.id)
            .order_by(models.Round.round_number.desc())
            .limit(1)
            .with_for_update()
        )
        return rows.scalars().first()

    def _finish_round(
        self,
        room: models.Room,
        game_round: models.Round,
        players: list[models.Player],
        result: ActionResult,
    ) -> None:
        """Applies the awards and moves the room to the results screen.

        Scores accumulate across rounds within a session. The rows are stored on
        the round so the results screen reads the same numbers on every poll,
        rather than being recomputed from a board that no longer exists after
        retention has run.
        """
        rows = []
        for person in players:
            note, gain = result.awards.get(person.id, ("No line", 0))
            person.score += gain
            name = person.display_name or serializers.ANONYMOUS_NAME
            rows.append(
                {
                    "playerId": str(person.id),
                    "name": name,
                    "initial": name[0].upper() if name else "",
                    "color": person.avatar_color,
                    "note": note,
                    "gain": gain,
                }
            )

        game_round.state = {**(game_round.state or {}), "results": rows}
        room.phase = models.PHASE_ROUND_RESULTS

    async def next_round(self, room: models.Room, caller: models.Player) -> None:
        """Deals the next round, or ends the session at the configured count."""
        self.require_host(room, caller)
        if room.phase != models.PHASE_ROUND_RESULTS:
            raise RoomError("wrong-phase", "The round is not finished.")

        total = int(room.settings.get("rounds", 1))
        if room.round_number >= total:
            room.phase = models.PHASE_FINISHED
            self._touch(room)
            await self._record(room, "session_ended", {"reason": "rounds_complete"})
            await self._commit_and_publish(room)
            return

        room.round_number += 1
        await self._deal_new_round(room)
        await self._record(room, "round_advanced", {"round": room.round_number})
        await self._commit_and_publish(room)

    # --- host controls -------------------------------------------------------

    async def lock_room(self, room: models.Room, caller: models.Player) -> None:
        self.require_host(room, caller)
        room.settings = {**room.settings, "privacy": "Locked after start"}
        self._touch(room)
        await self._record(room, "room_locked", {})
        await self._commit_and_publish(room)

    async def end_session(self, room: models.Room, caller: models.Player) -> None:
        self.require_host(room, caller)
        room.phase = models.PHASE_FINISHED
        game_round = await self.current_round(room)
        if game_round is not None and game_round.status == models.ROUND_PLAYING:
            game_round.status = models.ROUND_ABANDONED
            game_round.ended_at = now()
            game_round.turn_expires_at = None
        self._touch(room)
        await self._record(room, "session_ended", {"reason": "host_ended"})
        await self._commit_and_publish(room)

    async def replay_session(self, room: models.Room, caller: models.Player) -> None:
        """Starts the session over in the same room, keeping the players.

        Scores go to zero and the displayed round goes back to one. The round
        *rows* are kept and their sequence carries on, so a replayed session
        does not overwrite the history of the one before it.
        """
        self.require_host(room, caller)
        room.phase = models.PHASE_LOBBY
        room.round_number = 1
        await self.db.execute(
            update(models.Player)
            .where(models.Player.room_id == room.id, models.Player.left_at.is_(None))
            .values(score=0)
        )
        self._touch(room)
        await self._record(room, "session_replayed", {})
        await self._commit_and_publish(room)

    # --- idempotency ---------------------------------------------------------

    async def _replayed_response(
        self, game_round: models.Round, key: str | None, player: models.Player
    ) -> dict | None:
        """The room a previous attempt with this key produced, if any.

        The stored key is scoped to the player, which does two things at once:
        a snapshot can never be replayed to somebody else, so it cannot hand
        one player another player's board, and two players who happen to send
        the same key string in one round do not collide on the primary key.

        The snapshot is the state at the time of the original move; the client's
        poll brings it up to date within two seconds, which is the same
        freshness every other screen has.
        """
        if not key:
            return None
        record = await self.db.get(models.IdempotencyKey, (game_round.id, _scoped_key(player, key)))
        return None if record is None else record.response

    # --- retention -----------------------------------------------------------

    async def sweep(self) -> dict[str, int]:
        """Expiry and anonymisation. Safe to run repeatedly.

        Three steps, in the order the handover sets out:

        1. Flip past-window rooms to `expired`. The room leaves key lookup at
           once, and the key becomes reusable.
        2. After the retention window, drop the boards, the selections and the
           nicknames. Keep the rows and the ids.
        3. Keep events and rounds. They carry no name after step 2.

        Anonymising rather than deleting keeps every aggregate correct while
        honouring the promise that nothing is stored against a player.
        """
        moment = now()

        expired = await self.db.execute(
            update(models.Room)
            .where(
                models.Room.status == models.ROOM_STATUS_ACTIVE,
                models.Room.expires_at < moment,
            )
            .values(status=models.ROOM_STATUS_EXPIRED)
        )

        cutoff = moment - timedelta(days=settings.PLAYROOM_RETENTION_DAYS)
        stale_rooms = (
            (
                await self.db.execute(
                    select(models.Room.id).where(
                        models.Room.status == models.ROOM_STATUS_EXPIRED,
                        models.Room.expires_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )

        anonymised = 0
        purged = 0
        if stale_rooms:
            stale_rounds = (
                (
                    await self.db.execute(
                        select(models.Round.id).where(models.Round.room_id.in_(stale_rooms))
                    )
                )
                .scalars()
                .all()
            )

            if stale_rounds:
                await self.db.execute(
                    delete(models.BingoSelection).where(
                        models.BingoSelection.round_id.in_(stale_rounds)
                    )
                )
                # The board row survives with its cells dropped, so a count of
                # boards dealt stays correct.
                result = await self.db.execute(
                    update(models.BingoBoard)
                    .where(models.BingoBoard.round_id.in_(stale_rounds))
                    .values(cells=None)
                )
                purged = result.rowcount or 0
                await self.db.execute(
                    delete(models.IdempotencyKey).where(
                        models.IdempotencyKey.round_id.in_(stale_rounds)
                    )
                )

            result = await self.db.execute(
                update(models.Player)
                .where(
                    models.Player.room_id.in_(stale_rooms),
                    models.Player.display_name.is_not(None),
                )
                .values(display_name=None)
            )
            anonymised = result.rowcount or 0

        await self.db.commit()
        return {
            "expiredRooms": expired.rowcount or 0,
            "anonymisedPlayers": anonymised,
            "purgedRounds": purged,
        }


async def stream_room(
    session_factory,
    key: str,
    token: str | None,
    heartbeat_seconds: int = 20,
):
    """Yields Server-Sent Events for one room, one frame per change.

    Each connection re-reads and re-scopes the room itself. The broker carries
    only a version number, because two viewers of the same room must not receive
    the same boards.

    A heartbeat goes out on a quiet room. Proxies and Cloud Run close an idle
    connection, and a comment frame is the cheapest thing that stops them.
    """
    # The first read decides whether there is anything to stream. It has to be
    # reported as a frame rather than raised: the response status is already
    # sent by the time this generator runs, so an exception here would look to
    # the client like a dropped connection and it would reconnect for ever.
    async with session_factory() as db:
        service = PlayroomService(db)
        try:
            room = await service.load_room(key)
            viewer = await service.resolve_player(room, token)
            payload = await service.payload_for(room, viewer)
            await db.commit()
        except RoomError as error:
            yield _sse("closed", {"code": error.code, "message": error.message})
            return

    yield _sse("room", payload)

    async with broker.subscribe(normalise_room_key(key)) as queue:
        while True:
            try:
                await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue

            async with session_factory() as db:
                service = PlayroomService(db)
                try:
                    room = await service.load_room(key)
                    viewer = await service.resolve_player(room, token)
                    await service.enforce_turn_deadline(room)
                    payload = await service.payload_for(room, viewer)
                    await db.commit()
                except RoomError as error:
                    yield _sse("closed", {"code": error.code, "message": error.message})
                    return

            yield _sse("room", payload)


def _sse(event: str, data: object) -> str:
    """One SSE frame. The JSON is compact so a frame stays on one line."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _scoped_key(player: models.Player, key: str) -> str:
    """Namespaces an idempotency key to the player who sent it.

    The client's keys already carry the player id, but the header is whatever a
    caller chooses to send. Scoping it here is what makes that untrusted.
    """
    return f"{player.id}:{key}"[:128]


def bearer_token(header: str | None) -> str | None:
    """The credential out of an `Authorization: Bearer <token>` header."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def as_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise RoomError("not-in-room", "That player is not in this room.") from error


__all__ = [
    "PlayroomService",
    "as_uuid",
    "bearer_token",
    "stream_room",
]
